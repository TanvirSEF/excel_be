import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.utils.slugify import SLUG_PATTERN


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    slug: str | None = Field(default=None, max_length=120, pattern=SLUG_PATTERN)
    parent_id: uuid.UUID | None = None
    order_index: int = 0
    description: str | None = None
    icon_url: str | None = None
    color_hex: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    is_featured: bool = False
    seo_title: str | None = None
    seo_description: str | None = None


class CategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    slug: str | None = Field(default=None, max_length=120, pattern=SLUG_PATTERN)
    parent_id: uuid.UUID | None = None
    order_index: int | None = None
    description: str | None = None
    icon_url: str | None = None
    color_hex: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    is_featured: bool | None = None
    seo_title: str | None = None
    seo_description: str | None = None


class ReorderItem(BaseModel):
    id: uuid.UUID
    order_index: int
    parent_id: uuid.UUID | None = None


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    parent_id: uuid.UUID | None
    order_index: int
    description: str | None
    icon_url: str | None
    color_hex: str | None
    is_featured: bool
    seo_title: str | None
    seo_description: str | None
    children: list["CategoryOut"] = []


CategoryOut.model_rebuild()
