import math
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps.pagination import PaginationParams
from app.models import AuditLog, User
from app.schemas.audit import AuditLogOut


def record(
    db: AsyncSession,
    user_id: UUID | None,
    action: str,
    entity_type: str,
    entity_id: UUID | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    # flushed into the caller's transaction so the action and its audit entry commit together
    db.add(
        AuditLog(
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            meta=meta,
        )
    )


def jsonable(value: Any) -> Any:
    if hasattr(value, "value"):
        return value.value
    return value


async def list_logs(
    db: AsyncSession,
    pagination: PaginationParams,
    user_id: UUID | None = None,
    entity_type: str | None = None,
    action: str | None = None,
) -> dict:
    conditions = []
    if user_id is not None:
        conditions.append(AuditLog.user_id == user_id)
    if entity_type is not None:
        conditions.append(AuditLog.entity_type == entity_type)
    if action is not None:
        conditions.append(AuditLog.action == action)

    total = await db.scalar(select(func.count()).select_from(AuditLog).where(*conditions))
    rows = (
        await db.execute(
            select(AuditLog, User.name)
            .outerjoin(User, AuditLog.user_id == User.id)
            .where(*conditions)
            .order_by(AuditLog.id.desc())
            .offset(pagination.offset)
            .limit(pagination.page_size)
        )
    ).all()

    items = [
        AuditLogOut(
            id=log.id,
            user_id=log.user_id,
            actor_name=name,
            action=log.action,
            entity_type=log.entity_type,
            entity_id=log.entity_id,
            metadata=log.meta,
            created_at=log.created_at,
        )
        for log, name in rows
    ]

    return {
        "items": items,
        "total": total,
        "page": pagination.page,
        "page_size": pagination.page_size,
        "total_pages": math.ceil(total / pagination.page_size) if total else 0,
    }
