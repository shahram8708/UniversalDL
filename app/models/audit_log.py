import logging
import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import JSON, UUID

from app.extensions import db


logger = logging.getLogger(__name__)


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), nullable=True)
    action = db.Column(db.String(100), nullable=False)
    ip_address = db.Column(db.String(45), nullable=True)
    user_agent = db.Column(db.Text, nullable=True)
    resource_id = db.Column(UUID(as_uuid=True), nullable=True)
    detail_json = db.Column(JSON, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)

    user = db.relationship("User", back_populates="audit_logs")

    @staticmethod
    def _coerce_uuid(value, field_name):
        if value is None or isinstance(value, uuid.UUID):
            return value

        if isinstance(value, str):
            try:
                return uuid.UUID(value)
            except ValueError:
                logger.warning("Audit log ignored invalid %s value: %s", field_name, value)
                return None

        logger.warning("Audit log ignored unsupported %s type: %s", field_name, type(value).__name__)
        return None

    @classmethod
    def log(
        cls,
        action,
        user_id=None,
        ip_address=None,
        user_agent=None,
        resource_id=None,
        detail_json=None,
    ):
        try:
            user_id = cls._coerce_uuid(user_id, "user_id")
            resource_id = cls._coerce_uuid(resource_id, "resource_id")

            entry = cls(
                action=action,
                user_id=user_id,
                ip_address=ip_address,
                user_agent=user_agent,
                resource_id=resource_id,
                detail_json=detail_json,
            )
            db.session.add(entry)
            db.session.commit()
            return entry
        except Exception as exc:
            logger.warning("Audit log failed: %s", exc)
            db.session.rollback()
            return None
