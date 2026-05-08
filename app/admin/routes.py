import hashlib
import importlib
import json
import logging
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timedelta
from urllib.parse import urlparse

import redis
from flask import current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload

from app.admin import admin_bp
from app.admin.forms import AdminUserSearchForm, ExtractorTestForm, GlobalSettingsForm
from app.downloader.tasks import download_media_task, extractor_health_check_task, subscription_poll_task
from app.extensions import db
from app.models.api_usage import APIUsage
from app.models.audit_log import AuditLog
from app.models.download_job import DownloadJob
from app.models.extractor import PlatformExtractor
from app.models.subscription import Subscription
from app.models.user import User
from app.services import storage
from app.services.notify import send_email
from app.services.proxy import proxy_pool
from app.celery_app import celery


logger = logging.getLogger(__name__)


def _format_rupees(paise: int) -> str:
    rupees = int(round((paise or 0) / 100))
    return "₹" + f"{rupees:,}"


def _format_bytes(size_bytes: int) -> str:
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


def _mask_url(value: str) -> str:
    if not value:
        return "Not configured"
    parsed = urlparse(value)
    host = parsed.hostname or "localhost"
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path.lstrip("/")
    if path:
        return f"{host}{port}/{path}"
    return f"{host}{port}"


def _get_ffmpeg_version() -> str:
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
            check=False,
        )
        output = result.stdout.splitlines()
        if output:
            return output[0].strip()
    except Exception:
        return "Not found"
    return "Not found"


def _avatar_color(email: str) -> str:
    palette = [
        "#E94560",
        "#3B82F6",
        "#10B981",
        "#F59E0B",
        "#8B5CF6",
        "#14B8A6",
        "#F97316",
        "#EC4899",
    ]
    digest = hashlib.md5((email or "").encode("utf-8")).hexdigest()
    index = int(digest, 16) % len(palette)
    return palette[index]


def _resolve_extractor_class(extractor):
    module_paths = []
    if extractor.extractor_module:
        module_paths.append(extractor.extractor_module)
        if extractor.extractor_module.startswith("app.services.extractors."):
            module_paths.append(
                extractor.extractor_module.replace("app.services.extractors.", "app.extractors.")
            )
    else:
        module_paths.append(f"app.extractors.{extractor.platform_id}")

    for module_path in module_paths:
        try:
            module = importlib.import_module(module_path)
        except Exception:
            continue
        for attr in dir(module):
            obj = getattr(module, attr)
            if isinstance(obj, type) and getattr(obj, "PLATFORM_ID", None) == extractor.platform_id:
                return obj
    return None


