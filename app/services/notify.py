import logging
import uuid
from urllib.parse import quote

from flask import current_app, render_template_string
from flask_mail import Message

from app.extensions import mail
from app.models.batch_queue import BatchQueue
from app.models.download_job import DownloadJob
from app.models.subscription import Subscription
from app.models.user import User


logger = logging.getLogger(__name__)


EMAIL_BASE_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0; padding:0; background-color:#0F172A; color:#E2E8F0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;">
  <div style="max-width:640px; margin:0 auto; padding:28px 20px;">
    <div style="font-size:22px; font-weight:700; color:#E94560; letter-spacing:0.3px;">UniversalDL</div>
    <div style="background-color:#111827; border-radius:12px; padding:24px; margin-top:16px;">
      {content}
    </div>
    <div style="font-size:12px; color:#94A3B8; margin-top:18px; line-height:1.6;">
      You are receiving this email because notifications are enabled in your UniversalDL account.
      <br>
      Unsubscribe by disabling email notifications in Settings.
      <br>
    UniversalDL - Universal Media Downloader
    </div>
  </div>
</body>
</html>
"""


DOWNLOAD_COMPLETE_TEMPLATE = """
<h2 style="margin:0 0 12px; font-size:20px; color:#F8FAFC;">Your download is ready</h2>
<p style="margin:0 0 16px; color:#CBD5F5;">Your content is finished and ready in your dashboard.</p>
<div style="background:#0B1220; border:1px solid #1E293B; border-radius:10px; padding:16px; margin-bottom:18px;">
  <div style="font-weight:600; color:#F8FAFC;">{title}</div>
  <div style="font-size:13px; color:#94A3B8; margin-top:6px;">Platform: {platform}</div>
  <div style="font-size:13px; color:#94A3B8; margin-top:6px;">Quality: {quality} | Format: {format}</div>
  <div style="font-size:13px; color:#94A3B8; margin-top:6px;">File size: {size}</div>
</div>
<p style="margin:0 0 16px; color:#CBD5F5;">Your download link is available for 1 hour on the download page.</p>
<a href="{cta_url}" style="display:inline-block; padding:12px 18px; background:#E94560; color:#ffffff; text-decoration:none; border-radius:8px; font-weight:600;">Go to Dashboard</a>
"""


DOWNLOAD_FAILED_TEMPLATE = """
<h2 style="margin:0 0 12px; font-size:20px; color:#F8FAFC;">Download failed</h2>
<p style="margin:0 0 12px; color:#CBD5F5;">We could not complete this download.</p>
<div style="background:#0B1220; border:1px solid #1E293B; border-radius:10px; padding:16px; margin-bottom:18px;">
  <div style="font-weight:600; color:#F8FAFC;">{title}</div>
  <div style="font-size:13px; color:#94A3B8; margin-top:6px;">Platform: {platform}</div>
  <div style="font-size:13px; color:#FCA5A5; margin-top:8px;">{error_message}</div>
</div>
<p style="margin:0 0 12px; color:#CBD5F5;">Suggested next steps</p>
<ul style="margin:0 0 16px; padding-left:18px; color:#94A3B8;">
  <li>Try again in a few minutes</li>
  <li>Check if the URL is still available</li>
  <li>Switch to a different quality or format</li>
</ul>
<a href="{cta_url}" style="display:inline-block; padding:12px 18px; background:#E94560; color:#ffffff; text-decoration:none; border-radius:8px; font-weight:600;">Try Again</a>
"""


BATCH_COMPLETE_TEMPLATE = """
<h2 style="margin:0 0 12px; font-size:20px; color:#F8FAFC;">Batch download complete</h2>
<p style="margin:0 0 16px; color:#CBD5F5;">Your batch is ready. The ZIP download link is available for 1 hour.</p>
<table style="width:100%; border-collapse:collapse; margin-bottom:18px;">
  <tr>
    <td style="padding:8px 0; color:#94A3B8;">Total submitted</td>
    <td style="padding:8px 0; text-align:right; color:#F8FAFC; font-weight:600;">{total}</td>
  </tr>
  <tr>
    <td style="padding:8px 0; color:#94A3B8;">Completed</td>
    <td style="padding:8px 0; text-align:right; color:#34D399; font-weight:600;">{completed}</td>
  </tr>
  <tr>
    <td style="padding:8px 0; color:#94A3B8;">Failed</td>
    <td style="padding:8px 0; text-align:right; color:#FCA5A5; font-weight:600;">{failed}</td>
  </tr>
