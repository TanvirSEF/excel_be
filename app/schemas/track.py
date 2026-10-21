import uuid
from typing import Literal

from pydantic import BaseModel, Field

MAX_ENGAGED_MS = 6 * 3600 * 1000
MAX_BATCH_SIZE = 20

CustomEventName = Literal["outbound_click", "file_download", "calculator_use"]


class TrackEvent(BaseModel):
    event_id: uuid.UUID
    type: Literal["pageview", "ping", "event"]
    name: CustomEventName | None = None
    target: str | None = Field(default=None, max_length=255)
    session_id: uuid.UUID
    visitor_id: uuid.UUID
    page_id: uuid.UUID
    path: str = Field(min_length=1, max_length=300)
    referrer: str | None = Field(default=None, max_length=500)
    utm_source: str | None = Field(default=None, max_length=100)
    utm_medium: str | None = Field(default=None, max_length=100)
    utm_campaign: str | None = Field(default=None, max_length=100)
    engaged_ms: int = Field(default=0, ge=0, le=MAX_ENGAGED_MS)
    scroll: int = Field(default=0, ge=0, le=100)
    interacted: bool = False
    returning: bool = False
    internal: bool = False
    webdriver: bool = False
    has_languages: bool = True
    screen_width: int | None = Field(default=None, ge=0, le=20000)
    screen_height: int | None = Field(default=None, ge=0, le=20000)


class TrackBatch(BaseModel):
    events: list[TrackEvent] = Field(min_length=1, max_length=MAX_BATCH_SIZE)
