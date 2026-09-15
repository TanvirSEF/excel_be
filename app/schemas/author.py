import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AuthorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    name: str
    avatar_url: str | None = None
    bio: str | None = None
    joined_at: datetime = Field(validation_alias="created_at")
    post_count: int = 0
    website_url: str | None = None
    linkedin_url: str | None = None
    twitter_url: str | None = None
    github_url: str | None = None


