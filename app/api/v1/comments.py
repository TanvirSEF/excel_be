from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.deps.auth_deps import require_role
from app.models import Comment, User, UserRole
from app.schemas.comment import ModerateRequest
from app.services import comment_service

router = APIRouter(prefix="/comments", tags=["comments"])

EDITORS = (UserRole.super_admin, UserRole.senior_editor)


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