</table>
{failed_note}
<a href="{cta_url}" style="display:inline-block; padding:12px 18px; background:#E94560; color:#ffffff; text-decoration:none; border-radius:8px; font-weight:600;">Download ZIP</a>
"""


SUBSCRIPTION_NEW_CONTENT_TEMPLATE = """
<h2 style="margin:0 0 12px; font-size:20px; color:#F8FAFC;">New content found</h2>
<p style="margin:0 0 16px; color:#CBD5F5;">We found new items for your subscription and started downloads.</p>
<div style="background:#0B1220; border:1px solid #1E293B; border-radius:10px; padding:16px; margin-bottom:18px;">
  <div style="font-weight:600; color:#F8FAFC;">{channel_name}</div>
  <div style="font-size:13px; color:#94A3B8; margin-top:6px;">Platform: {platform}</div>
  <div style="font-size:13px; color:#94A3B8; margin-top:6px;">{count} new item(s) queued</div>
</div>
<p style="margin:0 0 16px; color:#CBD5F5;">Auto downloads follow your subscription settings.</p>
<a href="{cta_url}" style="display:inline-block; padding:12px 18px; background:#E94560; color:#ffffff; text-decoration:none; border-radius:8px; font-weight:600;">View Downloads</a>
"""


PASSWORD_RESET_TEMPLATE = """
<h2 style="margin:0 0 12px; font-size:20px; color:#F8FAFC;">Reset your UniversalDL password</h2>
<p style="margin:0 0 16px; color:#CBD5F5;">You requested a password reset. Click the button below. This link expires in 1 hour.</p>
<a href="{cta_url}" style="display:inline-block; padding:12px 18px; background:#E94560; color:#ffffff; text-decoration:none; border-radius:8px; font-weight:600;">Reset Password</a>
<p style="margin:16px 0 0; color:#94A3B8;">If you did not request this, ignore this email. Never share this link with anyone.</p>
"""


WELCOME_TEMPLATE = """
<h2 style="margin:0 0 12px; font-size:20px; color:#F8FAFC;">Welcome to UniversalDL</h2>
<p style="margin:0 0 12px; color:#CBD5F5;">Hi {display_name}! Your account is ready.</p>
<p style="margin:0 0 16px; color:#CBD5F5;">Start downloading from 200+ platforms instantly.</p>
<ol style="margin:0 0 16px; padding-left:18px; color:#94A3B8;">
  <li>Paste a URL</li>
  <li>Choose quality and format</li>
  <li>Download in one click</li>
