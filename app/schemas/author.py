import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AuthorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    avatar_url: str | None
    bio: str | None
    joined_at: datetime
    post_count: int
