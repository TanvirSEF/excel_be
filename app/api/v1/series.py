from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.deps.pagination import PaginationParams
from app.schemas.series import SeriesOut, SeriesWithPosts
from app.services import series_service

router = APIRouter(prefix="/series", tags=["series"])


@router.get("", response_model=list[SeriesOut])
async def list_series(
    category: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    return await series_service.list_series(db, category)


@router.get("/{slug}", response_model=SeriesWithPosts)
async def get_series(
    slug: str,
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await series_service.get_by_slug(db, slug, pagination)
