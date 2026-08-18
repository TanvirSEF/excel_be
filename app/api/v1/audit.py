from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.deps.auth_deps import require_role
from app.deps.pagination import PaginationParams
from app.models import User, UserRole
from app.schemas.audit import AuditLogOut
from app.schemas.common import Page
from app.services import audit_service

router = APIRouter(prefix="/audit-logs", tags=["audit"])


@router.get("", response_model=Page[AuditLogOut])
async def list_audit_logs(
    pagination: PaginationParams = Depends(),
    user_id: UUID | None = Query(default=None),
    entity_type: str | None = Query(default=None, max_length=50),
    action: str | None = Query(default=None, max_length=50),
    user: User = Depends(require_role(UserRole.super_admin)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await audit_service.list_logs(db, pagination, user_id, entity_type, action)
