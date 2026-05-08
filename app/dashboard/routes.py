import csv
import io
import json
import logging
import re
import time
import uuid
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import urlparse

import httpx
from flask import Response, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, logout_user
from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload

from app.dashboard import dashboard_bp
from app.dashboard.forms import (
    AccountSettingsForm,
    APIKeyForm,
    ChangePasswordForm,
    DeleteAPIKeyForm,
    HistoryFilterForm,
    OnboardingStep1Form,
    OnboardingStep2Form,
    OnboardingStep3Form,
    PreferencesForm,
    SubscriptionForm,
)
from app.downloader.tasks import analyze_url_task, subscription_poll_task
from app.extensions import csrf, db
from app.extractors.base import ExtractorError
from app.extractors.youtube import YoutubeExtractor
from app.models import APIUsage, AuditLog, BatchQueue, DownloadJob, Subscription, User
from app.services import notify, razorpay_service, storage, url_parser
from app.services.proxy import proxy_pool


logger = logging.getLogger(__name__)


def format_bytes(size_bytes: int) -> str:
    if not size_bytes:
        return "0 B"
    size = float(size_bytes)
    if size < 1024:
        return f"{int(size)} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    if size < 1024 ** 3:
        return f"{size / (1024 ** 2):.1f} MB"
    return f"{size / (1024 ** 3):.2f} GB"


def get_relative_time(dt) -> str:
    if not dt:
        return "Never"
    if getattr(dt, "tzinfo", None):
        now = datetime.now(dt.tzinfo)
    else:
        now = datetime.utcnow()
    diff = now - dt
    seconds = int(diff.total_seconds())
    if seconds < 60:
        return "Just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minutes ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hours ago"
    days = hours // 24
    if days < 7:
        return f"{days} days ago"
    return dt.strftime("%b %d, %Y")


def _get_greeting() -> str:
    hour = datetime.utcnow().hour
    if hour < 12:
        return "Good morning"
    if hour < 18:
        return "Good afternoon"
    return "Good evening"


def _platform_choices(user_id):
    platforms = (
        db.session.query(DownloadJob.platform)
        .filter(DownloadJob.user_id == user_id, DownloadJob.platform.isnot(None))
        .distinct()
        .order_by(DownloadJob.platform)
        .all()
    )
    choices = [("", "All Platforms")]
    for row in platforms:
        if row and row[0]:
            choices.append((row[0], row[0].title()))
    return choices, [row[0] for row in platforms if row and row[0]]


def _build_export_params():
    params = dict(request.args)
    params.pop("page", None)
    return params


def _parse_sort(sort_param: str):
    sort_map = {
        "date_desc": DownloadJob.created_at.desc(),
        "date_asc": DownloadJob.created_at.asc(),
        "size_desc": DownloadJob.file_size_bytes.desc().nullslast(),
        "size_asc": DownloadJob.file_size_bytes.asc().nullslast(),
        "platform": func.lower(DownloadJob.platform).asc().nullslast(),
        "title": func.lower(DownloadJob.title).asc().nullslast(),
    }
    return sort_map.get(sort_param, DownloadJob.created_at.desc())


def _extract_domain(url: str) -> str:
    if not url:
        return "Anonymous"
    parsed = urlparse(url)
    return parsed.netloc or "Anonymous"


def _friendly_channel_name(platform: str, name: str) -> str:
    if name:
        return name
    if platform:
        return platform.title()
    return "Channel"


def require_pro(view_func):
    @wraps(view_func)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        if not current_user.is_pro():
            if request.endpoint == "dashboard.api_settings" and request.method == "GET":
                return render_template("dashboard/api_settings.html", upgrade_required=True)
            if request.is_json or request.headers.get("X-Requested-With"):
                return (
                    jsonify(
                        {
                            "error": "pro_required",
                            "message": "This feature requires Pro plan.",
                            "upgrade_url": "/upgrade",
                            "monthly_price": "₹799/month",
                        }
                    ),
                    403,
                )
            flash("This feature requires Pro plan. Upgrade for ₹799/month.", "warning")
            return redirect(url_for("dashboard.upgrade"))
        return view_func(*args, **kwargs)

    return decorated


def check_download_limit(user):
    active_count = (
        DownloadJob.query.filter(
            DownloadJob.user_id == user.id,
            DownloadJob.status.in_(["queued", "analyzing", "downloading", "converting"]),
        ).count()
    )
    max_concurrent = 10 if user.is_pro() else 2
    if active_count >= max_concurrent:
        if user.is_pro():
            return False, f"You have {active_count} active downloads. Max is 10 for Pro."
        return (
            False,
            "Free plan allows 2 concurrent downloads. "
            f"You have {active_count} active. Upgrade to Pro for 10 concurrent downloads.",
        )
    return True, ""


def _extract_quality_height(quality_label: str) -> int:
    value = str(quality_label or "").strip().lower()
    if not value:
        return 0
    if "8k" in value:
        return 4320
    if "4k" in value:
        return 2160
    if "2k" in value:
        return 1440
    match = re.search(r"(\d{3,4})\s*p", value)
    if not match:
        return 0
    try:
        return int(match.group(1))
    except ValueError:
        return 0


def check_quality_limit(user, quality_label: str):
    if user.is_pro():
        return True, ""

    height = _extract_quality_height(quality_label)
    if height >= 1440:
        selected_label = quality_label or f"{height}p"
        return (
            False,
            f"{selected_label} quality requires Pro plan. "
            "Free plan supports up to 1080p. Upgrade for ₹799/month.",
        )
    return True, ""


def check_batch_limit(user, url_count: int):
    if not user.is_pro() and url_count > 5:
        return (
            False,
            "Free plan allows up to 5 URLs per batch. "
            f"You submitted {url_count}. Upgrade to Pro for unlimited batch downloads.",
        )
    return True, ""


def _is_supported_subscription_url(platform_id: str, channel_url: str) -> bool:
    value = (channel_url or "").lower()
    if platform_id == "youtube":
        return any(token in value for token in ("/channel/", "/@", "/c/", "/user/"))
    if platform_id == "twitch":
        return "/videos/" not in value and "/clip/" not in value and "twitch.tv/" in value
    if platform_id == "soundcloud":
        return "soundcloud.com/" in value and "/sets/" not in value
    if platform_id == "spotify":
        return "/show/" in value or value.endswith(".xml") or value.endswith(".rss")
    if platform_id == "reddit":
        return "/r/" in value or "/user/" in value
    if platform_id == "bilibili":
        return "space.bilibili.com" in value
    return False


