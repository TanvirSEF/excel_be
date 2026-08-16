import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

import httpx
import jwt
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    ConflictException,
    PermissionDeniedException,
    UnauthorizedException,
    ValidationException,
)
from app.core.security import (
    REFRESH,
    VERIFY_EMAIL,
    create_access_token,
    create_refresh_token,
    create_verify_email_token,
    decode_token,
    generate_reset_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.models import PasswordResetToken, RefreshToken, User
from app.schemas.auth import RegisterRequest
from app.services.email_service import (
    reset_password_email,
    send_email,
    verify_email_template,
)

logger = logging.getLogger(__name__)

REFRESH_LIFETIME = timedelta(days=settings.refresh_token_expire_days)
RESET_TOKEN_LIFETIME = timedelta(minutes=30)


async def register(db: AsyncSession, data: RegisterRequest) -> User:
    existing = await db.scalar(select(User).where(User.email == data.email))
    if existing is not None:
        raise ConflictException("Email already registered", code="EMAIL_TAKEN")

    user = User(
        name=data.name,
        email=data.email,
        password_hash=hash_password(data.password),
        role=data.role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def login(
    db: AsyncSession, email: str, password: str, device_info: str | None
) -> tuple[User, str, str]:
    user = await db.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(password, user.password_hash):
        raise UnauthorizedException("Invalid email or password")
    if not user.is_active:
        raise PermissionDeniedException("Account is deactivated")

    user.last_login_at = datetime.now(timezone.utc)
    refresh = await _issue_refresh_token(db, user, device_info)
    access = create_access_token(str(user.id), user.role.value)
    return user, access, refresh


async def rotate_refresh_token(
    db: AsyncSession, token: str, device_info: str | None
) -> tuple[User, str, str]:
    try:
        payload = decode_token(token)
    except jwt.PyJWTError:
        raise UnauthorizedException("Invalid refresh token")

    if payload.get("type") != REFRESH:
        raise UnauthorizedException("Wrong token type")

    row = await db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(token)))
    if row is None or row.revoked:
        raise UnauthorizedException("Refresh token revoked or unknown")

    user = await db.scalar(select(User).where(User.id == row.user_id))
    if user is None or not user.is_active:
        raise UnauthorizedException("User not found or inactive")

    row.revoked = True
    refresh = await _issue_refresh_token(db, user, device_info)
    access = create_access_token(str(user.id), user.role.value)
    return user, access, refresh


async def revoke_refresh_token(db: AsyncSession, token: str) -> None:
    row = await db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(token)))
    if row is not None and not row.revoked:
        row.revoked = True
        await db.commit()


async def forgot_password(db: AsyncSession, email: str) -> None:
    user = await db.scalar(select(User).where(User.email == email))
    if user is None or not user.is_active:
        return

    await db.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == user.id, PasswordResetToken.used.is_(False))
        .values(used=True)
    )
    token = generate_reset_token()
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=datetime.now(timezone.utc) + RESET_TOKEN_LIFETIME,
        )
    )
    await db.commit()

    link = f"{settings.frontend_url}/reset-password?token={token}"
    if settings.resend_api_key:
        try:
            await send_email(user.email, "Reset your Excel Insider password", reset_password_email(link))
        except httpx.HTTPError:
            logger.exception("Failed to send reset email to %s", user.email)
    else:
        logger.warning("DEV MODE reset link for %s: %s", user.email, link)


async def reset_password(db: AsyncSession, token: str, new_password: str) -> None:
    row = await db.scalar(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_token(token))
    )
    if row is None or row.used or row.expires_at < datetime.now(timezone.utc):
        raise ValidationException("Invalid or expired reset token", code="INVALID_RESET_TOKEN")

    user = await db.scalar(select(User).where(User.id == row.user_id))
    if user is None:
        raise UnauthorizedException()

    user.password_hash = hash_password(new_password)
    row.used = True
    await db.execute(
        update(RefreshToken).where(RefreshToken.user_id == user.id).values(revoked=True)
    )
    await db.commit()


async def change_password(
    db: AsyncSession, user: User, current_password: str, new_password: str
) -> None:
    if not verify_password(current_password, user.password_hash):
        raise UnauthorizedException("Current password is incorrect")

    user.password_hash = hash_password(new_password)
    await db.execute(
        update(RefreshToken).where(RefreshToken.user_id == user.id).values(revoked=True)
    )
    await db.commit()


async def send_verification_email(db: AsyncSession, user: User) -> None:
    if user.is_verified:
        raise ConflictException("Email already verified", code="ALREADY_VERIFIED")

    token = create_verify_email_token(str(user.id))
    link = f"{settings.frontend_url}/verify-email?token={token}"
    if settings.resend_api_key:
        try:
            await send_email(user.email, "Verify your Excel Insider email", verify_email_template(link))
        except httpx.HTTPError:
            logger.exception("Failed to send verification email to %s", user.email)
    else:
        logger.warning("DEV MODE verify link for %s: %s", user.email, link)


async def verify_email(db: AsyncSession, token: str) -> None:
    try:
        payload = decode_token(token)
    except jwt.PyJWTError:
        raise ValidationException("Invalid or expired verification token", code="INVALID_VERIFY_TOKEN")

    if payload.get("type") != VERIFY_EMAIL:
        raise ValidationException("Invalid or expired verification token", code="INVALID_VERIFY_TOKEN")

    user = await db.scalar(select(User).where(User.id == UUID(payload["sub"])))
    if user is None:
        raise UnauthorizedException()

    user.is_verified = True
    await db.commit()


async def _issue_refresh_token(
    db: AsyncSession, user: User, device_info: str | None
) -> str:
    refresh = create_refresh_token(str(user.id), user.role.value)
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_token(refresh),
            device_info=device_info,
            expires_at=datetime.now(timezone.utc) + REFRESH_LIFETIME,
        )
    )
    await db.commit()
    return refresh
