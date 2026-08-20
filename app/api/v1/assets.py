from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.deps.auth_deps import require_role
from app.models import User, UserRole
from app.schemas.asset import DownloadUrlOut
from app.services import asset_service

router = APIRouter(prefix="/assets", tags=["assets"])

EDITORS = (UserRole.super_admin, UserRole.senior_editor)


@router.get("/{asset_id}/download", response_model=DownloadUrlOut)
async def download(
    asset_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    url = await asset_service.issue_download_url(db, asset_id)
    return {"url": url, "expires_in": asset_service.DOWNLOAD_URL_TTL}


@router.delete("/{asset_id}")
async def delete_asset(
    asset_id: UUID,
    user: User = Depends(require_role(*EDITORS)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await asset_service.delete(db, asset_id)
    return {"message": "Asset deleted"}
