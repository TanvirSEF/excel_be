from pydantic import BaseModel, EmailStr, Field

from app.models import UserRole
from app.schemas.common import RequestModel
from app.schemas.user import UserOut


class RegisterRequest(RequestModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    role: UserRole


class RefreshRequest(RequestModel):
    refresh_token: str


class ForgotPasswordRequest(RequestModel):
    email: EmailStr


class ResetPasswordRequest(RequestModel):
    token: str
    new_password: str = Field(min_length=10, max_length=128)


class ChangePasswordRequest(RequestModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=128)


class VerifyEmailRequest(RequestModel):
    token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
