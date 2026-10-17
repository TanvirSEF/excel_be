from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.deps.pagination import PaginationParams
from app.schemas.author import AuthorOut
from app.schemas.common import Page
from app.schemas.post import PostListItem
from app.services import author_service

router = APIRouter(prefix="/authors", tags=["authors"])


@router.get("/{identifier}", response_model=AuthorOut)
async def get_author(
    identifier: str,
    db: AsyncSession = Depends(get_db),
) -> AuthorOut:
    return await author_service.get_public_author(db, identifier)


@router.get("/{identifier}/posts", response_model=Page[PostListItem])
async def list_author_posts(
    identifier: str,
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await author_service.list_posts(db, identifier, pagination)