@admin_bp.route("/dashboard")
def admin_dashboard():
    today = datetime.utcnow().date()
    yesterday = today - timedelta(days=1)

    downloads_today = (
        db.session.query(func.count(DownloadJob.id))
        .filter(func.date(DownloadJob.created_at) == today, DownloadJob.status == "complete")
        .scalar()
        or 0
    )
    downloads_yesterday = (
        db.session.query(func.count(DownloadJob.id))
        .filter(func.date(DownloadJob.created_at) == yesterday, DownloadJob.status == "complete")
        .scalar()
        or 0
    )
    downloads_change_pct = 0.0
    if downloads_yesterday > 0:
        downloads_change_pct = ((downloads_today - downloads_yesterday) / downloads_yesterday) * 100

    active_jobs_count = (
        DownloadJob.query.filter(
            DownloadJob.status.in_(["queued", "analyzing", "downloading", "converting"])
        ).count()
    )
    failed_jobs_today = (
        DownloadJob.query.filter(
            func.date(DownloadJob.created_at) == today,
            DownloadJob.status == "failed",
        ).count()
    )
    total_jobs_today = DownloadJob.query.filter(func.date(DownloadJob.created_at) == today).count()
    error_rate_today = (failed_jobs_today / total_jobs_today * 100) if total_jobs_today else 0

    total_users = User.query.count()
    new_users_today = User.query.filter(func.date(User.created_at) == today).count()
    pro_users = User.query.filter(
        User.plan.in_(["pro", "enterprise"]),
        or_(User.plan_expires_at.is_(None), User.plan_expires_at > datetime.utcnow()),
    ).count()
    free_users = max(0, total_users - pro_users)

    extractor_status_summary = {"active": 0, "degraded": 0, "down": 0, "disabled": 0}
    for extractor in PlatformExtractor.query.all():
        label = extractor.status_label()
        extractor_status_summary[label] = extractor_status_summary.get(label, 0) + 1

    degraded_extractors = (
        PlatformExtractor.query.filter(
            PlatformExtractor.is_enabled.is_(True),
            PlatformExtractor.success_rate_7d < 90,
        )
        .order_by(PlatformExtractor.success_rate_7d.asc())
        .limit(5)
        .all()
    )
    for extractor in degraded_extractors:
        extractor.default_test_url = None
        if extractor.config_json and isinstance(extractor.config_json, dict):
            extractor.default_test_url = extractor.config_json.get("test_url") or extractor.config_json.get("TEST_URL")
        if not extractor.default_test_url:
            extractor_cls = _resolve_extractor_class(extractor)
            if extractor_cls and getattr(extractor_cls, "TEST_URL", None):
                extractor.default_test_url = extractor_cls.TEST_URL

    start_date = today - timedelta(days=29)
    download_date_expr = func.date(DownloadJob.created_at)
    download_rows = (
        db.session.query(
            download_date_expr.label("date"),
            func.count(DownloadJob.id).label("count"),
        )
        .filter(DownloadJob.status == "complete", DownloadJob.created_at >= start_date)
        .group_by(download_date_expr)
        .order_by(download_date_expr)
        .all()
    )
    download_map = {str(row.date): row.count for row in download_rows}
    daily_download_labels = []
    daily_download_counts = []
    for offset in range(30):
        day = start_date + timedelta(days=offset)
        daily_download_labels.append(day.strftime("%b %d"))
        daily_download_counts.append(int(download_map.get(day.isoformat(), 0)))

    user_date_expr = func.date(User.created_at)
    user_rows = (
        db.session.query(
            user_date_expr.label("date"),
            func.count(User.id).label("count"),
        )
        .filter(User.created_at >= start_date)
        .group_by(user_date_expr)
        .order_by(user_date_expr)
        .all()
    )
    user_map = {str(row.date): row.count for row in user_rows}
    daily_user_labels = []
    daily_user_counts = []
    for offset in range(30):
        day = start_date + timedelta(days=offset)
        daily_user_labels.append(day.strftime("%b %d"))
        daily_user_counts.append(int(user_map.get(day.isoformat(), 0)))

    platform_rows = (
        db.session.query(DownloadJob.platform, func.count(DownloadJob.id).label("count"))
        .filter(DownloadJob.status == "complete", DownloadJob.platform.isnot(None))
        .group_by(DownloadJob.platform)
        .order_by(func.count(DownloadJob.id).desc())
        .limit(10)
        .all()
    )
    platform_labels = [row.platform.title() if row.platform else "Unknown" for row in platform_rows]
    platform_counts = [int(row.count) for row in platform_rows]

    recent_failed_jobs = (
        DownloadJob.query.options(joinedload(DownloadJob.user))
        .filter(DownloadJob.status == "failed")
        .order_by(DownloadJob.created_at.desc())
        .limit(10)
        .all()
    )

    estimated_mrr_paise = pro_users * 79900
    estimated_mrr_display = _format_rupees(estimated_mrr_paise)

    return render_template(
        "admin/dashboard.html",
        downloads_today=downloads_today,
        downloads_change_pct=downloads_change_pct,
        active_jobs_count=active_jobs_count,
        error_rate_today=round(error_rate_today, 1),
        total_jobs_today=total_jobs_today,
        total_users=total_users,
        new_users_today=new_users_today,
        pro_users=pro_users,
        free_users=free_users,
        estimated_mrr=estimated_mrr_display,
        extractor_status_summary=extractor_status_summary,
        degraded_extractors=degraded_extractors,
        recent_failed_jobs=recent_failed_jobs,
        daily_download_labels=json.dumps(daily_download_labels),
        daily_download_counts=json.dumps(daily_download_counts),
        daily_user_labels=json.dumps(daily_user_labels),
        daily_user_counts=json.dumps(daily_user_counts),
        platform_labels=json.dumps(platform_labels),
        platform_counts=json.dumps(platform_counts),
    )


@admin_bp.route("/extractors")
def admin_extractors():
    all_extractors = PlatformExtractor.query.order_by(PlatformExtractor.display_name).all()
    enabled = [item for item in all_extractors if item.is_enabled]
    disabled = [item for item in all_extractors if not item.is_enabled]

    cutoff = datetime.utcnow() - timedelta(hours=24)
    for extractor in all_extractors:
        extractor.status_label_value = extractor.status_label()
        extractor.status_color_value = extractor.status_color()
        extractor.recent_failures = (
            DownloadJob.query.filter(
                DownloadJob.platform == extractor.platform_id,
                DownloadJob.status == "failed",
                DownloadJob.created_at >= cutoff,
            ).count()
        )
        extractor.default_test_url = None
        if extractor.config_json and isinstance(extractor.config_json, dict):
            extractor.default_test_url = extractor.config_json.get("test_url") or extractor.config_json.get("TEST_URL")
        if not extractor.default_test_url:
            extractor_cls = _resolve_extractor_class(extractor)
            if extractor_cls and getattr(extractor_cls, "TEST_URL", None):
                extractor.default_test_url = extractor_cls.TEST_URL

    return render_template(
        "admin/extractors.html",
        extractors=all_extractors,
        enabled_count=len(enabled),
        disabled_count=len(disabled),
        test_form=ExtractorTestForm(),
    )