def _infer_subscription_platform(channel_url: str) -> str:
    parsed = urlparse(channel_url)
    host = (parsed.netloc or "").lower()
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    if "twitch.tv" in host:
        return "twitch"
    if "soundcloud.com" in host:
        return "soundcloud"
    if "spotify.com" in host:
        return "spotify"
    if "reddit.com" in host or "redd.it" in host:
        return "reddit"
    if "bilibili.com" in host:
        return "bilibili"
    return "generic"


def get_channel_metadata(platform_id: str, channel_url: str) -> dict:
    try:
        timeout = httpx.Timeout(5.0, read=5.0)
        if platform_id == "youtube":
            extractor = YoutubeExtractor(proxy_pool=proxy_pool)
            info = extractor.extract_info(
                channel_url,
                option_overrides={
                    "extract_flat": "in_playlist",
                    "skip_download": True,
                    "playlistend": 1,
                    "socket_timeout": 8,
                    "noplaylist": False,
                },
            )
            entries = info.get("entries") or []
            first = entries[0] if entries else {}
            channel_name = first.get("uploader") or info.get("title")
            channel_id = first.get("channel_id") or info.get("channel_id")
            return {"channel_name": channel_name or channel_url, "channel_id": channel_id}

        if platform_id == "twitch":
            match = re.search(r"twitch\.tv/([^/]+)", channel_url, re.IGNORECASE)
            login = match.group(1) if match else ""
            if login:
                query = "query { user(login: \"" + login + "\") { id displayName } }"
                payload = {"query": query}
                headers = {"Client-ID": "kimne78kx3ncx6brgo4mv6wki5h1ko"}
                with httpx.Client(timeout=timeout) as client:
                    response = client.post("https://gql.twitch.tv/gql", json=payload, headers=headers)
                    response.raise_for_status()
                    data = response.json().get("data", {}).get("user") or {}
                return {"channel_name": data.get("displayName") or login, "channel_id": data.get("id")}

        if platform_id == "soundcloud":
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                html = client.get(channel_url).text
                match = re.search(r"client_id\":\"([a-zA-Z0-9]+)\"", html)
                client_id = match.group(1) if match else ""
                if client_id:
                    resolve = client.get(
                        "https://api-v2.soundcloud.com/resolve",
                        params={"url": channel_url, "client_id": client_id},
                    )
                    resolve.raise_for_status()
                    payload = resolve.json()
                    user_data = payload.get("user") or payload
                    return {
                        "channel_name": user_data.get("username") or user_data.get("title") or channel_url,
                        "channel_id": user_data.get("id"),
                    }

        if platform_id == "reddit":
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                response = client.get(channel_url.rstrip("/") + ".json", headers={"User-Agent": "UniversalDL/1.0"})
                response.raise_for_status()
                data = response.json()
                post = data[0]["data"]["children"][0]["data"]
                subreddit = post.get("subreddit") or channel_url
                return {"channel_name": subreddit, "channel_id": post.get("subreddit_id")}

        if platform_id == "bilibili":
            match = re.search(r"space\.bilibili\.com/(\d+)", channel_url)
            uid = match.group(1) if match else ""
            if uid:
                with httpx.Client(timeout=timeout) as client:
                    response = client.get("https://api.bilibili.com/x/space/acc/info", params={"mid": uid})
                    response.raise_for_status()
                    data = response.json().get("data") or {}
                    return {"channel_name": data.get("name") or channel_url, "channel_id": uid}

        if platform_id == "spotify":
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                html = client.get(channel_url).text
                match = re.search(r"\"name\":\"([^\"]+)\",\"type\":\"show\"", html)
                show_name = match.group(1) if match else channel_url
                return {"channel_name": show_name, "channel_id": None}

        if platform_id == "generic":
            return {"channel_name": channel_url, "channel_id": None}
    except Exception:
        logger.warning("Channel metadata lookup failed for %s", channel_url, exc_info=True)
    return {"channel_name": channel_url, "channel_id": None}


@dashboard_bp.route("/dashboard")
@login_required
def dashboard():
    recent_jobs = (
        DownloadJob.query.filter(
            DownloadJob.user_id == current_user.id,
            DownloadJob.status != "cancelled",
        )
        .options(joinedload(DownloadJob.batch))
        .order_by(DownloadJob.created_at.desc())
        .limit(10)
        .all()
    )

    active_jobs = (
        DownloadJob.query.filter(
            DownloadJob.user_id == current_user.id,
            DownloadJob.status.in_(["queued", "analyzing", "downloading", "converting"]),
        )
        .order_by(DownloadJob.created_at.desc())
        .limit(10)
        .all()
    )

    kpi_total_downloads = (
        db.session.query(func.count(DownloadJob.id))
        .filter(DownloadJob.user_id == current_user.id, DownloadJob.status == "complete")
        .scalar()
        or 0
    )

    kpi_total_size = (
        db.session.query(func.coalesce(func.sum(DownloadJob.file_size_bytes), 0))
        .filter(DownloadJob.user_id == current_user.id, DownloadJob.status == "complete")
        .scalar()
        or 0
    )

    kpi_active_subscriptions = (
        Subscription.query.filter_by(user_id=current_user.id, is_active=True).count()
    )

    kpi_active_queue = len(active_jobs)

    subscriptions_summary = (
        Subscription.query.filter_by(user_id=current_user.id, is_active=True)
        .order_by(Subscription.last_download_at.desc().nullslast())
        .limit(5)
        .all()
    )

    recent_batches = (
        BatchQueue.query.filter(
            BatchQueue.user_id == current_user.id,
            BatchQueue.status.in_(["complete", "partial", "in_progress"]),
        )
        .order_by(BatchQueue.created_at.desc())
        .limit(3)
        .all()
    )

    return render_template(
        "dashboard/dashboard.html",
        greeting=_get_greeting(),
        recent_jobs=recent_jobs,
        active_jobs=active_jobs,
        kpi_total_downloads=kpi_total_downloads,
        kpi_total_size=format_bytes(kpi_total_size),
        kpi_active_subscriptions=kpi_active_subscriptions,
        kpi_active_queue=kpi_active_queue,
        subscriptions_summary=subscriptions_summary,
        recent_batches=recent_batches,
        is_free_user=not current_user.is_pro(),
    )


