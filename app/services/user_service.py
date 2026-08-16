import math
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    NotFoundException,
    PermissionDeniedException,
    ValidationException,
)
from app.deps.pagination import PaginationParams
from app.models import RefreshToken, User, UserRole
from app.schemas.user import UserUpdate

ADMIN_ONLY_FIELDS = {"role", "is_active", "is_verified"}


async def list_users(db: AsyncSession, pagination: PaginationParams) -> dict:
    total = await db.scalar(select(func.count()).select_from(User))
    result = await db.scalars(
        select(User).order_by(User.created_at.desc()).offset(pagination.offset).limit(pagination.page_size)
    )
    return {
        "items": result.all(),
        "total": total,
        "page": pagination.page,
        "page_size": pagination.page_size,
        "total_pages": math.ceil(total / pagination.page_size) if total else 0,
    }


async def get_user(db: AsyncSession, current_user: User, user_id: UUID) -> User:
    user = await db.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise NotFoundException("User not found", code="USER_NOT_FOUND")

    if user.id != current_user.id and current_user.role != UserRole.super_admin:
        raise PermissionDeniedException()
    return user


async def update_user(
    db: AsyncSession, current_user: User, user_id: UUID, data: UserUpdate
) -> User:
    user = await get_user(db, current_user, user_id)

    fields = data.model_fields_set
    if current_user.role != UserRole.super_admin and fields & ADMIN_ONLY_FIELDS:
        raise PermissionDeniedException("Only a super admin can change these fields")

    for field in fields:
        setattr(user, field, getattr(data, field))

    await db.commit()
    await db.refresh(user)
    return user


async def deactivate_user(db: AsyncSession, current_user: User, user_id: UUID) -> None:
    if current_user.id == user_id:
        raise ValidationException(
            "You cannot deactivate your own account", code="SELF_DEACTIVATION"
        )

    user = await db.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise NotFoundException("User not found", code="USER_NOT_FOUND")

    user.is_active = False
    await db.execute(
        update(RefreshToken).where(RefreshToken.user_id == user.id).values(revoked=True)
    )
    await db.commit()
