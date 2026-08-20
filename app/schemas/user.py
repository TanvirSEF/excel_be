import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

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
    is_active: bool
    is_verified: bool
    last_login_at: datetime | None
    created_at: datetime


class UserUpdate(RequestModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    avatar_url: str | None = None
    bio: str | None = None
    role: UserRole | None = None
    is_active: bool | None = None
    is_verified: bool | None = None
