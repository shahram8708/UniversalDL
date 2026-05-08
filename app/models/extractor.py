import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import JSON, UUID

from app.extensions import db


class PlatformExtractor(db.Model):
    __tablename__ = "platform_extractors"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    platform_id = db.Column(db.String(50), unique=True, nullable=False)
    display_name = db.Column(db.String(100), nullable=False)
    logo_url = db.Column(db.Text, nullable=True)
    is_enabled = db.Column(db.Boolean, default=True)
    requires_headless = db.Column(db.Boolean, default=False)
    requires_proxy = db.Column(db.Boolean, default=False)
    success_rate_7d = db.Column(db.Float, default=100.0)
    last_success_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_failure_at = db.Column(db.DateTime(timezone=True), nullable=True)
    failure_reason = db.Column(db.Text, nullable=True)
    extractor_module = db.Column(db.String(255), nullable=True)
    config_json = db.Column(JSON, nullable=True)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    def status_label(self):
        if not self.is_enabled:
            return "disabled"
        rate = self.success_rate_7d or 0
        if rate >= 90:
            return "active"
        if rate >= 70:
            return "degraded"
        return "down"

    def status_color(self):
        label = self.status_label()
        if label == "active":
            return "success"
        if label == "degraded":
            return "warning"
        if label == "down":
            return "danger"
        return "secondary"
