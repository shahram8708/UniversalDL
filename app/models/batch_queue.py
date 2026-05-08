import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db


class BatchQueue(db.Model):
    __tablename__ = "batch_queues"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = db.Column(UUID(as_uuid=True), db.ForeignKey("users.id"), nullable=False)
    name = db.Column(db.String(255), nullable=True)
    total_jobs = db.Column(db.Integer, default=0)
    completed_jobs = db.Column(db.Integer, default=0)
    failed_jobs = db.Column(db.Integer, default=0)
    status = db.Column(
        db.Enum("queued", "in_progress", "complete", "partial", "cancelled", name="batch_status_enum"),
        default="queued",
    )
    zip_url = db.Column(db.Text, nullable=True)
    zip_expires_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)
    completed_at = db.Column(db.DateTime(timezone=True), nullable=True)

    user = db.relationship("User", back_populates="batch_queues")
    jobs = db.relationship("DownloadJob", back_populates="batch", lazy="dynamic")

    def progress_percentage(self):
        if self.total_jobs <= 0:
            return 0
        completed = self.completed_jobs + self.failed_jobs
        return int((completed / self.total_jobs) * 100)
