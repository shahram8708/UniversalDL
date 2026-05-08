import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db


class APIUsage(db.Model):
    __tablename__ = "api_usages"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), nullable=True)
    api_key_prefix = db.Column(db.String(8), nullable=True)
    endpoint = db.Column(db.String(100), nullable=True)
    method = db.Column(db.String(10), nullable=True)
    status_code = db.Column(db.SmallInteger, nullable=True)
    response_ms = db.Column(db.Integer, nullable=True)
    called_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)

    user = db.relationship("User", back_populates="api_usages")