@admin_bp.route("/extractors/<extractor_id>/toggle", methods=["POST"])
def toggle_extractor(extractor_id: str):
    try:
        extractor_uuid = uuid.UUID(extractor_id)
    except ValueError:
        return jsonify({"success": False, "message": "Invalid extractor"}), 404

    extractor = PlatformExtractor.query.get(extractor_uuid)
    if not extractor:
        return jsonify({"success": False, "message": "Extractor not found"}), 404

    extractor.is_enabled = not extractor.is_enabled
    extractor.updated_at = datetime.utcnow()
    db.session.commit()

    AuditLog.log(
        "extractor_toggle",
        user_id=str(current_user.id),
        detail_json={"platform_id": extractor.platform_id, "enabled": extractor.is_enabled},
    )

    return jsonify(
        {
            "success": True,
            "is_enabled": extractor.is_enabled,
            "platform_id": extractor.platform_id,
            "message": f"{extractor.display_name} {'enabled' if extractor.is_enabled else 'disabled'}",
        }
    )


@admin_bp.route("/extractors/<extractor_id>/test", methods=["POST"])
def test_extractor(extractor_id: str):
    try:
        extractor_uuid = uuid.UUID(extractor_id)
    except ValueError:
        return jsonify({"success": False, "message": "Invalid extractor"}), 404

    extractor = PlatformExtractor.query.get(extractor_uuid)
    if not extractor:
        return jsonify({"success": False, "message": "Extractor not found"}), 404

    payload = request.get_json(silent=True) or {}
    test_url = (payload.get("test_url") or request.form.get("test_url") or "").strip()

    if not test_url and extractor.config_json:
        test_url = (extractor.config_json.get("test_url") or extractor.config_json.get("TEST_URL") or "").strip()

    if not test_url:
        extractor_cls = _resolve_extractor_class(extractor)
        if extractor_cls and getattr(extractor_cls, "TEST_URL", None):
            test_url = extractor_cls.TEST_URL

    if not test_url:
        return jsonify({"success": False, "message": "No test URL configured"}), 200

    try:
        extractor_cls = _resolve_extractor_class(extractor)
        if not extractor_cls:
            raise RuntimeError("Extractor class not found")

        extractor_instance = extractor_cls(proxy_pool=proxy_pool)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(extractor_instance.extract, test_url)
            media_info = future.result(timeout=30)

        extractor.last_success_at = datetime.utcnow()
        extractor.failure_reason = None
        db.session.commit()

        return jsonify(
            {
                "success": True,
                "result": {
                    "title": media_info.get("title", "Unknown"),
                    "platform": extractor.platform_id,
                    "qualities_count": len(media_info.get("qualities", [])),
                    "has_subtitles": len(media_info.get("subtitles", [])) > 0,
                    "duration": media_info.get("duration"),
                    "test_url": test_url,
                },
                "message": f"{extractor.display_name} test passed!",
            }
        )
    except FuturesTimeoutError:
        extractor.last_failure_at = datetime.utcnow()
        extractor.failure_reason = "Test timed out"
        db.session.commit()
        return jsonify(
            {
                "success": False,
                "error": "Test timed out",
                "message": f"{extractor.display_name} test FAILED: Test timed out",
            }
        ), 200
    except Exception as exc:
        extractor.last_failure_at = datetime.utcnow()
        extractor.failure_reason = str(exc)[:500]
        db.session.commit()
        return jsonify(
            {
                "success": False,
                "error": str(exc),
                "message": f"{extractor.display_name} test FAILED: {str(exc)[:200]}",
            }
        ), 200


@admin_bp.route("/extractors/status-summary")
def extractor_status_summary():
    summary = {"active": 0, "degraded": 0, "down": 0, "disabled": 0}
    for extractor in PlatformExtractor.query.all():
        label = extractor.status_label()
        summary[label] = summary.get(label, 0) + 1
    return jsonify(summary)


