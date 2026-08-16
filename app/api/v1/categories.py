from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.deps.auth_deps import require_role
from app.deps.pagination import PaginationParams
from app.models import Category, User, UserRole
from app.schemas.category import CategoryCreate, CategoryOut, CategoryUpdate, ReorderItem
from app.services import category_service

router = APIRouter(prefix="/categories", tags=["categories"])

EDITORS = (UserRole.super_admin, UserRole.senior_editor)


@router.get("", response_model=list[CategoryOut])
async def get_tree(db: AsyncSession = Depends(get_db)) -> list[CategoryOut]:
    return await category_service.get_tree(db)


@router.get("/{slug}")
async def get_by_slug(
    slug: str,
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await category_service.get_by_slug(db, slug, pagination)


@router.post("", response_model=CategoryOut, status_code=201)
async def create(
    data: CategoryCreate,
    user: User = Depends(require_role(*EDITORS)),
    db: AsyncSession = Depends(get_db),
) -> Category:
    return await category_service.create(db, data)


@router.patch("/reorder")
async def reorder(
    items: list[ReorderItem],
    user: User = Depends(require_role(*EDITORS)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await category_service.reorder(db, items)
    return {"message": "Categories reordered"}


@router.patch("/{category_id}", response_model=CategoryOut)
async def update(
    category_id: UUID,
    data: CategoryUpdate,
    user: User = Depends(require_role(*EDITORS)),
    db: AsyncSession = Depends(get_db),
) -> Category:
    return await category_service.update(db, category_id, data)


@router.delete("/{category_id}")
async def delete(
    category_id: UUID,
    user: User = Depends(require_role(UserRole.super_admin)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await category_service.delete(db, category_id)
    return {"message": "Category deleted"}
