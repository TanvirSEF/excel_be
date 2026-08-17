import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


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