@admin_bp.route("/users")
def admin_users():
    form = AdminUserSearchForm(request.args)
    base_query = User.query

    search = (form.search.data or "").strip()
    if search:
        search_value = "%" + search.lower() + "%"
        base_query = base_query.filter(
            or_(func.lower(User.email).like(search_value), func.lower(User.display_name).like(search_value))
        )

    if form.plan_filter.data:
        base_query = base_query.filter(User.plan == form.plan_filter.data)

    if form.status_filter.data == "suspended":
        base_query = base_query.filter(User.is_suspended.is_(True))
    elif form.status_filter.data == "active":
        base_query = base_query.filter(User.is_suspended.is_(False))
    elif form.status_filter.data == "admin":
        base_query = base_query.filter(User.is_admin.is_(True))

    if form.date_from.data:
        date_from = datetime.combine(form.date_from.data, datetime.min.time())
        base_query = base_query.filter(User.created_at >= date_from)

    page = request.args.get("page", 1, type=int)
    pagination = base_query.order_by(User.created_at.desc()).paginate(page=page, per_page=25, error_out=False)

    pro_user_count = User.query.filter(
        User.plan.in_(["pro", "enterprise"]),
        or_(User.plan_expires_at.is_(None), User.plan_expires_at > datetime.utcnow()),
    ).count()
    suspended_count = User.query.filter(User.is_suspended.is_(True)).count()

    for user in pagination.items:
        user.download_count = (
            DownloadJob.query.filter_by(user_id=user.id, status="complete").count()
        )
        last_job = (
            DownloadJob.query.filter_by(user_id=user.id)
            .order_by(DownloadJob.created_at.desc())
            .first()
        )
        last_download = last_job.created_at if last_job else None
        last_login = user.last_login_at
        user.last_active_at = max([value for value in [last_download, last_login] if value] or [None])
        user.total_data_bytes = (
            db.session.query(func.coalesce(func.sum(DownloadJob.file_size_bytes), 0))
            .filter(DownloadJob.user_id == user.id, DownloadJob.status == "complete")
            .scalar()
            or 0
        )
        user.avatar_color = _avatar_color(user.email)

    return render_template(
        "admin/users.html",
        users=pagination.items,
        pagination=pagination,
        form=form,
        total_users=User.query.count(),
        pro_users=pro_user_count,
        suspended_users=suspended_count,
    )


@admin_bp.route("/users/<user_id>/suspend", methods=["POST"])
def suspend_user(user_id: str):
    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        return jsonify({"success": False, "message": "Invalid user"}), 404

    user = User.query.get(user_uuid)
    if not user:
        return jsonify({"success": False, "message": "User not found"}), 404
    if user.id == current_user.id:
        return jsonify({"success": False, "message": "You cannot suspend your own account"}), 400
    if user.is_admin:
        return jsonify({"success": False, "message": "Cannot suspend another admin"}), 400

    user.is_suspended = True
    db.session.commit()

    AuditLog.log(
        "admin_suspend_user",
        user_id=str(current_user.id),
        resource_id=uuid.UUID(user_id),
        detail_json={"target_email": user.email},
    )

    return jsonify({"success": True, "message": f"{user.email} has been suspended"})


@admin_bp.route("/users/<user_id>/unsuspend", methods=["POST"])
def unsuspend_user(user_id: str):
    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        return jsonify({"success": False, "message": "Invalid user"}), 404

    user = User.query.get(user_uuid)
    if not user:
        return jsonify({"success": False, "message": "User not found"}), 404

    user.is_suspended = False
    db.session.commit()

    AuditLog.log(
        "admin_unsuspend_user",
        user_id=str(current_user.id),
        resource_id=uuid.UUID(user_id),
        detail_json={"target_email": user.email},
    )

    return jsonify({"success": True, "message": f"{user.email} has been unsuspended"})


@admin_bp.route("/users/<user_id>/grant-pro", methods=["POST"])
def grant_pro(user_id: str):
    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        return jsonify({"success": False, "message": "Invalid user"}), 404

    user = User.query.get(user_uuid)
    if not user:
        return jsonify({"success": False, "message": "User not found"}), 404

    payload = request.get_json(silent=True) or {}
    duration_days = int(payload.get("duration_days", 365))
    if duration_days < 1:
        duration_days = 365

    user.plan = "pro"
    user.plan_expires_at = datetime.utcnow() + timedelta(days=duration_days)
    db.session.commit()

    AuditLog.log(
        "admin_grant_pro",
        user_id=str(current_user.id),
        resource_id=user.id,
        detail_json={
            "target_email": user.email,
            "duration_days": duration_days,
            "expires_at": user.plan_expires_at.isoformat(),
        },
    )

    return jsonify(
        {
            "success": True,
            "message": f"Pro granted to {user.email} for {duration_days} days",
            "expires_at": user.plan_expires_at.isoformat(),
        }
    )


@admin_bp.route("/users/<user_id>/revoke-pro", methods=["POST"])
def revoke_pro(user_id: str):
    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        return jsonify({"success": False, "message": "Invalid user"}), 404

    user = User.query.get(user_uuid)
    if not user:
        return jsonify({"success": False, "message": "User not found"}), 404

    user.plan = "free"
    user.plan_expires_at = None
    db.session.commit()

    AuditLog.log(
        "admin_revoke_pro",
        user_id=str(current_user.id),
        resource_id=user.id,
        detail_json={"target_email": user.email},
    )

    return jsonify({"success": True, "message": f"Pro revoked from {user.email}"})


