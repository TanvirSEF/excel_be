from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.deps.auth_deps import require_role
from app.models import Tag, User, UserRole
from app.schemas.tag import TagCreate, TagOut
from app.services import tag_service

router = APIRouter(prefix="/tags", tags=["tags"])

WRITERS = (UserRole.super_admin, UserRole.senior_editor, UserRole.technical_writer)


@router.get("", response_model=list[TagOut])
async def list_tags(db: AsyncSession = Depends(get_db)) -> list[Tag]:
    return await tag_service.list_tags(db)


@router.post("", response_model=TagOut, status_code=201)
async def create_tag(
    data: TagCreate,
    user: User = Depends(require_role(*WRITERS)),
    db: AsyncSession = Depends(get_db),
) -> Tag:
    return await tag_service.create_tag(db, data)
