import functools
import hashlib
import hmac
import time

from flask import current_app, g, jsonify, request

from app.extensions import db
from app.models.api_usage import APIUsage
from app.models.user import User


def get_api_key_from_request():
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header.split(" ", 1)[1].strip()
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return api_key.strip()
    return None


def authenticate_api_key(raw_key: str):
    if not raw_key:
        return None
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    user = User.query.filter_by(api_key_hash=key_hash).first()
    if not user or not user.api_key_hash:
        return None
    if not hmac.compare_digest(user.api_key_hash, key_hash):
        return None
    if user.is_suspended:
        return None
    if not user.is_pro():
        return None
    return user


def require_api_key(view_func):
    @functools.wraps(view_func)
    def wrapper(*args, **kwargs):
        raw_key = get_api_key_from_request()
        g.api_start_time = time.monotonic()
        if not raw_key:
            response = jsonify(
                {
                    "error": "missing_api_key",
                    "message": "Authorization header required. Format: Bearer YOUR_API_KEY",
                }
            )
            response.status_code = 401
            response.headers["WWW-Authenticate"] = "Bearer"
            g.api_raw_key = None
            return response

        user = authenticate_api_key(raw_key)
        if not user:
            response = jsonify(
                {
                    "error": "invalid_api_key",
                    "message": "Invalid or expired API key. Check your key and ensure your plan is active.",
                }
            )
            response.status_code = 401
            response.headers["WWW-Authenticate"] = "Bearer"
            g.api_raw_key = raw_key[:8]
            return response

        g.api_user = user
        g.api_raw_key = raw_key[:8]
        return view_func(*args, **kwargs)

    return wrapper


def log_api_usage(response):
    try:
        start_time = getattr(g, "api_start_time", None)
        response_ms = int((time.monotonic() - start_time) * 1000) if start_time else 0
        raw_key = getattr(g, "api_raw_key", None)
        user = getattr(g, "api_user", None)
        usage = APIUsage(
            user_id=user.id if user else None,
            api_key_prefix=raw_key,
            endpoint=request.endpoint or request.path,
            method=request.method,
            status_code=response.status_code,
            response_ms=response_ms,
        )
        db.session.add(usage)
        db.session.commit()
    except Exception as exc:
        current_app.logger.warning("API usage log failed: %s", exc)
        db.session.rollback()
    return response


def get_api_rate_limit_key():
    raw_key = get_api_key_from_request()
    if raw_key:
        return "api_key:" + raw_key[:8]
    return "ip:" + (request.remote_addr or "unknown")