@admin_bp.route("/users/<user_id>/profile")
def admin_user_profile(user_id: str):
    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        return jsonify({"success": False, "message": "Invalid user"}), 404

    user = User.query.get(user_uuid)
    if not user:
        return jsonify({"success": False, "message": "User not found"}), 404

    total_downloads = DownloadJob.query.filter_by(user_id=user.id, status="complete").count()
    total_data = (
        db.session.query(func.coalesce(func.sum(DownloadJob.file_size_bytes), 0))
        .filter(DownloadJob.user_id == user.id, DownloadJob.status == "complete")
        .scalar()
        or 0
    )
    last_download = (
        DownloadJob.query.filter_by(user_id=user.id).order_by(DownloadJob.created_at.desc()).first()
    )
    sub_count = Subscription.query.filter_by(user_id=user.id).count()
    api_count = APIUsage.query.filter_by(user_id=user.id).count()

    return jsonify(
        {
            "user_id": str(user.id),
            "email": user.email,
            "display_name": user.display_name,
            "plan": user.plan,
            "plan_expires_at": user.plan_expires_at.isoformat() if user.plan_expires_at else None,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
            "is_admin": user.is_admin,
            "is_suspended": user.is_suspended,
            "download_stats": {
                "total_downloads": total_downloads,
                "total_data_bytes": total_data,
                "last_download_at": last_download.created_at.isoformat() if last_download else None,
            },
            "subscription_count": sub_count,
            "api_usage_count": api_count,
        }
    )


@admin_bp.route("/queue")
def admin_queue():
    inspector = celery.control.inspect(timeout=3.0)
    celery_available = True
    try:
        active_tasks = inspector.active() or {}
        reserved_tasks = inspector.reserved() or {}
        stats = inspector.stats() or {}
    except Exception:
        active_tasks = {}
        reserved_tasks = {}
        stats = {}
        celery_available = False

    def _normalize_worker_stats(raw_stats):
        normalized = {}
        if not isinstance(raw_stats, dict):
            return normalized
        for worker_name, worker_stats in (raw_stats or {}).items():
            worker_stats = worker_stats if isinstance(worker_stats, dict) else {}

            raw_pool = worker_stats.get("pool")
            if isinstance(raw_pool, dict):
                pool = raw_pool
            elif isinstance(raw_pool, str):
                pool = {"name": raw_pool}
            else:
                pool = {}

            raw_total = worker_stats.get("total")
            if isinstance(raw_total, dict):
                total = raw_total
            elif raw_total is not None:
                total = {"processed": raw_total}
            else:
                total = {}

            raw_rusage = worker_stats.get("rusage")
            rusage = raw_rusage if isinstance(raw_rusage, dict) else {}

            normalized_worker_stats = dict(worker_stats)
            normalized_worker_stats["pool"] = pool
            normalized_worker_stats["total"] = total
            normalized_worker_stats["rusage"] = rusage
            normalized[worker_name] = normalized_worker_stats
        return normalized

    def _flatten(task_map):
        items = []
        for worker, tasks in (task_map or {}).items():
            for task in tasks or []:
                task["worker"] = worker
                task_id = task.get("id") or task.get("uuid")
                task["task_id"] = task_id
                time_start = task.get("time_start")
                if time_start:
                    try:
                        task["time_start_dt"] = datetime.utcfromtimestamp(time_start)
                    except Exception:
                        task["time_start_dt"] = None
                items.append(task)
        return items

    flattened_active = _flatten(active_tasks)
    flattened_reserved = _flatten(reserved_tasks)

    task_ids = [task.get("task_id") for task in flattened_active + flattened_reserved if task.get("task_id")]
    job_map = {}
    if task_ids:
        jobs = DownloadJob.query.filter(DownloadJob.celery_task_id.in_(task_ids)).all()
        job_map = {job.celery_task_id: job for job in jobs}

    for task in flattened_active + flattened_reserved:
        task_id = task.get("task_id")
        if task_id and task_id in job_map:
            task["job"] = job_map[task_id]

    recent_failed = (
        DownloadJob.query.options(joinedload(DownloadJob.user))
        .filter_by(status="failed")
        .order_by(DownloadJob.created_at.desc())
        .limit(20)
        .all()
    )

    redis_client = current_app.extensions.get("redis")
    if not redis_client:
        redis_client = redis.Redis.from_url(current_app.config.get("REDIS_URL"), decode_responses=True)
    try:
        downloads_queue_depth = redis_client.llen("downloads")
        convert_queue_depth = redis_client.llen("convert")
    except Exception:
        downloads_queue_depth = 0
        convert_queue_depth = 0

    return render_template(
        "admin/queue.html",
        active_tasks=flattened_active,
        reserved_tasks=flattened_reserved,
        worker_stats=_normalize_worker_stats(stats),
        downloads_queue_depth=downloads_queue_depth,
        convert_queue_depth=convert_queue_depth,
        recent_failed_jobs=recent_failed,
        celery_available=celery_available,
    )


