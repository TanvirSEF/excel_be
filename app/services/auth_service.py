from datetime import datetime, timedelta, timezone

import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ConflictException, PermissionDeniedException, UnauthorizedException
from app.core.security import (
    REFRESH,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.models import RefreshToken, User
from app.schemas.auth import RegisterRequest

REFRESH_LIFETIME = timedelta(days=settings.refresh_token_expire_days)


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