@dashboard_bp.route("/dashboard/queue-status")
@login_required
def dashboard_queue_status():
    def generate():
        while True:
            active_jobs = (
                DownloadJob.query.filter(
                    DownloadJob.user_id == current_user.id,
                    DownloadJob.status.in_(["queued", "analyzing", "downloading", "converting"]),
                )
                .order_by(DownloadJob.created_at.desc())
                .limit(10)
                .all()
            )

            jobs_data = []
            for job in active_jobs:
                jobs_data.append(
                    {
                        "job_id": str(job.id),
                        "title": job.title or "Analyzing...",
                        "platform": job.platform,
                        "thumbnail_url": job.thumbnail_url,
                        "status": job.status,
                        "progress_pct": job.progress_pct or 0,
                        "speed_bps": job.speed_bps or 0,
                        "eta_seconds": job.eta_seconds or 0,
                        "selected_quality": job.selected_quality,
                        "selected_format": job.selected_format,
                        "created_at": job.created_at.isoformat() if job.created_at else None,
                    }
                )

            data = {"active_count": len(jobs_data), "jobs": jobs_data}
            yield "data: " + json.dumps(data) + "\n\n"

            if not jobs_data:
                break
            time.sleep(3)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@dashboard_bp.route("/history")
@login_required
def history():
    platform_choices, platforms_list = _platform_choices(current_user.id)
    form = HistoryFilterForm(request.args, platform_choices=platform_choices)

    base_query = DownloadJob.query.filter(DownloadJob.user_id == current_user.id)
    show_history_banner = False
    if not current_user.is_pro():
        cutoff = datetime.utcnow() - timedelta(days=7)
        base_query = base_query.filter(DownloadJob.created_at >= cutoff)
        show_history_banner = True

    search = (form.search.data or "").strip()
    if search:
        search_value = "%" + search.lower() + "%"
        base_query = base_query.filter(DownloadJob.url.isnot(None))
        base_query = base_query.filter(
            or_(
                func.lower(func.coalesce(DownloadJob.title, "")).like(search_value),
                func.lower(func.coalesce(DownloadJob.url, "")).like(search_value),
            )
        )

    if form.platform.data:
        base_query = base_query.filter(DownloadJob.platform == form.platform.data)

    if form.date_from.data:
        date_from = datetime.combine(form.date_from.data, datetime.min.time())
        base_query = base_query.filter(DownloadJob.created_at >= date_from)

    if form.date_to.data:
        date_to = datetime.combine(form.date_to.data, datetime.min.time()) + timedelta(days=1)
        base_query = base_query.filter(DownloadJob.created_at < date_to)

    if form.content_type.data:
        base_query = base_query.filter(DownloadJob.content_type == form.content_type.data)

    status_value = request.args.get("status") or form.status.data
    if status_value:
        base_query = base_query.filter(DownloadJob.status == status_value)

    sort_param = request.args.get("sort", "date_desc")
    order_by = _parse_sort(sort_param)

    page = request.args.get("page", 1, type=int)
    pagination = base_query.order_by(order_by).paginate(page=page, per_page=25, error_out=False)

    total_size_bytes = (
        base_query.with_entities(func.coalesce(func.sum(DownloadJob.file_size_bytes), 0)).scalar() or 0
    )

    platform_count = (
        base_query.with_entities(DownloadJob.platform, func.count(DownloadJob.id).label("count"))
        .filter(DownloadJob.platform.isnot(None))
        .group_by(DownloadJob.platform)
        .order_by(func.count(DownloadJob.id).desc())
        .first()
    )
    top_platform = platform_count[0] if platform_count else None

    current_filters = dict(request.args)
    current_filters.pop("page", None)

    return render_template(
        "dashboard/history.html",
        jobs=pagination.items,
        pagination=pagination,
        form=form,
        total_count=pagination.total,
        total_size=format_bytes(total_size_bytes),
        platforms_list=platforms_list,
        current_sort=sort_param,
        current_filters=current_filters,
        export_params=_build_export_params(),
        top_platform=top_platform,
        show_history_banner=show_history_banner,
    )


@dashboard_bp.route("/history/delete/<job_id>", methods=["POST"])
@login_required
def delete_history_item(job_id):
    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError:
        return jsonify({"success": False, "message": "Invalid job"}), 404
    job = DownloadJob.query.get(job_uuid)
    if not job:
        return jsonify({"success": False, "message": "Not found"}), 404
    if job.user_id != current_user.id:
        return jsonify({"success": False, "message": "Not allowed"}), 403

    storage.delete_job_files(str(job.id))
    db.session.delete(job)
    db.session.commit()
    AuditLog.log(action="history_delete", user_id=current_user.id, resource_id=job.id)
    return jsonify({"success": True, "message": "Download record deleted"})


@dashboard_bp.route("/history/delete-bulk", methods=["POST"])
@login_required
def delete_history_bulk():
    data = request.get_json(silent=True) or {}
    job_ids = data.get("job_ids") or []
    if not isinstance(job_ids, list) or not job_ids:
        return jsonify({"success": False, "message": "No job ids provided"}), 400
    if len(job_ids) > 100:
        return jsonify({"success": False, "message": "Too many job ids"}), 400

    deleted = 0
    failed = 0

    for job_id in job_ids:
        try:
            job_uuid = uuid.UUID(job_id)
        except ValueError:
            failed += 1
            continue
        job = DownloadJob.query.get(job_uuid)
        if not job or job.user_id != current_user.id:
            failed += 1
            continue
        storage.delete_job_files(str(job.id))
        db.session.delete(job)
        deleted += 1

    db.session.commit()
    AuditLog.log(action="history_bulk_delete", user_id=current_user.id, detail_json={"count": deleted})
    return jsonify({"success": True, "deleted_count": deleted, "failed_count": failed})


