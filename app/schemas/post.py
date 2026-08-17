import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import PostStatus
from app.utils.slugify import SLUG_PATTERN


class PostListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    slug: str
    excerpt: str | None
    featured_image_url: str | None
    reading_time_minutes: int | None
    is_trending: bool
    view_count: int
    published_at: datetime | None


class PostCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    slug: str | None = Field(default=None, max_length=255, pattern=SLUG_PATTERN)
    excerpt: str | None = Field(default=None, max_length=500)
    content_json: dict
    featured_image_url: str | None = None
    category_id: uuid.UUID | None = None
    meta_title: str | None = Field(default=None, max_length=255)
    meta_description: str | None = Field(default=None, max_length=500)
    canonical_url: str | None = None
    og_image_url: str | None = None
    schema_type: str | None = Field(default=None, max_length=50)


class PostUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(default=None, max_length=255, pattern=SLUG_PATTERN)
    excerpt: str | None = Field(default=None, max_length=500)
    content_json: dict | None = None
    featured_image_url: str | None = None
    category_id: uuid.UUID | None = None
    meta_title: str | None = Field(default=None, max_length=255)
    meta_description: str | None = Field(default=None, max_length=500)
    canonical_url: str | None = None
    og_image_url: str | None = None
    schema_type: str | None = Field(default=None, max_length=50)


class PostDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    slug: str
    excerpt: str | None
    content_json: dict
    content_html: str | None
    featured_image_url: str | None
    status: PostStatus
    author_id: uuid.UUID
    author_name: str = ""
    category_id: uuid.UUID | None = None
    category_name: str | None = None
    category_slug: str | None = None
    view_count: int
    is_trending: bool
    reading_time_minutes: int | None
    meta_title: str | None
    meta_description: str | None
    canonical_url: str | None
    og_image_url: str | None
    schema_type: str
    published_at: datetime | None
    scheduled_at: datetime | None
    rejection_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class PostAdminItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    slug: str
    status: PostStatus
    author_name: str = ""
    category_name: str | None = None
    rejection_reason: str | None = None
    updated_at: datetime
    published_at: datetime | None = None


class RejectRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class ScheduleRequest(BaseModel):
    scheduled_at: datetime


class SeoUpdate(BaseModel):
    meta_title: str | None = Field(default=None, max_length=255)
    meta_description: str | None = Field(default=None, max_length=500)
    canonical_url: str | None = None
    og_image_url: str | None = None
