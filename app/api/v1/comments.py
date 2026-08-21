from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.deps.auth_deps import require_role
from app.deps.pagination import PaginationParams
from app.models import Comment, CommentStatus, User, UserRole
from app.schemas.comment import CommentAdminItem, ModerateRequest
from app.schemas.common import Page
from app.services import comment_service

router = APIRouter(prefix="/comments", tags=["comments"])

EDITORS = (UserRole.super_admin, UserRole.senior_editor)


@router.get("", response_model=Page[CommentAdminItem])
async def admin_list(
    pagination: PaginationParams = Depends(),
    status: CommentStatus | None = Query(None),
    user: User = Depends(require_role(*EDITORS)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await comment_service.admin_list(db, pagination, status)


@router.patch("/{comment_id}/moderate")
async def moderate(
    comment_id: UUID,
    data: ModerateRequest,
    user: User = Depends(require_role(*EDITORS)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    comment = await comment_service.moderate(db, comment_id, data.status)
    return {"id": str(comment.id), "status": comment.status.value}


@router.delete("/{comment_id}")
async def delete_comment(
    comment_id: UUID,
    user: User = Depends(require_role(*EDITORS)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await comment_service.delete(db, comment_id)
    return {"message": "Comment deleted"}
