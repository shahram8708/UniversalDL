import json
import uuid
from datetime import datetime

import redis
from pydantic import ValidationError
from sqlalchemy import func

from flask import current_app, g, jsonify, request

from app.api import api_bp
from app.api.auth import get_api_rate_limit_key, require_api_key
from app.api.serializers import (
    ActionResponse,
    AnalyzeRequest,
    BatchRequest,
    BatchStatusResponse,
    DownloadRequest,
    FormatInfo,
    HistoryItemResponse,
    HistoryResponse,
    JobStatusResponse,
    PlatformInfo,
    SubscriptionCreateRequest,
    SubscriptionResponse,
    job_to_response_dict,
    make_error_response,
)
from app.dashboard.routes import (
    _infer_subscription_platform,
    _is_supported_subscription_url,
    check_batch_limit,
    check_download_limit,
    check_quality_limit,
    get_channel_metadata,
)
from app.downloader.tasks import analyze_url_task, download_media_task
from app.extensions import db, limiter
from app.models.batch_queue import BatchQueue
from app.models.download_job import DownloadJob
from app.models.extractor import PlatformExtractor
from app.models.subscription import Subscription
from app.services import url_parser


def _load_request_json_object():
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data, None

    raw = (request.get_data(cache=True, as_text=True) or "").strip()
    if not raw:
        return {}, None

    candidates = [raw]
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        candidates.append(raw[1:-1].strip())

    expanded_candidates = []
    for candidate in candidates:
        expanded_candidates.append(candidate)
        if '\\"' in candidate:
            expanded_candidates.append(candidate.replace('\\"', '"'))

    seen = set()
    for candidate in expanded_candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)

        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue

        if isinstance(parsed, str):
            nested = parsed.strip()
            if nested:
                try:
                    parsed = json.loads(nested)
                except json.JSONDecodeError:
                    pass

        if isinstance(parsed, dict):
            return parsed, None

    return None, "Request body must be a valid JSON object"


@api_bp.route("/analyze", methods=["POST"])
@limiter.limit("60 per minute", key_func=get_api_rate_limit_key)
@require_api_key
def api_analyze():
    data, parse_error = _load_request_json_object()
    if parse_error:
        return make_error_response("invalid_json", parse_error, 400)

    try:
        req = AnalyzeRequest(**data)
    except ValidationError as exc:
        return make_error_response("validation_error", str(exc.errors()), 422)

    allowed, message = check_download_limit(g.api_user)
    if not allowed:
        return make_error_response("limit_exceeded", message, 429)

    job = DownloadJob(user_id=g.api_user.id, url=req.url, status="queued")
    db.session.add(job)
    db.session.commit()

    from celery import chain

    task = chain(analyze_url_task.s(str(job.id)), download_media_task.si(str(job.id))).delay()
    job.celery_task_id = task.id
    db.session.commit()

    response = ActionResponse(
        success=True,
        status="analyzing",
        job_id=str(job.id),
        message="Analysis started. Poll /api/v1/jobs/{job_id} for status.",
    )
    return jsonify(response.model_dump()), 202


@api_bp.route("/download", methods=["POST"])
@limiter.limit("200 per hour", key_func=get_api_rate_limit_key)
@require_api_key
def api_download():
    data, parse_error = _load_request_json_object()
    if parse_error:
        return make_error_response("invalid_json", parse_error, 400)

    try:
        req = DownloadRequest(**data)
    except ValidationError as exc:
        return make_error_response("validation_error", str(exc.errors()), 422)

    allowed, message = check_download_limit(g.api_user)
    if not allowed:
        return make_error_response("limit_exceeded", message, 429)

    allowed, message = check_quality_limit(g.api_user, req.quality)
    if not allowed:
        return make_error_response("quality_limit", message, 403)

    job = DownloadJob(
        user_id=g.api_user.id,
        url=req.url,
        selected_quality=req.quality,
        selected_format=req.format.value,
        subtitle_language=req.subtitle_language,
        subtitle_embed=bool(req.subtitle_embed),
        status="queued",
    )
    db.session.add(job)
    db.session.commit()

    task = analyze_url_task.delay(str(job.id))
    job.celery_task_id = task.id
    db.session.commit()

    response = ActionResponse(
        success=True,
        status="queued",
        job_id=str(job.id),
        message="Download queued. Poll /api/v1/jobs/{job_id} for status.",
    )
    return jsonify(response.model_dump()), 202


