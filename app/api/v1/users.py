from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.deps.auth_deps import get_current_user, require_role
from app.deps.pagination import PaginationParams
from app.models import User, UserRole
from app.schemas.common import Page
from app.schemas.user import UserOut, UserUpdate
from app.services import user_service

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=Page[UserOut])
async def list_users(
    pagination: PaginationParams = Depends(),
    user: User = Depends(require_role(UserRole.super_admin)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await user_service.list_users(db, pagination)


@router.get("/{user_id}", response_model=UserOut)
async def get_user(
    user_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    return await user_service.get_user(db, user, user_id)


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: UUID,
    data: UserUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    return await user_service.update_user(db, user, user_id, data)


@router.delete("/{user_id}")
async def deactivate_user(
    user_id: UUID,
    user: User = Depends(require_role(UserRole.super_admin)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await user_service.deactivate_user(db, user, user_id)
    return {"message": "User deactivated"}
