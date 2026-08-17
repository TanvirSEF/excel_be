from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.deps.pagination import PaginationParams
from app.schemas.common import Page
from app.schemas.post import PostListItem
from app.services import search_service

router = APIRouter(prefix="/search", tags=["search"])


@router.get("", response_model=Page[PostListItem])
async def search(
    q: str = Query(min_length=2),
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await search_service.search(db, pagination, q)
