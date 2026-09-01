from pydantic import EmailStr, Field

from app.schemas.common import RequestModel


class ContactMessageCreate(RequestModel):
    email: EmailStr
    subject: str = Field(min_length=3, max_length=255)
    service: str | None = Field(default=None, max_length=50)
    message: str = Field(min_length=10, max_length=5000)
