import json
import mimetypes
import os
import time
import uuid
from datetime import datetime, timedelta

from celery import chain, group
from flask import Response, current_app, flash, jsonify, redirect, render_template, request, send_file, stream_with_context, url_for
from flask_login import current_user, login_required

from app.downloader import downloader_bp
from app.dashboard.routes import check_batch_limit, check_download_limit, check_quality_limit, require_pro
from app.downloader.forms import BatchDownloadForm, DownloadForm
from app.downloader.tasks import analyze_url_task, delete_job_files_task, download_media_task
from app.extensions import csrf, db, limiter
from app.models import AuditLog, BatchQueue, DownloadJob
from app.services import storage, url_parser


def _safe_content_type(value: str) -> str:
    return url_parser.normalize_content_type(value)


def _rate_limit_analyze():
    return "120 per minute" if current_user.is_authenticated else "30 per minute"


def _rate_limit_download():
    if current_user.is_authenticated and current_user.is_pro():
        return "200 per hour"
    return "20 per hour"


csrf.exempt(downloader_bp)


@downloader_bp.before_request
def enforce_csrf_for_browser_requests():
    if request.method in {"GET", "HEAD", "OPTIONS", "TRACE"}:
        return None
    if request.headers.get("X-Extension-Version"):
        return None
    csrf.protect()
    return None


@downloader_bp.route("/download", methods=["GET"])
def download_page():
    form = DownloadForm()
    prefill_url = request.args.get("url")
    if prefill_url:
        form.url.data = prefill_url
    if current_user.is_authenticated:
        default_quality = current_user.default_quality
        if default_quality == "audio":
            default_quality = "audio_only"
        form.quality.data = default_quality
        form.format.data = current_user.default_format
    return render_template("downloader/download.html", form=form)


@downloader_bp.route("/download/analyze", methods=["POST"])
@limiter.limit(_rate_limit_analyze)
def analyze_url():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    try:
        platform_id, content_type, clean_url = url_parser.parse_url(url)
    except ValueError:
        return (
            jsonify(
                {
                    "error": "invalid_url",
                    "message": "Please enter a valid URL starting with http:// or https://",
                }
            ),
            400,
        )

    job = DownloadJob(
        user_id=current_user.id if current_user.is_authenticated else None,
        url=clean_url,
        platform=platform_id,
        content_type=_safe_content_type(content_type),
        status="queued",
        selected_quality=data.get("quality", "best"),
        selected_format=data.get("format", "mp4"),
    )
    db.session.add(job)
    db.session.commit()

    task = analyze_url_task.delay(str(job.id))
    job.celery_task_id = task.id
    db.session.commit()

    return jsonify({"job_id": str(job.id), "status": "analyzing"})


@downloader_bp.route("/download/start", methods=["POST"])
@limiter.limit(_rate_limit_download)
def start_download():
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id")
    if not job_id:
        return jsonify({"error": "missing_job_id"}), 400

    job = DownloadJob.query.get(uuid.UUID(job_id))
    if not job:
        return jsonify({"error": "not_found"}), 404

    if current_user.is_authenticated:
        if job.user_id != current_user.id:
            return jsonify({"error": "forbidden"}), 403
    elif job.user_id is not None:
        return jsonify({"error": "forbidden"}), 403

    requested_quality = data.get("quality", job.selected_quality)
    quality_for_limit = requested_quality
    try:
        from app.downloader.tasks import _load_cached_media_info, _pick_quality

        media_info = _load_cached_media_info(str(job.id))
        if media_info:
            matched_quality = _pick_quality(media_info, requested_quality)
            if matched_quality and matched_quality.get("label"):
                quality_for_limit = matched_quality.get("label")
    except Exception:
        quality_for_limit = requested_quality

    if current_user.is_authenticated:
        allowed, message = check_download_limit(current_user)
        if not allowed:
            return (
                jsonify(
                    {
                        "error": "limit_exceeded",
                        "message": message,
                        "upgrade_url": "/upgrade",
                    }
                ),
                429,
            )
        allowed, message = check_quality_limit(current_user, quality_for_limit)
        if not allowed:
            return (
                jsonify(
                    {
                        "error": "quality_limit",
                        "message": message,
                        "upgrade_url": "/upgrade",
                    }
                ),
                403,
            )

    job.selected_quality = requested_quality
    job.selected_format = data.get("format", job.selected_format)
    job.subtitle_language = data.get("subtitle_language")
    job.subtitle_embed = bool(data.get("subtitle_embed"))
    db.session.commit()

    subtitle_embed_mode = data.get("subtitle_embed_mode")
    task = download_media_task.delay(str(job.id), subtitle_embed_mode)
    job.celery_task_id = task.id
    db.session.commit()

    return jsonify({"job_id": str(job.id), "status": "queued"})


