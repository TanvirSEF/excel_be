import math
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    NotFoundException,
    PermissionDeniedException,
    ValidationException,
)
from app.deps.pagination import PaginationParams
from app.models import Media, PasswordResetToken, Post, RefreshToken, User, UserRole
from app.schemas.user import UserUpdate
from app.services import audit_service

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


async def list_authors(db: AsyncSession) -> list[User]:
    return list(
        (
            await db.scalars(
                select(User)
                .where(User.is_active.is_(True))
                .order_by(User.name.asc())
            )
        ).all()
    )


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

    if "is_active" in fields and data.is_active is False and user.id == current_user.id:
        raise ValidationException(
            "You cannot deactivate your own account", code="SELF_DEACTIVATION"
        )

    if await _is_last_super_admin(db, user):
        loses_admin = "role" in fields and data.role != UserRole.super_admin
        gets_deactivated = "is_active" in fields and data.is_active is False
        if loses_admin or gets_deactivated:
            raise ValidationException(
                "Cannot remove the last active super admin", code="LAST_SUPER_ADMIN"
            )

    was_active = user.is_active
    changes = {
        field: {
            "before": audit_service.jsonable(getattr(user, field)),
            "after": audit_service.jsonable(getattr(data, field)),
        }
        for field in fields
    }
    for field in fields:
        setattr(user, field, getattr(data, field))

    if was_active and not user.is_active:
        await db.execute(
            update(RefreshToken).where(RefreshToken.user_id == user.id).values(revoked=True)
        )

    action = "user.role_change" if "role" in fields else "user.update"
    audit_service.record(db, current_user.id, action, "user", user.id, {"changes": changes})
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

    if await _is_last_super_admin(db, user):
        raise ValidationException(
            "Cannot remove the last active super admin", code="LAST_SUPER_ADMIN"
        )

    user.is_active = False
    await db.execute(
        update(RefreshToken).where(RefreshToken.user_id == user.id).values(revoked=True)
    )
    audit_service.record(db, current_user.id, "user.deactivate", "user", user.id)
    await db.commit()


async def _is_last_super_admin(db: AsyncSession, user: User) -> bool:
    if user.role != UserRole.super_admin or not user.is_active:
        return False
    others = await db.scalar(
        select(func.count())
        .select_from(User)
        .where(
            User.role == UserRole.super_admin,
            User.is_active,
            User.id != user.id,
        )
    )
    return others == 0


async def delete_user_permanently(db: AsyncSession, current_user: User, user_id: UUID) -> None:
    if current_user.id == user_id:
        raise ValidationException(
            "You cannot delete your own account", code="SELF_DELETION"
        )

    user = await db.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise NotFoundException("User not found", code="USER_NOT_FOUND")

    if await _is_last_super_admin(db, user):
        raise ValidationException(
            "Cannot delete the last active super admin", code="LAST_SUPER_ADMIN"
        )

    # Reassign any authored posts to the current admin
    await db.execute(
        update(Post).where(Post.author_id == user.id).values(author_id=current_user.id)
    )

    # Reassign any uploaded media to the current admin
    await db.execute(
        update(Media).where(Media.uploader_id == user.id).values(uploader_id=current_user.id)
    )

    # Delete all authentication tokens
    await db.execute(
        delete(RefreshToken).where(RefreshToken.user_id == user.id)
    )
    await db.execute(
        delete(PasswordResetToken).where(PasswordResetToken.user_id == user.id)
    )

    audit_service.record(
        db,
        current_user.id,
        "user.delete_permanent",
        "user",
        user.id,
        {"deleted_name": user.name, "deleted_email": user.email},
    )

    await db.delete(user)
    await db.commit()
