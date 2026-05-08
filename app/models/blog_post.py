import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import UUID

from app.extensions import db


class BlogPost(db.Model):
    __tablename__ = "blog_posts"

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug = db.Column(db.String(255), unique=True, nullable=False, index=True)
    title = db.Column(db.String(500), nullable=False)
    excerpt = db.Column(db.Text, nullable=True)
    content = db.Column(db.Text, nullable=True)
    thumbnail_url = db.Column(db.Text, nullable=True)
    author = db.Column(db.String(100), default="UniversalDL Team")
    category = db.Column(db.String(100), nullable=True)
    published_at = db.Column(db.DateTime(timezone=True), nullable=True)
    is_published = db.Column(db.Boolean, default=False)
    view_count = db.Column(db.Integer, default=0)
    meta_description = db.Column(db.String(300), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)

    @classmethod
    def get_by_slug(cls, slug):
        return cls.query.filter_by(slug=slug, is_published=True).first()

    @classmethod
    def get_published(cls, page=1, per_page=9):
        return (
            cls.query.filter_by(is_published=True)
            .order_by(cls.published_at.desc())
            .paginate(page=page, per_page=per_page, error_out=False)
        )