@downloader_bp.route("/download/progress/<job_id>")
def download_progress(job_id):
    def generate():
        media_info_sent = False
        while True:
            db.session.remove()
            try:
                job_uuid = uuid.UUID(job_id)
            except ValueError:
                yield f"data: {json.dumps({'error': 'Invalid job id'})}\n\n"
                break
            job = DownloadJob.query.get(job_uuid)
            if not job:
                yield f"data: {json.dumps({'error': 'Job not found'})}\n\n"
                break
            data = {
                "status": job.status,
                "progress_pct": job.progress_pct or 0,
                "speed_bps": job.speed_bps or 0,
                "eta_seconds": job.eta_seconds or 0,
                "title": job.title,
                "platform": job.platform,
                "content_type": job.content_type,
                "selected_quality": job.selected_quality,
                "selected_format": job.selected_format,
                "thumbnail_url": job.thumbnail_url,
                "file_size_bytes": job.file_size_bytes,
                "download_url": job.download_url if job.status == "complete" else None,
                "error_message": job.error_message if job.status == "failed" else None,
            }
            if job.status == "pending_download" and not media_info_sent:
                try:
                    from app.downloader.tasks import _load_cached_media_info

                    media_info = _load_cached_media_info(job_id)
                except Exception:
                    media_info = None
                if media_info:
                    data["media_info"] = media_info
                    media_info_sent = True
            yield f"data: {json.dumps(data)}\n\n"
            if job.status in ("complete", "failed", "cancelled"):
                break
            time.sleep(1)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@downloader_bp.route("/download/file/<token>")
def serve_file(token):
    verified = storage.verify_signed_url(token)
    if not verified:
        flash("Download link has expired. Please download again.", "warning")
        return redirect(url_for("downloader.download_page"))

    job_id, filename = verified
    job = DownloadJob.query.get(uuid.UUID(job_id))
    if current_user.is_authenticated and job and job.user_id != current_user.id:
        flash("You do not have access to this download.", "danger")
        return redirect(url_for("downloader.download_page"))

    try:
        file_path = storage.get_output_path(job_id, filename)
    except ValueError:
        flash("Download link has expired. Please download again.", "warning")
        return redirect(url_for("downloader.download_page"))

    if not os.path.exists(file_path):
        flash("Download link has expired. Please download again.", "warning")
        return redirect(url_for("downloader.download_page"))

    AuditLog.log(action="download_served", user_id=current_user.id if current_user.is_authenticated else None)

    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    response = send_file(file_path, as_attachment=True, download_name=filename, mimetype=mime_type)
    response.call_on_close(lambda: delete_job_files_task.delay(job_id))
    return response


@downloader_bp.route("/download/cancel/<job_id>", methods=["POST"])
def cancel_download(job_id):
    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError:
        return jsonify({"success": False, "message": "Invalid job"}), 400
    job = DownloadJob.query.get(job_uuid)
    if not job:
        return jsonify({"success": False, "message": "Job not found"}), 404
    if current_user.is_authenticated and job.user_id != current_user.id:
        return jsonify({"success": False, "message": "Not allowed"}), 403
    if job.status in ("queued", "analyzing", "downloading", "converting"):
        if job.celery_task_id:
            from app.celery_app import celery

            celery.control.revoke(job.celery_task_id, terminate=True)
        job.status = "cancelled"
        db.session.commit()
        storage.delete_job_files(job_id)
        return jsonify({"success": True, "message": "Download cancelled"})
    return jsonify({"success": False, "message": "Cannot cancel this job"})


@downloader_bp.route("/download/batch", methods=["GET"])
@login_required
@require_pro
def batch_page():
    max_urls = 5 if not current_user.is_pro() else None
    form = BatchDownloadForm()
    recent_batches = (
        BatchQueue.query.filter_by(user_id=current_user.id)
        .order_by(BatchQueue.created_at.desc())
        .limit(5)
        .all()
    )
    return render_template(
        "downloader/batch.html",
        form=form,
        max_urls=max_urls,
        recent_batches=recent_batches,
    )


@downloader_bp.route("/download/batch/start", methods=["POST"])
@login_required
def start_batch():
    data = request.get_json(silent=True) or {}
    urls = data.get("urls") or []
    default_quality = data.get("default_quality", "best")
    default_format = data.get("default_format", "mp4")
    notify_email = bool(data.get("notify_email"))

    allowed, message = check_batch_limit(current_user, len(urls))
    if not allowed:
        return (
            jsonify({"error": "batch_limit", "message": message, "upgrade_url": "/upgrade"}),
            403,
        )

    batch = BatchQueue(user_id=current_user.id, total_jobs=len(urls), status="queued")
    db.session.add(batch)
    db.session.commit()

    jobs = []
    for url in urls:
        job = DownloadJob(
            user_id=current_user.id,
            batch_id=batch.id,
            url=url,
            status="queued",
            selected_quality=default_quality,
            selected_format=default_format,
        )
        db.session.add(job)
        jobs.append(job)
    db.session.commit()

    task_group = group(chain(analyze_url_task.s(str(job.id)), download_media_task.si(str(job.id))) for job in jobs)
    result = task_group.delay()
    if not batch.name:
        batch.name = result.id
    db.session.commit()

    return jsonify({"batch_id": str(batch.id), "total_jobs": len(urls), "status": "queued"})


