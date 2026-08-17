from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.deps.auth_deps import require_role
from app.deps.pagination import PaginationParams
from app.models import Media, User, UserRole
from app.schemas.common import Page
from app.schemas.media import MediaOut
from app.services import media_service

router = APIRouter(prefix="/media", tags=["media"])

WRITERS = (UserRole.super_admin, UserRole.senior_editor, UserRole.technical_writer)
EDITORS = (UserRole.super_admin, UserRole.senior_editor)


@router.post("/upload", response_model=MediaOut, status_code=201)
async def upload(
    file: UploadFile = File(...),
    folder: str = Form("uncategorized"),
    user: User = Depends(require_role(*WRITERS)),
    db: AsyncSession = Depends(get_db),
) -> Media:
    file_bytes = await file.read()
    return await media_service.upload(db, user, file_bytes, file.filename or "", folder)


@router.get("", response_model=Page[MediaOut])
async def list_media(
    pagination: PaginationParams = Depends(),
    folder: str | None = Query(None),
    user: User = Depends(require_role(*WRITERS)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await media_service.list_media(db, pagination, folder)


@router.delete("/{media_id}")
async def delete_media(
    media_id: UUID,
    user: User = Depends(require_role(*EDITORS)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await media_service.delete_media(db, media_id)
    return {"message": "Media deleted"}
