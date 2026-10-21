import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TrafficLogItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    viewed_at: datetime
    post_id: uuid.UUID
    post_title: str
    post_slug: str
    ip_address: str | None = None
    country: str | None = None
    country_code: str | None = None
    city: str | None = None
    device_type: str | None = None
    browser: str | None = None
    os: str | None = None
    referrer: str | None = None


class LiveTrafficStats(BaseModel):
    active_30m: int
    today_views: int
    top_country: str | None = None
    top_country_code: str | None = None
    top_device: str | None = None


class TrafficFeedResponse(BaseModel):
    items: list[TrafficLogItem]
    total: int
    page: int
    page_size: int
    total_pages: int
    stats: LiveTrafficStats
