import json
import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import PostStatus
from app.schemas.common import RequestModel
from app.utils.slugify import SLUG_PATTERN

ALLOWED_BLOCK_TYPES = frozenset(
    {"paragraph", "heading", "quote", "code", "list", "html", "image", "table", "hr"}
)
ALLOWED_MARK_TYPES = frozenset({"bold", "italic", "strike", "code", "link"})
ALLOWED_ALIGNS = frozenset({"left", "center", "right"})
SAFE_HREF = re.compile(r"^(https?://|mailto:|/|#)", re.IGNORECASE)
MAX_BLOCKS = 2000
MAX_CONTENT_JSON_BYTES = 512 * 1024


def _validate_inlines(inlines: object) -> None:
    if not isinstance(inlines, list):
        raise ValueError("inline content must be a list")
    for inline in inlines:
        if not isinstance(inline, dict) or not isinstance(inline.get("text"), str):
            raise ValueError("inline content items must be objects with a text string")
        marks = inline.get("marks")
        if marks is None:
            continue
        if not isinstance(marks, list):
            raise ValueError("marks must be a list")
        for mark in marks:
            if not isinstance(mark, dict) or mark.get("type") not in ALLOWED_MARK_TYPES:
                raise ValueError("marks must be objects with a supported type")
            if mark.get("type") == "link":
                href = mark.get("href")
                if not isinstance(href, str) or not SAFE_HREF.match(href):
                    raise ValueError("link marks require a safe href")


def _validate_rich(value: object) -> None:
    if isinstance(value, list):
        _validate_inlines(value)


def _validate_block(block: dict) -> None:
    block_type = block.get("type")

    if block_type in {"paragraph", "heading", "quote"}:
        if not isinstance(block.get("text"), str):
            raise ValueError("text blocks require a text string")
        align = block.get("align")
        if align is not None and align not in ALLOWED_ALIGNS:
            raise ValueError("align must be left, center or right")
        if "content" in block:
            _validate_inlines(block.get("content"))

    if block_type == "list":
        items = block.get("items")
        if not isinstance(items, list):
            raise ValueError("list blocks require an items list")
        for item in items:
            _validate_rich(item)

    if block_type == "table":
        rows = block.get("rows")
        if not isinstance(rows, list):
            raise ValueError("table blocks require a rows list")
        for row in rows:
            if not isinstance(row, list):
                raise ValueError("table rows must be lists")
            for cell in row:
                _validate_rich(cell)


def _validate_content(value: dict | None) -> dict | None:
    if value is None:
        return value
    blocks = value.get("blocks")
    if not isinstance(blocks, list):
        raise ValueError("content_json must contain a 'blocks' list")
    if len(blocks) > MAX_BLOCKS:
        raise ValueError(f"content_json cannot exceed {MAX_BLOCKS} blocks")
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") not in ALLOWED_BLOCK_TYPES:
            raise ValueError("every block must be an object with a supported type")
        _validate_block(block)
    if len(json.dumps(value)) > MAX_CONTENT_JSON_BYTES:
        raise ValueError("content_json is too large")
    return value


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


class PostCreate(RequestModel):
    title: str = Field(min_length=1, max_length=255)
    slug: str | None = Field(default=None, max_length=255, pattern=SLUG_PATTERN)
    excerpt: str | None = Field(default=None, max_length=500)
    content_json: dict
    featured_image_url: str | None = None
    category_id: uuid.UUID | None = None
    tags: list[str] | None = None
    meta_title: str | None = Field(default=None, max_length=255)
    meta_description: str | None = Field(default=None, max_length=500)
    canonical_url: str | None = None
    og_image_url: str | None = None
    schema_type: str | None = Field(default=None, max_length=50)

    _check_content = field_validator("content_json")(_validate_content)


class PostUpdate(RequestModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(default=None, max_length=255, pattern=SLUG_PATTERN)
    excerpt: str | None = Field(default=None, max_length=500)
    content_json: dict | None = None
    featured_image_url: str | None = None
    category_id: uuid.UUID | None = None
    tags: list[str] | None = None
    meta_title: str | None = Field(default=None, max_length=255)
    meta_description: str | None = Field(default=None, max_length=500)
    canonical_url: str | None = None
    og_image_url: str | None = None
    schema_type: str | None = Field(default=None, max_length=50)

    _check_content = field_validator("content_json")(_validate_content)


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
    tags: list[str] = []
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


class RejectRequest(RequestModel):
    reason: str = Field(min_length=1, max_length=500)


class ScheduleRequest(RequestModel):
    scheduled_at: datetime


class SeoUpdate(RequestModel):
    meta_title: str | None = Field(default=None, max_length=255)
    meta_description: str | None = Field(default=None, max_length=500)
    canonical_url: str | None = None
    og_image_url: str | None = None
