import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timedelta

import razorpay
from flask import current_app


logger = logging.getLogger(__name__)


def get_razorpay_client() -> razorpay.Client:
    key_id = current_app.config.get("RAZORPAY_KEY_ID")
    key_secret = current_app.config.get("RAZORPAY_KEY_SECRET")
    if not key_id or not key_secret:
        raise ValueError(
            "Razorpay credentials not configured. "
            "Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET in .env"
        )
    return razorpay.Client(auth=(key_id, key_secret))


def create_order(amount_paise: int, currency: str = "INR", receipt: str = None, notes: dict = None) -> dict:
    """
    Creates a Razorpay order for payment initiation.
    Amount must be in paise (smallest unit): ₹799 = 79900 paise.
    Returns the full Razorpay order dict on success.
    Raises RuntimeError on Razorpay API failure.
    """
    client = get_razorpay_client()
    receipt = receipt or f"udl_order_{uuid.uuid4().hex[:12]}"
    order_data = {
        "amount": int(amount_paise),
        "currency": currency,
        "receipt": receipt,
        "payment_capture": 1,
        "notes": notes or {},
    }
    try:
        order = client.order.create(data=order_data)
        logger.info("Razorpay order created: %s", order.get("id"))
        return order
    except Exception as exc:
        logger.error("Failed to create Razorpay order", exc_info=True)
        raise RuntimeError("Failed to create payment order: " + str(exc))


def verify_payment_signature(order_id: str, payment_id: str, signature: str) -> bool:
    """
    Verifies the Razorpay payment signature after checkout completion.
    This is CRITICAL for security - never skip this verification.
    Returns True if signature is valid, False if tampered or invalid.
    """
    secret = current_app.config.get("RAZORPAY_KEY_SECRET")
    if not secret:
        logger.error("Razorpay secret key not configured")
        return False
    message = f"{order_id}|{payment_id}"
    expected = hmac.new(
        secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    is_valid = hmac.compare_digest(expected, signature or "")
    if is_valid:
        logger.info("Payment signature verified for order %s", order_id)
    else:
        logger.warning("Invalid payment signature for order %s", order_id)
    return is_valid


def verify_webhook_signature(payload_body: bytes, signature_header: str) -> bool:
    """
    Verifies Razorpay webhook signature from X-Razorpay-Signature header.
    payload_body must be RAW bytes from request.get_data() and not parsed JSON.
    """
    secret = current_app.config.get("RAZORPAY_WEBHOOK_SECRET")
    if not secret:
        logger.warning("Webhook secret not configured")
        return False
    expected = hmac.new(secret.encode("utf-8"), payload_body, hashlib.sha256).hexdigest()
    result = hmac.compare_digest(expected, signature_header or "")
    logger.debug("Webhook signature verification result: %s", result)
    return result


def get_payment_details(payment_id: str) -> dict:
    """
    Fetches payment details from Razorpay API for verification or logging.
    Returns payment dict or empty dict on failure.
    """
    try:
        client = get_razorpay_client()
        return client.payment.fetch(payment_id)
    except Exception:
        logger.error("Failed to fetch payment details", exc_info=True)
        return {}


def get_plan_details(plan_name: str) -> dict:
    """
    Returns plan configuration for the given plan name.
    plan_name: "pro_monthly" or "pro_annual".
    """
    plans = {
        "pro_monthly": {
            "amount_paise": 79900,
            "display_price": "₹799",
            "display_period": "month",
            "plan_duration_days": 30,
            "description": "UniversalDL Pro - Monthly Plan",
        },
        "pro_annual": {
            "amount_paise": 639900,
            "display_price": "₹6,399",
            "display_period": "year",
            "plan_duration_days": 365,
            "description": "UniversalDL Pro - Annual Plan (Save ₹2,189)",
        },
    }
    if plan_name not in plans:
        raise ValueError("Unknown plan: " + str(plan_name))
    return plans[plan_name]


def activate_pro_plan(user, plan_name: str, payment_id: str, order_id: str) -> bool:
    """
    Activates Pro plan for the user after successful payment verification.
    Updates user.plan and user.plan_expires_at in the database.
    Logs the activation to AuditLog.
    Returns True on success, False on failure.
    """
    from app.extensions import db
    from app.models import AuditLog

    plan_details = get_plan_details(plan_name)
    plan_days = plan_details["plan_duration_days"]
    now = datetime.utcnow()
    if user.plan == "pro" and user.plan_expires_at and user.plan_expires_at > now:
        new_expiry = user.plan_expires_at + timedelta(days=plan_days)
    else:
        new_expiry = now + timedelta(days=plan_days)

    user.plan = "pro"
    user.plan_expires_at = new_expiry

    try:
        db.session.commit()
        AuditLog.log(
            action="pro_plan_activated",
            user_id=str(user.id),
            detail_json={
                "plan": plan_name,
                "payment_id": payment_id,
                "order_id": order_id,
                "expires_at": new_expiry.isoformat(),
            },
        )
        logger.info("Pro plan activated for user %s, expires %s", user.email, new_expiry)
        return True
    except Exception:
        db.session.rollback()
        logger.error("Failed to activate pro plan", exc_info=True)
        return False


def format_paise_to_inr(paise: int) -> str:
    """Converts paise integer to formatted INR string. 79900 -> '₹799'"""
    if paise % 100 == 0:
        return f"₹{paise // 100:,}"
    return f"₹{paise / 100:,.2f}"
