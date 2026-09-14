import uuid

from pydantic import BaseModel, ConfigDict

from app.schemas.common import Page
from app.schemas.post import CategoryMini, PostListItem


class SeriesOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    description: str | None
    seo_title: str | None
    seo_description: str | None
    category: CategoryMini | None = None
    post_count: int = 0


class SeriesWithPosts(BaseModel):
    series: SeriesOut
    posts: Page[PostListItem]
