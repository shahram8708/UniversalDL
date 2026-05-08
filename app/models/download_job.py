import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db


class DownloadJob(db.Model):
    __tablename__ = "download_jobs"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), nullable=True, index=True)
    url = db.Column(db.Text, nullable=True)
    platform = db.Column(db.String(50), nullable=True)
    content_type = db.Column(
        db.Enum("video", "audio", "image", "document", "post", "unknown", name="content_type_enum"),
        nullable=True,
    )
    title = db.Column(db.Text, nullable=True)
    thumbnail_url = db.Column(db.Text, nullable=True)
    selected_quality = db.Column(db.String(20), nullable=True)
    selected_format = db.Column(db.String(10), nullable=True)
    subtitle_language = db.Column(db.String(10), nullable=True)
    subtitle_embed = db.Column(db.Boolean, default=False)
    status = db.Column(
        db.Enum(
            "queued",
            "analyzing",
            "pending_download",
            "downloading",
            "converting",
            "complete",
            "failed",
            "cancelled",
            name="download_status_enum",
        ),
        default="queued",
    )
    progress_pct = db.Column(db.SmallInteger, default=0)
    speed_bps = db.Column(db.BigInteger, nullable=True)
    eta_seconds = db.Column(db.Integer, nullable=True)
    file_size_bytes = db.Column(db.BigInteger, nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    celery_task_id = db.Column(db.String(255), nullable=True)
    download_url = db.Column(db.Text, nullable=True)
    download_url_expires_at = db.Column(db.DateTime(timezone=True), nullable=True)
    batch_id = db.Column(UUID(as_uuid=True), db.ForeignKey("batch_queues.id"), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)
    completed_at = db.Column(db.DateTime(timezone=True), nullable=True)

    user = db.relationship("User", back_populates="download_jobs")
    batch = db.relationship("BatchQueue", back_populates="jobs")

    def is_expired(self):
        if self.download_url_expires_at is None:
            return False
        return self.download_url_expires_at < datetime.utcnow()

    def formatted_size(self):
        return _format_bytes(self.file_size_bytes)

    def formatted_speed(self):
        speed = _format_bytes(self.speed_bps)
        if speed == "Unknown":
            return speed
        return f"{speed}/s"


def _format_bytes(value):
    if value is None:
        return "Unknown"
    size = float(value)
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return "Unknown"