</ol>
<p style="margin:0 0 12px; color:#CBD5F5;">Pro includes batch downloads, channel monitor, and browser extension.</p>
<p style="margin:0 0 18px; color:#94A3B8;">Upgrade any time from ₹799 per month.</p>
<a href="{cta_url}" style="display:inline-block; padding:12px 18px; background:#E94560; color:#ffffff; text-decoration:none; border-radius:8px; font-weight:600;">Start Downloading</a>
"""


def _render_email(content_html: str) -> str:
    html = EMAIL_BASE_TEMPLATE.format(content=content_html)
    return render_template_string(html)


def _truncate(text: str, limit: int = 50) -> str:
    if not text:
        return ""
    return text[:limit]


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


def _clean_error_message(message: str) -> str:
    if not message:
        return "The download could not be completed. Please try again."
    cleaned = " ".join(str(message).split())
    return cleaned[:200]


def _as_uuid(value):
    if isinstance(value, uuid.UUID):
        return value
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def send_email(to: str, subject: str, html_body: str) -> bool:
    if not current_app.config.get("MAIL_USERNAME"):
        logger.debug("Email not sent - MAIL_USERNAME not configured")
        return False
    try:
        msg = Message(subject=subject, recipients=[to], html=html_body)
        mail.send(msg)
        logger.info("Email sent to %s: %s", to, subject)
        return True
    except Exception:
        logger.error("Email send failed for %s: %s", to, subject, exc_info=True)
        return False


def send_download_complete_email(user_id: str, job_id: str):
    user_uuid = _as_uuid(user_id)
    job_uuid = _as_uuid(job_id)
    if not user_uuid or not job_uuid:
        return

    user = User.query.get(user_uuid)
    if not user or not user.email_notifications:
        return
    job = DownloadJob.query.get(job_uuid)
    if not job:
        return
    title = job.title or "Your download"
    subject = "Your download is ready - " + _truncate(title, 50)
    content = DOWNLOAD_COMPLETE_TEMPLATE.format(
        title=title,
        platform=(job.platform or "Unknown").title(),
        quality=(job.selected_quality or "best"),
        format=(job.selected_format or "mp4").upper(),
        size=_format_bytes(job.file_size_bytes),
        cta_url="/dashboard",
    )
    html_body = _render_email(content)
    send_email(user.email, subject, html_body)


def send_download_failed_email(user_id: str, job_id: str, error_message: str):
    user_uuid = _as_uuid(user_id)
    job_uuid = _as_uuid(job_id)
    if not user_uuid or not job_uuid:
        return

    user = User.query.get(user_uuid)
    if not user or not user.email_notifications:
        return
    job = DownloadJob.query.get(job_uuid)
    if not job:
        return
    title = job.title or "Unknown content"
    subject = "Download failed - " + _truncate(title, 50)
    retry_url = "/download"
    if job.url:
        retry_url = "/download?url=" + quote(job.url, safe="")
    content = DOWNLOAD_FAILED_TEMPLATE.format(
        title=title,
        platform=(job.platform or "Unknown").title(),
        error_message=_clean_error_message(error_message),
        cta_url=retry_url,
    )
    html_body = _render_email(content)
    send_email(user.email, subject, html_body)


def send_batch_complete_email(user_id: str, batch_id: str):
    user_uuid = _as_uuid(user_id)
    batch_uuid = _as_uuid(batch_id)
    if not user_uuid or not batch_uuid:
        return

    user = User.query.get(user_uuid)
    if not user or not user.email_notifications:
        return
    batch = BatchQueue.query.get(batch_uuid)
    if not batch:
        return
    completed = batch.completed_jobs or 0
    failed = batch.failed_jobs or 0
    total = batch.total_jobs or 0
    subject = "Batch download complete - " + str(completed) + " files ready"
    failed_note = ""
    if failed > 0:
        failed_note = (
            "<p style=\"margin:0 0 16px; color:#FCA5A5;\">"
            + str(failed)
            + " files failed. Try individual downloads for those items."
            + "</p>"
        )
    content = BATCH_COMPLETE_TEMPLATE.format(
        total=total,
        completed=completed,
        failed=failed,
        failed_note=failed_note,
        cta_url="/download/batch/zip/" + str(batch.id),
    )
    html_body = _render_email(content)
    send_email(user.email, subject, html_body)


def send_subscription_new_content_email(user_id: str, subscription_id: str, new_items_count: int):
    user_uuid = _as_uuid(user_id)
    subscription_uuid = _as_uuid(subscription_id)
    if not user_uuid or not subscription_uuid:
        return

    user = User.query.get(user_uuid)
    if not user or not user.email_notifications:
        return
    sub = Subscription.query.get(subscription_uuid)
    if not sub:
        return
    subject = "New content from " + (sub.channel_name or "your subscription") + " - " + str(new_items_count)
    content = SUBSCRIPTION_NEW_CONTENT_TEMPLATE.format(
        channel_name=sub.channel_name or sub.channel_url,
        platform=(sub.platform or "Unknown").title(),
        count=new_items_count,
        cta_url="/dashboard",
    )
    html_body = _render_email(content)
    send_email(user.email, subject, html_body)


def send_password_reset_email(user_email: str, reset_url: str):
    subject = "Reset your UniversalDL password"
    content = PASSWORD_RESET_TEMPLATE.format(cta_url=reset_url)
    html_body = _render_email(content)
    send_email(user_email, subject, html_body)


def send_welcome_email(user_email: str, display_name: str):
    subject = "Welcome to UniversalDL"
    content = WELCOME_TEMPLATE.format(display_name=display_name or "there", cta_url="/download")
    html_body = _render_email(content)
    send_email(user_email, subject, html_body)


def send_upgrade_confirmation_email(user_email: str, display_name: str, plan_name: str, expires_at):
        """
        Sent after successful Pro plan activation.
        """
        plan_label = "Pro Monthly" if plan_name == "pro_monthly" else "Pro Annual"
        expiry_text = expires_at.strftime("%B %d, %Y") if expires_at else ""
        subject = "Welcome to UniversalDL Pro! 🎉"
        content = """
<h2 style="margin:0 0 12px; font-size:20px; color:#F8FAFC;">Your Pro plan is active</h2>
<p style="margin:0 0 12px; color:#CBD5F5;">Hi {display_name}! Your Pro plan is now active.</p>
<div style="background:#0B1220; border:1px solid #1E293B; border-radius:10px; padding:16px; margin-bottom:18px;">
    <div style="font-weight:600; color:#F8FAFC;">Plan: {plan_label}</div>
    <div style="font-size:13px; color:#94A3B8; margin-top:6px;">Expires: {expiry_text}</div>
</div>
<p style="margin:0 0 12px; color:#CBD5F5;">What is unlocked:</p>
<ul style="margin:0 0 16px; padding-left:18px; color:#94A3B8;">
    <li>8K quality downloads</li>
    <li>10 concurrent downloads</li>
    <li>Unlimited batch downloads</li>
    <li>Channel subscription monitor</li>
    <li>REST API access</li>
    <li>Download history forever</li>
</ul>
<a href="/dashboard" style="display:inline-block; padding:12px 18px; background:#E94560; color:#ffffff; text-decoration:none; border-radius:8px; font-weight:600;">Explore Pro Features</a>
<p style="margin:16px 0 0; color:#94A3B8;">Questions? Email us at support@universaldl.com</p>
<p style="margin:8px 0 0; color:#94A3B8;">Payment processed by Razorpay. Contact your bank for payment receipts.</p>
"""
        html_body = _render_email(
                content.format(display_name=display_name or "there", plan_label=plan_label, expiry_text=expiry_text)
        )
        send_email(user_email, subject, html_body)
