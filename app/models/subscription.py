import uuid
from datetime import datetime, timedelta

from sqlalchemy.dialects.postgresql import JSON, UUID

from app.extensions import db


class Subscription(db.Model):
    __tablename__ = "subscriptions"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), nullable=False)
    channel_url = db.Column(db.Text, nullable=False)
    platform = db.Column(db.String(50), nullable=True)
    channel_name = db.Column(db.String(255), nullable=True)
    channel_id = db.Column(db.String(255), nullable=True)
    quality = db.Column(db.String(20), default="best")
    format = db.Column(db.String(10), default="mp4")
    frequency = db.Column(
        db.Enum("hourly", "daily", "weekly", name="frequency_enum"),
        default="daily",
    )
    is_active = db.Column(db.Boolean, default=True)
    last_checked_at = db.Column(db.DateTime(timezone=True), nullable=True)
    next_check_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_download_at = db.Column(db.DateTime(timezone=True), nullable=True)
    total_downloaded = db.Column(db.Integer, default=0)
    known_content_ids = db.Column(JSON, default=list)
    notification_email = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)

    user = db.relationship("User", back_populates="subscriptions")

    def calculate_next_check(self):
        now = datetime.utcnow()
        if self.frequency == "hourly":
            return now + timedelta(hours=1)
        if self.frequency == "weekly":
            return now + timedelta(days=7)
        return now + timedelta(days=1)
