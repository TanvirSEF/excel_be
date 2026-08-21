import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import RequestModel


class MediaUpdate(RequestModel):
    alt_text: str | None = Field(default=None, max_length=255)
    folder: str | None = Field(default=None, max_length=100)


class MediaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    file_url: str
    file_type: str
    alt_text: str | None
    width: int | None
    height: int | None
    size_kb: int | None
    folder: str
    created_at: datetime