@dashboard_bp.route("/history/export")
@login_required
def export_history():
    platform_choices, _ = _platform_choices(current_user.id)
    form = HistoryFilterForm(request.args, platform_choices=platform_choices)

    base_query = DownloadJob.query.filter(DownloadJob.user_id == current_user.id)

    search = (form.search.data or "").strip()
    if search:
        search_value = "%" + search.lower() + "%"
        base_query = base_query.filter(DownloadJob.url.isnot(None))
        base_query = base_query.filter(
            or_(
                func.lower(func.coalesce(DownloadJob.title, "")).like(search_value),
                func.lower(func.coalesce(DownloadJob.url, "")).like(search_value),
            )
        )

    if form.platform.data:
        base_query = base_query.filter(DownloadJob.platform == form.platform.data)

    if form.date_from.data:
        date_from = datetime.combine(form.date_from.data, datetime.min.time())
        base_query = base_query.filter(DownloadJob.created_at >= date_from)

    if form.date_to.data:
        date_to = datetime.combine(form.date_to.data, datetime.min.time()) + timedelta(days=1)
        base_query = base_query.filter(DownloadJob.created_at < date_to)

    if form.content_type.data:
        base_query = base_query.filter(DownloadJob.content_type == form.content_type.data)

    status_value = request.args.get("status") or form.status.data
    if status_value:
        base_query = base_query.filter(DownloadJob.status == status_value)

    jobs = base_query.order_by(DownloadJob.created_at.desc()).all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "ID",
            "Title",
            "Platform",
            "Content Type",
            "Quality",
            "Format",
            "Status",
            "File Size (MB)",
            "Created At",
            "Completed At",
            "URL",
        ]
    )
    for job in jobs:
        size_mb = round((job.file_size_bytes or 0) / (1024 * 1024), 2)
        writer.writerow(
            [
                str(job.id),
                job.title or "",
                job.platform or "",
                job.content_type or "",
                job.selected_quality or "",
                (job.selected_format or "").upper(),
                job.status or "",
                size_mb,
                job.created_at.isoformat() if job.created_at else "",
                job.completed_at.isoformat() if job.completed_at else "",
                job.url or "",
            ]
        )

    date_str = datetime.utcnow().strftime("%Y%m%d")
    response = Response(buffer.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = (
        "attachment; filename=universaldl_history_" + date_str + ".csv"
    )
    return response


@dashboard_bp.route("/history/redownload/<job_id>", methods=["POST"])
@login_required
def redownload_job(job_id):
    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError:
        return jsonify({"success": False, "message": "Invalid job"}), 404
    job = DownloadJob.query.get(job_uuid)
    if not job or job.user_id != current_user.id:
        return jsonify({"success": False, "message": "Not found"}), 404
    if not job.url:
        return jsonify({"success": False, "message": "Original URL not available for re-download"}), 400

    new_job = DownloadJob(
        user_id=current_user.id,
        url=job.url,
        status="queued",
        selected_quality=job.selected_quality,
        selected_format=job.selected_format,
        subtitle_language=job.subtitle_language,
        subtitle_embed=job.subtitle_embed,
    )
    db.session.add(new_job)
    db.session.commit()

    analyze_url_task.delay(str(new_job.id))
    return jsonify(
        {
            "success": True,
            "new_job_id": str(new_job.id),
            "redirect_url": "/download",
        }
    )


@dashboard_bp.route("/subscriptions")
@login_required
def subscriptions():
    if not current_user.is_pro():
        return render_template(
            "dashboard/subscriptions.html",
            upgrade_required=True,
            pro_monthly_price="₹799",
            pro_annual_price="₹6,399",
        )

    subscriptions_list = (
        Subscription.query.filter_by(user_id=current_user.id)
        .order_by(Subscription.created_at.desc())
        .all()
    )

    max_subscriptions = 20 if current_user.plan == "pro" else None
    can_add_more = current_user.plan == "enterprise" or len(subscriptions_list) < 20

    form = SubscriptionForm()
    return render_template(
        "dashboard/subscriptions.html",
        subscriptions=subscriptions_list,
        form=form,
        upgrade_required=False,
        subscription_count=len(subscriptions_list),
        max_subscriptions=max_subscriptions,
        can_add_more=can_add_more,
    )


@dashboard_bp.route("/subscriptions/add", methods=["POST"])
@login_required
@require_pro
def add_subscription():
    if not current_user.is_pro():
        return (
            jsonify({"success": False, "message": "Upgrade required to use subscriptions."}),
            403,
        )

    current_count = Subscription.query.filter_by(user_id=current_user.id).count()
    if current_user.plan == "pro" and current_count >= 20:
        return (
            jsonify(
                {
                    "success": False,
                    "message": "Subscription limit reached. Upgrade to Enterprise for unlimited subscriptions.",
                }
            ),
            403,
        )

    payload = request.get_json(silent=True) or {}
    form = SubscriptionForm(meta={"csrf": False}, data=payload)
    if not form.validate():
        error = next(iter(form.errors.values()))[0] if form.errors else "Invalid input"
        return jsonify({"success": False, "message": error}), 400

    channel_url = (form.channel_url.data or "").strip()
    try:
        platform_id, _, clean_url = url_parser.parse_url(channel_url)
    except ValueError:
        return jsonify({"success": False, "message": "Invalid channel URL"}), 400
    if platform_id == "generic":
        platform_id = _infer_subscription_platform(clean_url)

    allowed = {"youtube", "twitch", "soundcloud", "spotify", "reddit", "bilibili"}
    if platform_id not in allowed or not _is_supported_subscription_url(platform_id, clean_url):
        return (
            jsonify(
                {
                    "success": False,
                    "message": "This platform is not supported for channel subscriptions. Supported: YouTube, Twitch, SoundCloud, Spotify, Reddit, Bilibili.",
                }
            ),
            400,
        )

    existing = Subscription.query.filter_by(user_id=current_user.id, channel_url=clean_url).first()
    if existing:
        return jsonify({"success": False, "message": "You are already subscribed to this channel."}), 409

    metadata = get_channel_metadata(platform_id, clean_url)

    sub = Subscription(
        user_id=current_user.id,
        channel_url=clean_url,
        platform=platform_id,
        channel_name=metadata.get("channel_name") or clean_url,
        channel_id=metadata.get("channel_id"),
        quality=form.quality.data,
        format=form.format.data,
        frequency=form.frequency.data,
        notification_email=bool(form.notification_email.data),
        is_active=True,
        known_content_ids=[],
    )
    sub.next_check_at = sub.calculate_next_check()
    db.session.add(sub)
    db.session.commit()

    AuditLog.log(action="subscription_add", user_id=current_user.id, resource_id=sub.id)

    frequency_text = {
        "daily": "24 hours",
        "weekly": "7 days",
        "hourly": "1 hour",
    }.get(sub.frequency, "24 hours")

    return (
        jsonify(
            {
                "success": True,
                "subscription_id": str(sub.id),
                "channel_name": sub.channel_name,
                "platform": sub.platform,
                "message": "Subscribed to "
                + _friendly_channel_name(sub.platform, sub.channel_name)
                + ". First check scheduled in "
                + frequency_text
                + ".",
            }
        ),
        201,
    )


@dashboard_bp.route("/subscriptions/delete/<sub_id>", methods=["POST"])
@login_required
def delete_subscription(sub_id):
    try:
        sub_uuid = uuid.UUID(sub_id)
    except ValueError:
        return jsonify({"success": False, "message": "Invalid subscription"}), 404
    sub = Subscription.query.get(sub_uuid)
    if not sub or sub.user_id != current_user.id:
        return jsonify({"success": False, "message": "Not found"}), 404
    db.session.delete(sub)
    db.session.commit()
    AuditLog.log(action="subscription_delete", user_id=current_user.id, resource_id=sub.id)
    return jsonify({"success": True, "message": "Subscription removed"})


@dashboard_bp.route("/subscriptions/toggle/<sub_id>", methods=["POST"])
@login_required
def toggle_subscription(sub_id):
    try:
        sub_uuid = uuid.UUID(sub_id)
    except ValueError:
        return jsonify({"success": False, "message": "Invalid subscription"}), 404
    sub = Subscription.query.get(sub_uuid)
    if not sub or sub.user_id != current_user.id:
        return jsonify({"success": False, "message": "Not found"}), 404

    sub.is_active = not sub.is_active
    if sub.is_active:
        sub.next_check_at = sub.calculate_next_check()
    db.session.commit()

    return jsonify(
        {
            "success": True,
            "is_active": sub.is_active,
            "message": "Subscription resumed" if sub.is_active else "Subscription paused",
        }
    )


@dashboard_bp.route("/subscriptions/test/<sub_id>", methods=["POST"])
@login_required
def test_subscription(sub_id):
    try:
        sub_uuid = uuid.UUID(sub_id)
    except ValueError:
        return jsonify({"success": False, "message": "Invalid subscription"}), 404
    sub = Subscription.query.get(sub_uuid)
    if not sub or sub.user_id != current_user.id:
        return jsonify({"success": False, "message": "Not found"}), 404

    subscription_poll_task.delay(str(sub.id), force=True)
    return jsonify(
        {
            "success": True,
            "message": "Checking for new content now. Downloads will appear in your queue shortly.",
        }
    )


@dashboard_bp.route("/subscriptions/status/<sub_id>")
@login_required
def subscription_status(sub_id):
    try:
        sub_uuid = uuid.UUID(sub_id)
    except ValueError:
        return jsonify({"success": False, "message": "Invalid subscription"}), 404
    sub = Subscription.query.get(sub_uuid)
    if not sub or sub.user_id != current_user.id:
        return jsonify({"success": False, "message": "Not found"}), 404

    channel_filter = DownloadJob.url.isnot(None)
    channel_filter = channel_filter & DownloadJob.url.ilike("%" + sub.channel_url + "%")

    total_downloaded = (
        DownloadJob.query.filter(
            DownloadJob.user_id == current_user.id,
            DownloadJob.status == "complete",
            channel_filter,
        ).count()
    )

    recent_jobs = (
        DownloadJob.query.filter(
            DownloadJob.user_id == current_user.id,
            channel_filter,
        )
        .order_by(DownloadJob.created_at.desc())
        .limit(5)
        .all()
    )

    recent_downloads = [
        {
            "title": job.title or "",
            "created_at": job.created_at.isoformat() if job.created_at else None,
        }
        for job in recent_jobs
    ]

    return jsonify(
        {
            "sub_id": str(sub.id),
            "channel_name": sub.channel_name,
            "is_active": sub.is_active,
            "last_checked_at": sub.last_checked_at.isoformat() if sub.last_checked_at else None,
            "next_check_at": sub.next_check_at.isoformat() if sub.next_check_at else None,
            "total_downloaded": total_downloaded,
            "recent_downloads": recent_downloads,
        }
    )


@dashboard_bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    active_tab = request.args.get("tab", "profile")

    profile_form = AccountSettingsForm(
        display_name=current_user.display_name,
        email=current_user.email,
    )
    password_form = ChangePasswordForm()
    prefs_form = PreferencesForm(
        default_quality=(
            "audio_only" if current_user.default_quality == "audio" else current_user.default_quality
        ),
        default_format=current_user.default_format,
        email_notifications=current_user.email_notifications,
        anonymous_mode=current_user.anonymous_mode,
    )

    if request.method == "POST":
        form_name = request.form.get("form_name")

        if form_name == "profile":
            profile_form = AccountSettingsForm()
            if profile_form.validate_on_submit():
                if profile_form.email.data != current_user.email:
                    existing = User.query.filter_by(email=profile_form.email.data).first()
                    if existing and existing.id != current_user.id:
                        profile_form.email.errors.append("Email already in use by another account")
                        return render_template(
                            "dashboard/settings.html",
                            profile_form=profile_form,
                            password_form=password_form,
                            prefs_form=prefs_form,
                            active_tab="profile",
                            has_password=current_user.password_hash is not None,
                            has_google=current_user.google_oauth_id is not None,
                            plan_expires=current_user.plan_expires_at,
                            is_pro=current_user.is_pro(),
                            account_id_prefix=str(current_user.id)[:8],
                        )
                current_user.display_name = profile_form.display_name.data.strip() or None
                current_user.email = profile_form.email.data.lower().strip()
                db.session.commit()
                AuditLog.log(
                    action="profile_update",
                    user_id=str(current_user.id),
                    ip_address=request.remote_addr,
                )
                flash("Profile updated successfully.", "success")
                return redirect(url_for("dashboard.settings", tab="profile"))

        if form_name == "password":
            password_form = ChangePasswordForm()
            if current_user.password_hash is None:
                password_form.current_password.validators = []
                password_form.current_password.data = "oauth"
            if password_form.validate_on_submit():
                if current_user.password_hash is not None:
                    if not current_user.check_password(password_form.current_password.data):
                        flash("Current password is incorrect.", "danger")
                        return redirect(url_for("dashboard.settings", tab="security"))
                current_user.set_password(password_form.new_password.data)
                db.session.commit()
                AuditLog.log(
                    action="password_change",
                    user_id=str(current_user.id),
                    ip_address=request.remote_addr,
                )
                flash("Password changed successfully.", "success")
                return redirect(url_for("dashboard.settings", tab="security"))

        if form_name == "preferences":
            prefs_form = PreferencesForm()
            if prefs_form.validate_on_submit():
                quality_value = prefs_form.default_quality.data
                if quality_value == "audio_only":
                    quality_value = "audio"
                current_user.default_quality = quality_value
                current_user.default_format = prefs_form.default_format.data
                current_user.email_notifications = bool(prefs_form.email_notifications.data)
                current_user.anonymous_mode = bool(prefs_form.anonymous_mode.data)
                db.session.commit()
                flash("Preferences saved.", "success")
                return redirect(url_for("dashboard.settings", tab="preferences"))

        flash("Unable to update settings. Please review the form.", "warning")
        return redirect(url_for("dashboard.settings", tab=active_tab))

    return render_template(
        "dashboard/settings.html",
        profile_form=profile_form,
        password_form=password_form,
        prefs_form=prefs_form,
        active_tab=active_tab,
        has_password=current_user.password_hash is not None,
        has_google=current_user.google_oauth_id is not None,
        plan_expires=current_user.plan_expires_at,
        is_pro=current_user.is_pro(),
        account_id_prefix=str(current_user.id)[:8],
    )


@dashboard_bp.route("/settings/delete-account", methods=["POST"])
@login_required
def delete_account():
    data = request.get_json(silent=True) or {}
    password = data.get("password")
    confirm = data.get("confirm")

    if not password or not current_user.check_password(password):
        return jsonify({"success": False, "message": "Password is incorrect"}), 400
    if confirm != "DELETE":
        return jsonify({"success": False, "message": "Confirm keyword is invalid"}), 400

    AuditLog.log(action="account_delete", user_id=current_user.id, detail_json={"email": current_user.email})

    jobs = DownloadJob.query.filter_by(user_id=current_user.id).all()
    for job in jobs:
        storage.delete_job_files(str(job.id))

    Subscription.query.filter_by(user_id=current_user.id).delete(synchronize_session=False)
    DownloadJob.query.filter_by(user_id=current_user.id).delete(synchronize_session=False)
    BatchQueue.query.filter_by(user_id=current_user.id).delete(synchronize_session=False)
    AuditLog.query.filter_by(user_id=current_user.id).delete(synchronize_session=False)

    user = User.query.get(current_user.id)
    if user:
        db.session.delete(user)
    db.session.commit()
    logout_user()

    return jsonify({"success": True, "redirect": "/"})


@dashboard_bp.route("/settings/toggle-anonymous", methods=["POST"])
@login_required
def toggle_anonymous():
    data = request.get_json(silent=True) or {}
    enabled = bool(data.get("anonymous_mode"))
    current_user.anonymous_mode = enabled
    db.session.commit()
    AuditLog.log(
        action="privacy_toggle",
        user_id=str(current_user.id),
        ip_address=request.remote_addr,
        detail_json={"anonymous_mode": enabled},
    )
    return jsonify({"success": True, "anonymous_mode": enabled})


@dashboard_bp.route("/settings/download-data")
@login_required
def download_user_data():
    user = current_user
    downloads = (
        DownloadJob.query.filter_by(user_id=user.id, status="complete")
        .order_by(DownloadJob.created_at.desc())
        .all()
    )
    subscriptions = (
        Subscription.query.filter_by(user_id=user.id).order_by(Subscription.created_at.desc()).all()
    )

    data = {
        "account": {
            "email": user.email,
            "display_name": user.display_name,
            "plan": user.plan,
            "created_at": user.created_at,
        },
        "preferences": {
            "default_quality": user.default_quality,
            "default_format": user.default_format,
            "email_notifications": user.email_notifications,
        },
        "download_history": [
            {
                "title": job.title,
                "platform": job.platform,
                "quality": job.selected_quality,
                "format": job.selected_format,
                "created_at": job.created_at,
                "file_size_bytes": job.file_size_bytes,
            }
            for job in downloads
        ],
        "subscriptions": [
            {
                "channel_name": sub.channel_name,
                "platform": sub.platform,
                "frequency": sub.frequency,
                "created_at": sub.created_at,
                "total_downloaded": sub.total_downloaded,
            }
            for sub in subscriptions
        ],
        "export_date": datetime.utcnow().isoformat(),
    }

    date_str = datetime.utcnow().strftime("%Y%m%d")
    response = Response(
        json.dumps(data, indent=2, default=str),
        mimetype="application/json",
    )
    response.headers["Content-Disposition"] = (
        "attachment; filename=universaldl_data_export_" + date_str + ".json"
    )
    return response


@dashboard_bp.route("/settings/api")
@login_required
@require_pro
def api_settings():
    api_key_form = APIKeyForm()
    delete_form = DeleteAPIKeyForm()

    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    usage_last_30_days = (
        APIUsage.query.filter(
            APIUsage.user_id == current_user.id,
            APIUsage.called_at >= thirty_days_ago,
        ).count()
    )

    usage_by_day = (
        APIUsage.query.with_entities(
            func.date(APIUsage.called_at).label("date"),
            func.count(APIUsage.id).label("count"),
        )
        .filter(APIUsage.user_id == current_user.id, APIUsage.called_at >= thirty_days_ago)
        .group_by(func.date(APIUsage.called_at))
        .order_by(func.date(APIUsage.called_at))
        .all()
    )

    usage_by_endpoint = (
        APIUsage.query.with_entities(
            APIUsage.endpoint,
            APIUsage.method,
            func.count(APIUsage.id).label("count"),
        )
        .filter(APIUsage.user_id == current_user.id, APIUsage.called_at >= thirty_days_ago)
        .group_by(APIUsage.endpoint, APIUsage.method)
        .order_by(func.count(APIUsage.id).desc())
        .limit(5)
        .all()
    )

    monthly_limit = current_app.config.get("API_MONTHLY_LIMIT", 10000)
    usage_percentage = (usage_last_30_days / monthly_limit) * 100 if monthly_limit else 0

    date_map = {str(row.date): row.count for row in usage_by_day}
    daily_labels = []
    daily_counts = []
    start_date = datetime.utcnow().date() - timedelta(days=29)
    for offset in range(30):
        day = start_date + timedelta(days=offset)
        daily_labels.append(day.strftime("%b %d"))
        daily_counts.append(int(date_map.get(day.isoformat(), 0)))

    last_usage = (
        APIUsage.query.filter(APIUsage.user_id == current_user.id)
        .order_by(APIUsage.called_at.desc())
        .first()
    )

    return render_template(
        "dashboard/api_settings.html",
        upgrade_required=False,
        api_key_form=api_key_form,
        delete_form=delete_form,
        has_api_key=current_user.api_key_hash is not None,
        api_key_prefix=current_user.api_key_prefix,
        usage_last_30_days=usage_last_30_days,
        monthly_limit=monthly_limit,
        usage_percentage=min(usage_percentage, 100),
        daily_labels=json.dumps(daily_labels),
        daily_counts=json.dumps(daily_counts),
        usage_by_endpoint=usage_by_endpoint,
        last_used_at=last_usage.called_at if last_usage else None,
    )


@dashboard_bp.route("/settings/api/generate", methods=["POST"])
@login_required
@require_pro
def generate_api_key():
    data = request.get_json(silent=True)
    if data:
        form = APIKeyForm(meta={"csrf": False}, data=data)
    else:
        form = APIKeyForm()
    if not form.validate():
        return jsonify({"error": "validation", "message": form.errors}), 400

    raw_key = current_user.generate_api_key()
    db.session.commit()

    AuditLog.log(
        action="api_key_generate",
        user_id=str(current_user.id),
        ip_address=request.remote_addr,
    )

    return jsonify(
        {
            "success": True,
            "raw_key": raw_key,
            "prefix": current_user.api_key_prefix,
            "message": "API key generated. Copy it now - it will not be shown again.",
        }
    )


@dashboard_bp.route("/settings/api/revoke", methods=["POST"])
@login_required
@require_pro
def revoke_api_key():
    data = request.get_json(silent=True)
    if data:
        form = DeleteAPIKeyForm(meta={"csrf": False}, data=data)
        if not form.validate():
            return jsonify({"error": "validation", "message": form.errors}), 400

    current_user.api_key_hash = None
    current_user.api_key_prefix = None
    db.session.commit()

    AuditLog.log(
        action="api_key_revoke",
        user_id=str(current_user.id),
        ip_address=request.remote_addr,
    )

    return jsonify({"success": True, "message": "API key revoked successfully."})


@dashboard_bp.route("/onboarding")
@login_required
def onboarding():
    if current_user.is_onboarded:
        return redirect("/dashboard")

    step = int(request.args.get("step", 1))
    if step not in (1, 2, 3):
        step = 1

    onboarding_data = session.get("onboarding_data", {})

    if step == 1:
        form = OnboardingStep1Form(
            default_format=onboarding_data.get("default_format", "mp4"),
            default_quality=onboarding_data.get("default_quality", "best"),
        )
    elif step == 2:
        form = OnboardingStep2Form(
            email_notifications=onboarding_data.get("email_notifications", True),
            anonymous_mode=onboarding_data.get("anonymous_mode", False),
        )
    else:
        form = OnboardingStep3Form()

    return render_template(
        "dashboard/onboarding.html",
        step=step,
        form=form,
        onboarding_data=onboarding_data,
        total_steps=3,
    )


@dashboard_bp.route("/onboarding/step/1", methods=["POST"])
@login_required
def onboarding_step1():
    form = OnboardingStep1Form()
    if form.validate_on_submit():
        if "onboarding_data" not in session:
            session["onboarding_data"] = {}
        session["onboarding_data"]["default_format"] = form.default_format.data
        session["onboarding_data"]["default_quality"] = form.default_quality.data
        session.modified = True
        return redirect("/onboarding?step=2")
    flash("Please complete all fields.", "warning")
    return redirect("/onboarding?step=1")


@dashboard_bp.route("/onboarding/step/2", methods=["POST"])
@login_required
def onboarding_step2():
    form = OnboardingStep2Form()
    if form.validate_on_submit():
        if "onboarding_data" not in session:
            session["onboarding_data"] = {}
        session["onboarding_data"]["email_notifications"] = form.email_notifications.data
        session["onboarding_data"]["anonymous_mode"] = form.anonymous_mode.data
        session.modified = True
        return redirect("/onboarding?step=3")
    return redirect("/onboarding?step=2")


@dashboard_bp.route("/onboarding/complete", methods=["POST"])
@login_required
def complete_onboarding():
    onboarding_data = session.get("onboarding_data", {})

    current_user.default_format = onboarding_data.get(
        "default_format",
        current_user.default_format,
    )
    current_user.default_quality = onboarding_data.get(
        "default_quality",
        current_user.default_quality,
    )
    current_user.email_notifications = onboarding_data.get(
        "email_notifications",
        current_user.email_notifications,
    )
    current_user.anonymous_mode = onboarding_data.get(
        "anonymous_mode",
        current_user.anonymous_mode,
    )
    current_user.is_onboarded = True

    db.session.commit()

    session.pop("onboarding_data", None)
    session.modified = True

    AuditLog.log("onboarding_complete", user_id=str(current_user.id))

    notify.send_welcome_email(
        current_user.email,
        current_user.display_name or current_user.email.split("@")[0],
    )

    flash("Welcome to UniversalDL! Your preferences have been saved.", "success")
    return redirect("/dashboard")


@dashboard_bp.route("/onboarding/skip")
@login_required
def skip_onboarding():
    current_user.is_onboarded = True
    db.session.commit()
    session.pop("onboarding_data", None)
    AuditLog.log("onboarding_skipped", user_id=str(current_user.id))
    return redirect("/dashboard")


@dashboard_bp.route("/upgrade")
@login_required
def upgrade():
    plan = request.args.get("plan")
    success = request.args.get("success", "false").lower() == "true"
    plan_progress_pct = None
    days_remaining = None
    if current_user.plan_expires_at:
        remaining = current_user.plan_expires_at - datetime.utcnow()
        days_remaining = max(0, remaining.days)
        total_days = 365 if days_remaining > 60 else 30
        if total_days > 0:
            plan_progress_pct = min(100, max(5, (days_remaining / total_days) * 100))
    return render_template(
        "dashboard/upgrade.html",
        is_already_pro=current_user.is_pro(),
        plan_expires=current_user.plan_expires_at,
        plan_progress_pct=plan_progress_pct,
        days_remaining=days_remaining,
        razorpay_key_id=current_app.config.get("RAZORPAY_KEY_ID", ""),
        pro_monthly_paise=79900,
        pro_annual_paise=639900,
        pro_monthly_display="₹799",
        pro_annual_display="₹6,399",
        savings_display="₹2,189",
        user_email=current_user.email,
        user_name=current_user.display_name or current_user.email.split("@")[0],
        preselected_plan=plan,
        payment_success=success,
    )


@dashboard_bp.route("/upgrade/create-order", methods=["POST"])
@login_required
def create_upgrade_order():
    payload = request.get_json(silent=True) or {}
    plan_name = payload.get("plan")
    if plan_name not in ("pro_monthly", "pro_annual"):
        return jsonify({"error": "invalid_plan", "message": "Invalid plan selected."}), 400

    try:
        plan_details = razorpay_service.get_plan_details(plan_name)
        order = razorpay_service.create_order(
            amount_paise=plan_details["amount_paise"],
            receipt=f"udl_{str(current_user.id)[:8]}_{plan_name}",
            notes={
                "user_id": str(current_user.id),
                "user_email": current_user.email,
                "plan": plan_name,
            },
        )
    except ValueError as exc:
        return jsonify({"error": "config_error", "message": str(exc)}), 500
    except RuntimeError as exc:
        return jsonify({"error": "order_failed", "message": str(exc)}), 502

    session["pending_order"] = {
        "order_id": order.get("id"),
        "plan_name": plan_name,
        "amount_paise": plan_details["amount_paise"],
        "created_at": datetime.utcnow().isoformat(),
    }
    session.modified = True

    AuditLog.log(
        "payment_order_created",
        user_id=str(current_user.id),
        detail_json={
            "order_id": order.get("id"),
            "plan": plan_name,
            "amount_paise": plan_details["amount_paise"],
        },
    )

    return jsonify(
        {
            "success": True,
            "order_id": order.get("id"),
            "amount": plan_details["amount_paise"],
            "currency": "INR",
            "plan_name": plan_name,
            "plan_description": plan_details["description"],
        }
    )


@dashboard_bp.route("/upgrade/verify-payment", methods=["POST"])
@login_required
def verify_upgrade_payment():
    payload = request.get_json(silent=True) or {}
    order_id = payload.get("razorpay_order_id")
    payment_id = payload.get("razorpay_payment_id")
    signature = payload.get("razorpay_signature")
    if not order_id or not payment_id or not signature:
        return jsonify({"error": "missing_fields", "message": "Incomplete payment data."}), 400

    pending = session.get("pending_order")
    if not pending:
        return (
            jsonify({"error": "session_expired", "message": "Payment session expired. Please try again."}),
            400,
        )

    if pending.get("order_id") != order_id:
        AuditLog.log(
            "payment_fraud_attempt",
            user_id=str(current_user.id),
            detail_json={
                "submitted_order": order_id,
                "session_order": pending.get("order_id"),
            },
        )
        return (
            jsonify({"error": "order_mismatch", "message": "Order verification failed. Please contact support."}),
            400,
        )

    is_valid = razorpay_service.verify_payment_signature(order_id, payment_id, signature)
    if not is_valid:
        AuditLog.log(
            "payment_signature_invalid",
            user_id=str(current_user.id),
            detail_json={"order_id": order_id, "payment_id": payment_id},
        )
        return (
            jsonify({"error": "signature_invalid", "message": "Payment verification failed. Please contact support."}),
            400,
        )

    success = razorpay_service.activate_pro_plan(
        user=current_user,
        plan_name=pending.get("plan_name"),
        payment_id=payment_id,
        order_id=order_id,
    )
    if not success:
        return (
            jsonify(
                {
                    "error": "activation_failed",
                    "message": "Payment received but plan activation failed. Please contact support with payment ID: "
                    + payment_id,
                }
            ),
            500,
        )

    session.pop("pending_order", None)
    session.modified = True

    plan_details = razorpay_service.get_plan_details(pending.get("plan_name"))
    notify.send_upgrade_confirmation_email(
        current_user.email,
        current_user.display_name or current_user.email.split("@")[0],
        pending.get("plan_name"),
        current_user.plan_expires_at,
    )

    return jsonify(
        {
            "success": True,
            "message": "Pro plan activated successfully!",
            "redirect_url": "/upgrade?success=true",
            "plan": pending.get("plan_name"),
            "expires_at": current_user.plan_expires_at.isoformat() if current_user.plan_expires_at else None,
        }
    )


@dashboard_bp.route("/upgrade/webhook", methods=["POST"])
@csrf.exempt
def razorpay_webhook():
    payload_body = request.get_data()
    signature = request.headers.get("X-Razorpay-Signature", "")

    is_valid = razorpay_service.verify_webhook_signature(payload_body, signature)
    if not is_valid:
        logger.warning("Razorpay webhook with invalid signature from %s", request.remote_addr)
        return jsonify({"error": "invalid_signature"}), 400

    payload = json.loads(payload_body)
    event = payload.get("event")
    logger.info("Razorpay webhook received: %s", event)

    if event == "payment.captured":
        payment_data = payload.get("payload", {}).get("payment", {}).get("entity", {})
        order_id = payment_data.get("order_id")
        payment_id = payment_data.get("id")
        notes = payment_data.get("notes", {})
        user_id = notes.get("user_id")
        plan_name = notes.get("plan")

        if user_id and plan_name:
            try:
                user = User.query.get(uuid.UUID(user_id))
            except ValueError:
                user = None
            if user:
                razorpay_service.activate_pro_plan(user, plan_name, payment_id, order_id)
                logger.info("Webhook: Pro activated for user %s", user.email)
            else:
                logger.error("Webhook: User %s not found for payment %s", user_id, payment_id)

    elif event == "payment.failed":
        payment_data = payload.get("payload", {}).get("payment", {}).get("entity", {})
        logger.warning(
            "Payment failed: %s, error: %s",
            payment_data.get("id"),
            payment_data.get("error_description"),
        )

    elif event == "order.paid":
        order_entity = payload.get("payload", {}).get("order", {}).get("entity", {})
        logger.info("Order paid: %s", order_entity.get("id"))

    else:
        logger.debug("Unhandled Razorpay webhook event: %s", event)

    return jsonify({"status": "ok"}), 200
