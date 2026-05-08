import hashlib
import secrets
import uuid
from datetime import datetime

from flask_login import UserMixin
from sqlalchemy.dialects.postgresql import UUID

from app.extensions import bcrypt, db


class User(db.Model, UserMixin):
    __tablename__ = "users"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=True)
    display_name = db.Column(db.String(100), nullable=True)
    plan = db.Column(
        db.Enum("free", "pro", "enterprise", name="plan_enum"),
        default="free",
        nullable=False,
    )
    plan_expires_at = db.Column(db.DateTime(timezone=True), nullable=True)
    google_oauth_id = db.Column(db.String(255), nullable=True, unique=True)
    api_key_hash = db.Column(db.String(255), nullable=True)
    api_key_prefix = db.Column(db.String(8), nullable=True)
    default_quality = db.Column(
        db.Enum("best", "1080p", "720p", "480p", "audio", name="quality_enum"),
        default="best",
        nullable=False,
    )
    default_format = db.Column(
        db.Enum("mp4", "mkv", "webm", "mp3", "flac", name="format_enum"),
        default="mp4",
        nullable=False,
    )
    email_notifications = db.Column(db.Boolean, default=True)
    anonymous_mode = db.Column(db.Boolean, default=False)
    is_admin = db.Column(db.Boolean, default=False)
    is_suspended = db.Column(db.Boolean, default=False)
    is_onboarded = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)
    last_login_at = db.Column(db.DateTime(timezone=True), nullable=True)

    download_jobs = db.relationship("DownloadJob", back_populates="user", lazy="dynamic")
    subscriptions = db.relationship("Subscription", back_populates="user", lazy="dynamic")
    audit_logs = db.relationship("AuditLog", back_populates="user", lazy="dynamic")
    api_usages = db.relationship("APIUsage", back_populates="user", lazy="dynamic")
    batch_queues = db.relationship("BatchQueue", back_populates="user", lazy="dynamic")

    def is_active(self):
        return not self.is_suspended

    def get_id(self):
        return str(self.id)

    def set_password(self, password):
        self.password_hash = bcrypt.generate_password_hash(password).decode("utf-8")

    def check_password(self, password):
        if not self.password_hash:
            return False
        return bcrypt.check_password_hash(self.password_hash, password)

    def generate_api_key(self):
        raw_key = secrets.token_urlsafe(32)
        key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        self.api_key_hash = key_hash
        self.api_key_prefix = raw_key[:8]
        return raw_key

    def is_pro(self):
        if self.plan not in ("pro", "enterprise"):
            return False
        if self.plan_expires_at is None:
            return True
        return self.plan_expires_at > datetime.utcnow()

    def __repr__(self):
        return f"<User {self.email}>"