@admin_bp.route("/queue/retry/<task_id_or_job_id>", methods=["POST"])
def retry_task(task_id_or_job_id: str):
    try:
        job_uuid = uuid.UUID(task_id_or_job_id)
        job = DownloadJob.query.get(job_uuid)
    except ValueError:
        job = None

    if job:
        job.status = "queued"
        job.error_message = None
        db.session.commit()
        task = download_media_task.delay(str(job.id))
        job.celery_task_id = task.id
        db.session.commit()
        return jsonify({"success": True, "message": "Job requeued"})

    return jsonify(
        {
            "success": False,
            "message": "Cannot retry this task directly. Use the re-download feature.",
        }
    )


@admin_bp.route("/queue/cancel/<job_id>", methods=["POST"])
def cancel_admin_job(job_id: str):
    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError:
        return jsonify({"success": False, "message": "Invalid job"}), 404

    job = DownloadJob.query.get(job_uuid)
    if not job:
        return jsonify({"success": False, "message": "Job not found"}), 404

    if job.celery_task_id:
        celery.control.revoke(job.celery_task_id, terminate=True)

    job.status = "cancelled"
    db.session.commit()
    storage.delete_job_files(str(job.id))

    return jsonify({"success": True, "message": "Job cancelled"})


@admin_bp.route("/queue/stats")
def queue_stats_json():
    inspector = celery.control.inspect(timeout=3.0)
    try:
        active_tasks = inspector.active() or {}
        reserved_tasks = inspector.reserved() or {}
    except Exception:
        active_tasks = {}
        reserved_tasks = {}

    active_count = sum(len(tasks or []) for tasks in (active_tasks or {}).values())
    reserved_count = sum(len(tasks or []) for tasks in (reserved_tasks or {}).values())

    redis_client = current_app.extensions.get("redis")
    if not redis_client:
        redis_client = redis.Redis.from_url(current_app.config.get("REDIS_URL"), decode_responses=True)
    try:
        downloads_depth = redis_client.llen("downloads")
        convert_depth = redis_client.llen("convert")
    except Exception:
        downloads_depth = 0
        convert_depth = 0

    return jsonify(
        {
            "active_count": active_count,
            "reserved_count": reserved_count,
            "downloads_depth": downloads_depth,
            "convert_depth": convert_depth,
        }
    )


@admin_bp.route("/logs")
def admin_logs():
    action_filter = (request.args.get("action") or "").strip()
    user_search = (request.args.get("user_search") or "").strip()
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")
    ip_filter = (request.args.get("ip_filter") or "").strip()

    distinct_actions = [
        row.action
        for row in db.session.query(AuditLog.action).distinct().order_by(AuditLog.action).all()
    ]

    base_query = AuditLog.query.join(User, AuditLog.user_id == User.id, isouter=True)
    base_query = base_query.options(joinedload(AuditLog.user))

    if action_filter:
        base_query = base_query.filter(AuditLog.action == action_filter)
    if user_search:
        base_query = base_query.filter(User.email.ilike("%" + user_search + "%"))
    if date_from:
        try:
            start_date = datetime.strptime(date_from, "%Y-%m-%d")
            base_query = base_query.filter(AuditLog.created_at >= start_date)
        except ValueError:
            pass
    if date_to:
        try:
            end_date = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
            base_query = base_query.filter(AuditLog.created_at < end_date)
        except ValueError:
            pass
    if ip_filter:
        base_query = base_query.filter(AuditLog.ip_address.ilike("%" + ip_filter + "%"))

    action_breakdown = (
        base_query.with_entities(AuditLog.action, func.count(AuditLog.id).label("count"))
        .group_by(AuditLog.action)
        .order_by(func.count(AuditLog.id).desc())
        .limit(10)
        .all()
    )

    page = request.args.get("page", 1, type=int)
    pagination = base_query.order_by(AuditLog.created_at.desc()).paginate(page=page, per_page=50, error_out=False)

    for log in pagination.items:
        ip = log.ip_address or ""
        if ip.count(".") == 3:
            parts = ip.split(".")
            log.masked_ip = "{}.{}.{}.***".format(parts[0], parts[1], parts[2])
        else:
            log.masked_ip = ip if ip else "-"
        log.badge_class = _action_badge_class(log.action)

    return render_template(
        "admin/logs.html",
        logs=pagination.items,
        pagination=pagination,
        action_types=distinct_actions,
        action_breakdown=action_breakdown,
        total_matching=pagination.total,
        current_filters=dict(request.args),
    )