@downloader_bp.route("/download/batch/status/<batch_id>")
@login_required
def batch_status(batch_id):
    def generate():
        while True:
            db.session.remove()
            try:
                batch_uuid = uuid.UUID(batch_id)
            except ValueError:
                yield f"data: {json.dumps({'error': 'Invalid batch id'})}\n\n"
                break
            batch = BatchQueue.query.get(batch_uuid)
            if not batch or batch.user_id != current_user.id:
                yield f"data: {json.dumps({'error': 'Batch not found'})}\n\n"
                break
            jobs = (
                DownloadJob.query.filter_by(batch_id=batch.id)
                .order_by(DownloadJob.created_at.asc())
                .all()
            )
            job_data = [
                {
                    "job_id": str(job.id),
                    "title": job.title,
                    "platform": job.platform,
                    "thumbnail_url": job.thumbnail_url,
                    "status": job.status,
                    "progress_pct": job.progress_pct or 0,
                    "speed_bps": job.speed_bps or 0,
                    "eta_seconds": job.eta_seconds or 0,
                    "error_message": job.error_message,
                    "download_url": job.download_url if job.status == "complete" else None,
                    "selected_quality": job.selected_quality,
                    "selected_format": job.selected_format,
                }
                for job in jobs
            ]
            data = {
                "batch_id": str(batch.id),
                "status": batch.status,
                "total_jobs": batch.total_jobs,
                "completed_jobs": batch.completed_jobs,
                "failed_jobs": batch.failed_jobs,
                "overall_pct": batch.progress_percentage(),
                "zip_url": batch.zip_url if batch.status in ("complete", "partial") else None,
                "jobs": job_data,
            }
            yield f"data: {json.dumps(data)}\n\n"
            if batch.status in ("complete", "partial", "cancelled"):
                break
            time.sleep(2)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@downloader_bp.route("/download/batch/zip/<batch_id>")
@login_required
def download_batch_zip(batch_id):
    try:
        batch_uuid = uuid.UUID(batch_id)
    except ValueError:
        flash("Batch not found.", "warning")
        return redirect(url_for("downloader.batch_page"))
    batch = BatchQueue.query.get(batch_uuid)
    if not batch or batch.user_id != current_user.id:
        flash("Batch not found.", "warning")
        return redirect(url_for("downloader.batch_page"))

    if not batch.zip_url or (batch.zip_expires_at and batch.zip_expires_at < datetime.utcnow()):
        job_ids = [str(job.id) for job in DownloadJob.query.filter_by(batch_id=batch.id, status="complete").all()]
        zip_path = storage.create_zip_archive(job_ids, str(batch.id))
        batch.zip_url = f"/download/batch/zip/{batch.id}"
        batch.zip_expires_at = datetime.utcnow() + timedelta(seconds=3600)
        db.session.commit()
    else:
        zip_path = os.path.join(
            current_app.config.get("TEMP_DOWNLOAD_DIR"),
            f"batch_{batch.id}.zip",
        )

    if not os.path.exists(zip_path):
        flash("Batch ZIP not available yet.", "warning")
        return redirect(url_for("downloader.batch_page"))

    return send_file(
        zip_path,
        as_attachment=True,
        download_name=f"universaldl_batch_{str(batch.id)[:8]}.zip",
        mimetype="application/zip",
    )


@downloader_bp.route("/download/info/<job_id>")
def get_job_info(job_id):
    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError:
        return jsonify({"error": "invalid_job_id"}), 400
    job = DownloadJob.query.get(job_uuid)
    if not job:
        return jsonify({"error": "not_found"}), 404
    media_info = None
    try:
        from app.downloader.tasks import _load_cached_media_info

        media_info = _load_cached_media_info(job_id)
    except Exception:
        media_info = None

    job_dict = {
        "job_id": str(job.id),
        "status": job.status,
        "title": job.title,
        "platform": job.platform,
        "thumbnail_url": job.thumbnail_url,
        "selected_quality": job.selected_quality,
        "selected_format": job.selected_format,
        "content_type": job.content_type,
        "progress_pct": job.progress_pct or 0,
        "speed_bps": job.speed_bps or 0,
        "eta_seconds": job.eta_seconds or 0,
        "error_message": job.error_message,
        "download_url": job.download_url if job.status == "complete" else None,
        "file_size_bytes": job.file_size_bytes if job.status == "complete" else None,
    }
    return jsonify({"job": job_dict, "media_info": media_info})
