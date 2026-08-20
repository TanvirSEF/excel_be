import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    post_id: uuid.UUID
    file_name: str
    file_url: str
    file_type: str
    file_size_kb: int | None
    download_count: int
    created_at: datetime


class DownloadUrlOut(BaseModel):
    url: str
    expires_in: int