def _action_badge_class(action: str) -> str:
    value = (action or "").lower()
    if any(token in value for token in ["login", "register", "password", "auth"]):
        return "action-badge-auth"
    if "delete" in value:
        return "action-badge-delete"
    if "admin" in value:
        return "action-badge-admin"
    if "payment" in value or "upgrade" in value:
        return "action-badge-payment"
    if "download" in value or "batch" in value:
        return "action-badge-download"
    if "security" in value or "suspend" in value:
        return "action-badge-security"
    return "action-badge-download"


@admin_bp.route("/logs/<log_id>/detail")
def log_detail(log_id: str):
    try:
        log_uuid = uuid.UUID(log_id)
    except ValueError:
        return jsonify({"success": False, "message": "Invalid log"}), 404

    log = AuditLog.query.get(log_uuid)
    if not log:
        return jsonify({"success": False, "message": "Log not found"}), 404

    return jsonify(
        {
            "log_id": str(log.id),
            "action": log.action,
            "user_email": log.user.email if log.user else None,
            "ip_address": log.ip_address,
            "resource_id": str(log.resource_id) if log.resource_id else None,
            "detail_json": log.detail_json,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
    )


@admin_bp.route("/settings", methods=["GET", "POST"])
def admin_settings():
    config = current_app.config
    form = GlobalSettingsForm()

    if request.method == "POST" and form.validate_on_submit():
        old_values = {
            "MAX_CONCURRENT_FREE": config.get("MAX_CONCURRENT_FREE"),
            "MAX_CONCURRENT_PRO": config.get("MAX_CONCURRENT_PRO"),
            "MAX_BATCH_URLS_FREE": config.get("MAX_BATCH_URLS_FREE"),
            "HISTORY_RETENTION_DAYS_FREE": config.get("HISTORY_RETENTION_DAYS_FREE"),
            "MAX_FILE_AGE_SECONDS": config.get("MAX_FILE_AGE_SECONDS"),
            "FFMPEG_PATH": config.get("FFMPEG_PATH"),
        }

        config["MAX_CONCURRENT_FREE"] = form.max_concurrent_free.data
        config["MAX_CONCURRENT_PRO"] = form.max_concurrent_pro.data
        config["MAX_BATCH_URLS_FREE"] = form.max_batch_urls_free.data
        config["HISTORY_RETENTION_DAYS_FREE"] = form.history_retention_days_free.data
        config["MAX_FILE_AGE_SECONDS"] = form.max_file_age_seconds.data
        config["FFMPEG_PATH"] = form.ffmpeg_path.data or None
        config["TEMP_DOWNLOAD_DIR"] = form.temp_download_dir.data or config.get("TEMP_DOWNLOAD_DIR")

        AuditLog.log(
            "admin_settings_update",
            user_id=str(current_user.id),
            detail_json={"old": old_values, "new": {
                "MAX_CONCURRENT_FREE": config.get("MAX_CONCURRENT_FREE"),
                "MAX_CONCURRENT_PRO": config.get("MAX_CONCURRENT_PRO"),
                "MAX_BATCH_URLS_FREE": config.get("MAX_BATCH_URLS_FREE"),
                "HISTORY_RETENTION_DAYS_FREE": config.get("HISTORY_RETENTION_DAYS_FREE"),
                "MAX_FILE_AGE_SECONDS": config.get("MAX_FILE_AGE_SECONDS"),
                "FFMPEG_PATH": config.get("FFMPEG_PATH"),
                "TEMP_DOWNLOAD_DIR": config.get("TEMP_DOWNLOAD_DIR"),
            }},
        )

        flash(
            "Settings updated for current session. To persist settings, update the .env file.",
            "warning",
        )
        return redirect(url_for("admin.admin_settings"))

    if request.method == "GET":
        form.max_concurrent_free.data = config.get("MAX_CONCURRENT_FREE", form.max_concurrent_free.data)
        form.max_concurrent_pro.data = config.get("MAX_CONCURRENT_PRO", form.max_concurrent_pro.data)
        form.max_batch_urls_free.data = config.get("MAX_BATCH_URLS_FREE", form.max_batch_urls_free.data)
        form.history_retention_days_free.data = config.get(
            "HISTORY_RETENTION_DAYS_FREE", form.history_retention_days_free.data
        )
        form.ffmpeg_path.data = config.get("FFMPEG_PATH", "")
        form.temp_download_dir.data = config.get("TEMP_DOWNLOAD_DIR", "")
        form.max_file_age_seconds.data = config.get("MAX_FILE_AGE_SECONDS", form.max_file_age_seconds.data)

    proxy_total = len(proxy_pool.proxies)
    proxy_failed = len(proxy_pool.failed_proxies)
    proxy_healthy = max(0, proxy_total - proxy_failed)

    extractor_total = PlatformExtractor.query.count()
    extractor_enabled = PlatformExtractor.query.filter(PlatformExtractor.is_enabled.is_(True)).count()
    extractor_disabled = PlatformExtractor.query.filter(PlatformExtractor.is_enabled.is_(False)).count()
    extractor_degraded = PlatformExtractor.query.filter(
        PlatformExtractor.is_enabled.is_(True), PlatformExtractor.success_rate_7d < 90
    ).count()

    temp_dir_size = _format_bytes(storage.get_temp_dir_size())

    redis_client = current_app.extensions.get("redis")
    if not redis_client:
        redis_client = redis.Redis.from_url(config.get("REDIS_URL"), decode_responses=True)
    try:
        redis_client.ping()
        redis_ok = True
    except Exception:
        redis_ok = False

    playwright_installed = False
    try:
        import playwright  # noqa: F401

        playwright_installed = True
    except Exception:
        playwright_installed = False

    return render_template(
        "admin/settings.html",
        form=form,
        temp_dir_size=temp_dir_size,
        temp_dir_path=config.get("TEMP_DOWNLOAD_DIR"),
        proxy_total=proxy_total,
        proxy_healthy=proxy_healthy,
        proxy_failed=proxy_failed,
        proxy_provider_configured=bool(config.get("PROXY_PROVIDER_URL")),
        extractor_total=extractor_total,
        extractor_enabled=extractor_enabled,
        extractor_disabled=extractor_disabled,
        extractor_degraded=extractor_degraded,
        flask_env=config.get("FLASK_ENV"),
        app_version=config.get("APP_VERSION"),
        db_display=_mask_url(config.get("DATABASE_URL")),
        redis_display=_mask_url(config.get("REDIS_URL")),
        broker_display=_mask_url(config.get("CELERY_BROKER_URL")),
        ffmpeg_version=_get_ffmpeg_version(),
        sentry_configured=bool(config.get("SENTRY_DSN")),
        python_version="{}.{}.{}".format(*tuple(__import__("sys").version_info[:3])),
        redis_ok=redis_ok,
        playwright_installed=playwright_installed,
        mail_server=config.get("MAIL_SERVER"),
        mail_username=config.get("MAIL_USERNAME"),
    )


@admin_bp.route("/settings/reload-proxies", methods=["POST"])
def reload_proxies():
    proxy_pool.load_proxies()
    return jsonify(
        {
            "success": True,
            "proxy_count": len(proxy_pool.proxies),
            "message": f"Proxy pool reloaded: {len(proxy_pool.proxies)} proxies",
        }
    )


@admin_bp.route("/settings/test-email", methods=["POST"])
def test_email():
    subject = "UniversalDL - Test Email"
    body = "This is a test email from the UniversalDL admin panel."
    html = f"<p>{body}</p>"
    success = send_email(current_user.email, subject, html)
    if success:
        return jsonify({"success": True, "message": "Test email sent"})
    return jsonify({"success": False, "message": "Failed to send test email"}), 500


@admin_bp.route("/settings/clear-cache", methods=["POST"])
def clear_cache():
    redis_client = current_app.extensions.get("redis")
    if not redis_client:
        redis_client = redis.Redis.from_url(current_app.config.get("REDIS_URL"), decode_responses=True)

    count = 0
    for key in redis_client.scan_iter(match="media_info:*"):
        redis_client.delete(key)
        count += 1

    AuditLog.log(
        "admin_cache_clear",
        user_id=str(current_user.id),
        detail_json={"keys_cleared": count},
    )

    return jsonify({"success": True, "message": f"Cleared {count} cached analysis entries"})


@admin_bp.route("/settings/clear-temp", methods=["POST"])
def clear_temp_files():
    deleted = storage.purge_expired_files(max_age_seconds=0)
    return jsonify({"success": True, "message": f"Cleared {deleted} temp file folders"})


@admin_bp.route("/settings/trigger-health-check", methods=["POST"])
def trigger_health_check():
    extractor_health_check_task.delay()
    AuditLog.log("admin_trigger_health_check", user_id=str(current_user.id))
    return jsonify(
        {
            "success": True,
            "message": "Health check task dispatched. Results in ~5 minutes.",
        }
    )


@admin_bp.route("/settings/trigger-sub-poll", methods=["POST"])
def trigger_sub_poll():
    subscription_poll_task.delay()
    AuditLog.log("admin_trigger_sub_poll", user_id=str(current_user.id))
    return jsonify({"success": True, "message": "Subscription poll task dispatched."})


@admin_bp.route("/settings/detect-ffmpeg", methods=["POST"])
def detect_ffmpeg():
    version = _get_ffmpeg_version()
    return jsonify({"success": True, "version": version})