@api_bp.route("/jobs/<job_id>", methods=["GET"])
@limiter.limit("300 per minute", key_func=get_api_rate_limit_key)
@require_api_key
def api_job_status(job_id: str):
    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError:
        return make_error_response("invalid_job_id", "Invalid job id", 400)

    job = DownloadJob.query.get(job_uuid)
    if not job or job.user_id != g.api_user.id:
        return make_error_response("not_found", "Job not found", 404)

    media_info = None
    if job.status == "pending_download":
        redis_url = current_app.config.get("REDIS_URL")
        client = redis.Redis.from_url(redis_url, decode_responses=True)
        cached = client.get(f"media_info:{job_id}")
        if cached:
            try:
                media_info = json.loads(cached)
            except json.JSONDecodeError:
                media_info = None

    payload = job_to_response_dict(job)
    if job.status == "complete" and job.is_expired():
        payload["download_url"] = None
    if media_info and media_info.get("qualities"):
        payload["qualities"] = media_info.get("qualities")

    response = JobStatusResponse(**payload)
    return jsonify(response.model_dump()), 200


@api_bp.route("/batch", methods=["POST"])
@limiter.limit("20 per hour", key_func=get_api_rate_limit_key)
@require_api_key
def api_batch():
    data, parse_error = _load_request_json_object()
    if parse_error:
        return make_error_response("invalid_json", parse_error, 400)

    try:
        req = BatchRequest(**data)
    except ValidationError as exc:
        return make_error_response("validation_error", str(exc.errors()), 422)

    allowed, message = check_batch_limit(g.api_user, len(req.urls))
    if not allowed:
        return make_error_response("batch_limit", message, 403)

    batch = BatchQueue(user_id=g.api_user.id, total_jobs=len(req.urls), status="queued")
    db.session.add(batch)
    db.session.commit()

    jobs = []
    for url in req.urls:
        job = DownloadJob(
            user_id=g.api_user.id,
            batch_id=batch.id,
            url=url,
            status="queued",
            selected_quality=req.quality,
            selected_format=req.format.value,
        )
        db.session.add(job)
        jobs.append(job)
    db.session.commit()

    from celery import chain, group

    task_group = group(chain(analyze_url_task.s(str(job.id)), download_media_task.si(str(job.id))) for job in jobs)
    task_group.delay()

    response = ActionResponse(
        success=True,
        batch_id=str(batch.id),
        total_jobs=len(req.urls),
        status="queued",
        message="Batch queued. Poll /api/v1/queue/{batch_id} for status.",
    )
    return jsonify(response.model_dump()), 202


@api_bp.route("/queue/<batch_id>", methods=["GET"])
@limiter.limit("300 per minute", key_func=get_api_rate_limit_key)
@require_api_key
def api_batch_status(batch_id: str):
    try:
        batch_uuid = uuid.UUID(batch_id)
    except ValueError:
        return make_error_response("invalid_batch_id", "Invalid batch id", 400)

    batch = BatchQueue.query.get(batch_uuid)
    if not batch or batch.user_id != g.api_user.id:
        return make_error_response("not_found", "Batch not found", 404)

    jobs = DownloadJob.query.filter_by(batch_id=batch.id).order_by(DownloadJob.created_at.asc()).all()
    job_items = [JobStatusResponse(**job_to_response_dict(job)) for job in jobs]

    zip_url = None
    if batch.status in ("complete", "partial") and batch.zip_url:
        zip_url = batch.zip_url

    response = BatchStatusResponse(
        batch_id=str(batch.id),
        status=batch.status,
        total_jobs=batch.total_jobs or 0,
        completed_jobs=batch.completed_jobs or 0,
        failed_jobs=batch.failed_jobs or 0,
        overall_pct=batch.progress_percentage(),
        zip_url=zip_url,
        jobs=job_items,
    )
    return jsonify(response.model_dump()), 200


