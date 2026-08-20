from pydantic import EmailStr, Field

from app.schemas.common import RequestModel


class SubscribeRequest(RequestModel):
    email: EmailStr
    source: str | None = Field(default=None, max_length=50)


class UnsubscribeRequest(RequestModel):
    token: str
