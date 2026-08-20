import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models import CommentStatus
from app.schemas.common import RequestModel


class CommentCreate(RequestModel):
    user_name: str = Field(min_length=1, max_length=100)
    user_email: EmailStr
    comment_text: str = Field(min_length=1)
    parent_id: uuid.UUID | None = None


class ModerateRequest(RequestModel):
    status: CommentStatus


class CommentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    parent_id: uuid.UUID | None
    user_name: str
    comment_text: str
    created_at: datetime
    children: list["CommentOut"] = []


CommentOut.model_rebuild()
