from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.deps.auth_deps import require_role
from app.models import User, UserRole
from app.schemas.role import PermissionGroupOut, RoleOut, RolePermissionsUpdate
from app.services import role_service

router = APIRouter(prefix="/roles", tags=["roles"])

VIEWERS = (UserRole.super_admin, UserRole.senior_editor)


@router.get("", response_model=list[RoleOut])
async def list_roles(
    user: User = Depends(require_role(*VIEWERS)),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    return await role_service.list_roles(db)


@router.get("/permissions", response_model=list[PermissionGroupOut])
async def list_permissions(
    user: User = Depends(require_role(*VIEWERS)),
) -> list[dict]:
    return await role_service.get_permission_groups()


@router.put("/{role}/permissions", response_model=RoleOut)
async def update_role_permissions(
    role: UserRole,
    data: RolePermissionsUpdate,
    user: User = Depends(require_role(UserRole.super_admin)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await role_service.update_role_permissions(
        db, user, role, data.permissions
    )
