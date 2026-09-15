import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl

from app.models import UserRole
from app.schemas.common import RequestModel


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    email: EmailStr
    role: UserRole
    avatar_url: str | None
    bio: str | None
    website_url: str | None
    linkedin_url: str | None
    twitter_url: str | None
    github_url: str | None
    is_active: bool
    is_verified: bool
    last_login_at: datetime | None
    created_at: datetime


class UserUpdate(RequestModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    avatar_url: str | None = None
    bio: str | None = None
    website_url: str | None = None
    linkedin_url: str | None = None
    twitter_url: str | None = None
    github_url: str | None = None
    role: UserRole | None = None
    is_active: bool | None = None
    is_verified: bool | None = None

