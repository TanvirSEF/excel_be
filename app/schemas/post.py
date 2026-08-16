import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


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