@api_bp.route("/history", methods=["GET"])
@limiter.limit("300 per minute", key_func=get_api_rate_limit_key)
@require_api_key
def api_history():
    page = request.args.get("page", 1, type=int)
    if page < 1:
        page = 1
    per_page = request.args.get("per_page", 25, type=int)
    if per_page < 1:
        per_page = 1
    if per_page > 100:
        per_page = 100

    platform = request.args.get("platform")
    status = request.args.get("status", "complete")

    query = DownloadJob.query.filter_by(user_id=g.api_user.id)
    if platform:
        query = query.filter(DownloadJob.platform == platform)
    if status:
        query = query.filter(DownloadJob.status == status)

    pagination = query.order_by(DownloadJob.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
    items = [
        HistoryItemResponse(
            job_id=str(job.id),
            platform=job.platform,
            title=job.title,
            content_type=job.content_type,
            selected_quality=job.selected_quality,
            selected_format=job.selected_format,
            status=job.status,
            file_size_bytes=job.file_size_bytes,
            created_at=job.created_at,
            completed_at=job.completed_at,
        )
        for job in pagination.items
    ]

    response = HistoryResponse(
        items=items,
        total=pagination.total,
        page=pagination.page,
        per_page=pagination.per_page,
        pages=pagination.pages,
    )
    return jsonify(response.model_dump()), 200


@api_bp.route("/subscriptions", methods=["GET"])
@limiter.limit("300 per minute", key_func=get_api_rate_limit_key)
@require_api_key
def api_subscriptions():
    if not g.api_user.is_pro():
        return make_error_response("pro_required", "Upgrade to Pro to use subscriptions.", 403)

    subs = Subscription.query.filter_by(user_id=g.api_user.id).order_by(Subscription.created_at.desc()).all()
    payload = [
        SubscriptionResponse(
            sub_id=str(sub.id),
            channel_url=sub.channel_url,
            platform=sub.platform,
            channel_name=sub.channel_name,
            quality=sub.quality,
            format=sub.format,
            frequency=sub.frequency,
            is_active=sub.is_active,
            last_checked_at=sub.last_checked_at,
            next_check_at=sub.next_check_at,
            total_downloaded=sub.total_downloaded or 0,
            created_at=sub.created_at,
        ).model_dump()
        for sub in subs
    ]
    return jsonify({"subscriptions": payload, "total": len(payload)}), 200


@api_bp.route("/subscriptions", methods=["POST"])
@limiter.limit("60 per hour", key_func=get_api_rate_limit_key)
@require_api_key
def api_subscriptions_create():
    if not g.api_user.is_pro():
        return make_error_response("pro_required", "Upgrade to Pro to use subscriptions.", 403)

    data, parse_error = _load_request_json_object()
    if parse_error:
        return make_error_response("invalid_json", parse_error, 400)

    try:
        req = SubscriptionCreateRequest(**data)
    except ValidationError as exc:
        return make_error_response("validation_error", str(exc.errors()), 422)

    current_count = Subscription.query.filter_by(user_id=g.api_user.id).count()
    if g.api_user.plan == "pro" and current_count >= 20:
        return make_error_response(
            "subscription_limit",
            "Subscription limit reached. Upgrade to Enterprise for unlimited subscriptions.",
            403,
        )

    channel_url = req.channel_url.strip()
    try:
        platform_id, _, clean_url = url_parser.parse_url(channel_url)
    except ValueError:
        return make_error_response("invalid_channel_url", "Invalid channel URL", 400)

    if platform_id == "generic":
        platform_id = _infer_subscription_platform(clean_url)

    allowed = {"youtube", "twitch", "soundcloud", "spotify", "reddit", "bilibili"}
    if platform_id not in allowed or not _is_supported_subscription_url(platform_id, clean_url):
        return make_error_response(
            "unsupported_platform",
            "This platform is not supported for channel subscriptions.",
            400,
        )

    existing = Subscription.query.filter_by(user_id=g.api_user.id, channel_url=clean_url).first()
    if existing:
        return make_error_response("duplicate_subscription", "Already subscribed to this channel.", 409)

    metadata = get_channel_metadata(platform_id, clean_url)

    sub = Subscription(
        user_id=g.api_user.id,
        channel_url=clean_url,
        platform=platform_id,
        channel_name=metadata.get("channel_name") or clean_url,
        channel_id=metadata.get("channel_id"),
        quality=req.quality,
        format=req.format.value,
        frequency=req.frequency,
        notification_email=bool(req.notification_email),
        is_active=True,
        known_content_ids=[],
    )
    sub.next_check_at = sub.calculate_next_check()
    db.session.add(sub)
    db.session.commit()

    response = SubscriptionResponse(
        sub_id=str(sub.id),
        channel_url=sub.channel_url,
        platform=sub.platform,
        channel_name=sub.channel_name,
        quality=sub.quality,
        format=sub.format,
        frequency=sub.frequency,
        is_active=sub.is_active,
        last_checked_at=sub.last_checked_at,
        next_check_at=sub.next_check_at,
        total_downloaded=sub.total_downloaded or 0,
        created_at=sub.created_at,
    )
    return jsonify(response.model_dump()), 201


@api_bp.route("/subscriptions/<sub_id>", methods=["DELETE"])
@limiter.limit("60 per hour", key_func=get_api_rate_limit_key)
@require_api_key
def api_subscriptions_delete(sub_id: str):
    if not g.api_user.is_pro():
        return make_error_response("pro_required", "Upgrade to Pro to use subscriptions.", 403)

    try:
        sub_uuid = uuid.UUID(sub_id)
    except ValueError:
        return make_error_response("invalid_subscription_id", "Invalid subscription id", 400)

    sub = Subscription.query.get(sub_uuid)
    if not sub or sub.user_id != g.api_user.id:
        return make_error_response("not_found", "Subscription not found", 404)

    db.session.delete(sub)
    db.session.commit()

    response = ActionResponse(success=True, message="Subscription deleted")
    return jsonify(response.model_dump()), 200


@api_bp.route("/formats", methods=["GET"])
@limiter.limit("300 per minute", key_func=get_api_rate_limit_key)
@require_api_key
def api_formats():
    formats = [
        FormatInfo(
            id="mp4",
            label="MP4 Video",
            type="video",
            codecs=["h264", "h265", "av1"],
            description="Most compatible video format",
        ),
        FormatInfo(
            id="mkv",
            label="MKV Video",
            type="video",
            codecs=["h264", "h265", "av1", "vp9"],
            description="Best for archiving",
        ),
        FormatInfo(
            id="webm",
            label="WebM Video",
            type="video",
            codecs=["vp9", "av1"],
            description="Web-optimized video",
        ),
        FormatInfo(
            id="mp3",
            label="MP3 Audio",
            type="audio",
            codecs=["mp3"],
            description="Universal audio format",
        ),
        FormatInfo(
            id="flac",
            label="FLAC Audio",
            type="audio",
            codecs=["flac"],
            description="Lossless audio archiving",
        ),
        FormatInfo(
            id="m4a",
            label="M4A Audio",
            type="audio",
            codecs=["aac"],
            description="High quality audio for Apple devices",
        ),
        FormatInfo(
            id="wav",
            label="WAV Audio",
            type="audio",
            codecs=["pcm"],
            description="Uncompressed audio",
        ),
    ]
    return jsonify({"formats": [item.model_dump() for item in formats], "total": len(formats)}), 200


@api_bp.route("/platforms", methods=["GET"])
@limiter.limit("300 per minute", key_func=get_api_rate_limit_key)
@require_api_key
def api_platforms():
    extractors = PlatformExtractor.query.order_by(PlatformExtractor.display_name).all()
    items = []
    for extractor in extractors:
        items.append(
            PlatformInfo(
                platform_id=extractor.platform_id,
                display_name=extractor.display_name,
                status=extractor.status_label(),
                success_rate_7d=extractor.success_rate_7d or 0.0,
                requires_headless=bool(extractor.requires_headless),
                last_success_at=extractor.last_success_at,
                last_failure_at=extractor.last_failure_at,
            )
        )

    status_priority = {"active": 0, "degraded": 1, "down": 2, "disabled": 3}
    items.sort(key=lambda item: status_priority.get(item.status, 9))

    active_count = len([item for item in items if item.status == "active"])
    degraded_count = len([item for item in items if item.status == "degraded"])

    return jsonify(
        {
            "platforms": [item.model_dump() for item in items],
            "total": len(items),
            "active": active_count,
            "degraded": degraded_count,
        }
    ), 200
