import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import Computed

from app.models.base import Base


class PostStatus(str, enum.Enum):
    draft = "draft"
    pending_review = "pending_review"
    published = "published"
    rejected = "rejected"
    scheduled = "scheduled"


class Post(Base):
    __tablename__ = "posts"
    __table_args__ = (
        Index("ix_posts_status", "status"),
        Index("ix_posts_category_id", "category_id"),
        Index("ix_posts_author_id", "author_id"),
        Index("ix_posts_published_at", "published_at"),
        Index("ix_posts_is_trending", "is_trending"),
        Index("idx_posts_content_tsv", "content_tsv", postgresql_using="gin"),
        Index(
            "idx_posts_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(255), unique=True)
    excerpt: Mapped[str | None] = mapped_column(String(500))
    content_json: Mapped[dict] = mapped_column(JSONB)
    content_html: Mapped[str | None] = mapped_column(Text)
    content_tsv: Mapped[str] = mapped_column(
        TSVECTOR(),
        Computed(
            "setweight(to_tsvector('english', coalesce(title, '')), 'A') || "
            "setweight(to_tsvector('english', coalesce(excerpt, '')), 'B') || "
            "setweight(to_tsvector('english', coalesce(content_html, '')), 'C')",
            persisted=True,
        ),
    )
    featured_image_url: Mapped[str | None] = mapped_column(Text)
    author_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id")
    )
    status: Mapped[PostStatus] = mapped_column(Enum(PostStatus, name="post_status"))
    view_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    is_trending: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    reading_time_minutes: Mapped[int | None] = mapped_column(SmallInteger)
    meta_title: Mapped[str | None] = mapped_column(String(255))
    meta_description: Mapped[str | None] = mapped_column(String(500))
    canonical_url: Mapped[str | None] = mapped_column(Text)
    og_image_url: Mapped[str | None] = mapped_column(Text)
    schema_type: Mapped[str] = mapped_column(String(50), default="TechArticle", server_default=text("'TechArticle'"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
