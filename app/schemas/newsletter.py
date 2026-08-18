from pydantic import BaseModel, EmailStr, Field


class SubscribeRequest(BaseModel):
    email: EmailStr
    source: str | None = Field(default=None, max_length=50)


class UnsubscribeRequest(BaseModel):
    token: str
