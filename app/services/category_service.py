import math
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictException, NotFoundException, ValidationException
from app.deps.pagination import PaginationParams
from app.models import Category, Post, PostStatus
from app.schemas.category import CategoryCreate, CategoryOut, CategoryUpdate, ReorderItem
from app.utils.slugify import slugify


async def get_tree(db: AsyncSession) -> list[CategoryOut]:
    categories = (
        await db.scalars(select(Category).order_by(Category.order_index, Category.name))
    ).all()

    children_of: dict[UUID | None, list[Category]] = {}
    for category in categories:
        children_of.setdefault(category.parent_id, []).append(category)

    def to_node(category: Category) -> CategoryOut:
        out = CategoryOut.model_validate(category)
        out.children = [to_node(child) for child in children_of.get(category.id, [])]
        return out

    return [to_node(root) for root in children_of.get(None, [])]


async def get_by_slug(
    db: AsyncSession, slug: str, pagination: PaginationParams
) -> dict:
    category = await db.scalar(select(Category).where(Category.slug == slug))
    if category is None:
        raise NotFoundException("Category not found", code="CATEGORY_NOT_FOUND")

    conditions = (
        Post.category_id == category.id,
        Post.status == PostStatus.published,
        Post.deleted_at.is_(None),
    )
    total = await db.scalar(select(func.count()).select_from(Post).where(*conditions))
    posts = (
        await db.scalars(
            select(Post)
            .where(*conditions)
            .order_by(Post.published_at.desc())
            .offset(pagination.offset)
            .limit(pagination.page_size)
        )
    ).all()

    return {
        "category": CategoryOut.model_validate(category),
        "posts": {
            "items": posts,
            "total": total,
            "page": pagination.page,
            "page_size": pagination.page_size,
            "total_pages": math.ceil(total / pagination.page_size) if total else 0,
        },
    }


async def create(db: AsyncSession, data: CategoryCreate) -> Category:
    slug = data.slug or slugify(data.name)
    if not slug:
        raise ValidationException("Could not generate a slug from this name", code="SLUG_REQUIRED")

    existing = await db.scalar(select(Category).where(Category.slug == slug))
    if existing is not None:
        raise ConflictException("Slug already taken", code="SLUG_TAKEN")

    if data.parent_id is not None:
        await _get_or_404(db, data.parent_id)

    category = Category(
        name=data.name,
        slug=slug,
        parent_id=data.parent_id,
        order_index=data.order_index,
        description=data.description,
        icon_url=data.icon_url,
        color_hex=data.color_hex,
        is_featured=data.is_featured,
        seo_title=data.seo_title,
        seo_description=data.seo_description,
    )
    db.add(category)
    await db.commit()
    await db.refresh(category)
    return category


async def update(db: AsyncSession, category_id: UUID, data: CategoryUpdate) -> Category:
    category = await _get_or_404(db, category_id)

    fields = data.model_fields_set

    if "slug" in fields and data.slug and data.slug != category.slug:
        existing = await db.scalar(select(Category).where(Category.slug == data.slug))
        if existing is not None:
            raise ConflictException("Slug already taken", code="SLUG_TAKEN")

    if "parent_id" in fields and data.parent_id is not None:
        if data.parent_id == category.id:
            raise ValidationException("A category cannot be its own parent", code="INVALID_PARENT")
        await _get_or_404(db, data.parent_id)
        if await _creates_cycle(db, category.id, data.parent_id):
            raise ValidationException(
                "Cannot move a category under its own descendant", code="CYCLE"
            )

    for field in fields:
        setattr(category, field, getattr(data, field))

    await db.commit()
    await db.refresh(category)
    return category


async def reorder(db: AsyncSession, items: list[ReorderItem]) -> None:
    categories = {
        c.id: c for c in (await db.scalars(select(Category))).all()
    }

    for item in items:
        if item.id not in categories:
            raise NotFoundException("Category not found", code="CATEGORY_NOT_FOUND")
        if item.parent_id is not None:
            if item.parent_id == item.id:
                raise ValidationException("A category cannot be its own parent", code="INVALID_PARENT")
            if item.parent_id not in categories:
                raise NotFoundException("Parent category not found", code="CATEGORY_NOT_FOUND")

    for item in items:
        category = categories[item.id]
        category.order_index = item.order_index
        category.parent_id = item.parent_id

    for category_id in categories:
        parent = categories[category_id].parent_id
        seen = set()
        while parent is not None:
            if parent in seen or parent == category_id:
                raise ValidationException("Reorder would create a cycle", code="CYCLE")
            seen.add(parent)
            parent = categories[parent].parent_id if parent in categories else None

    await db.commit()


async def delete(db: AsyncSession, category_id: UUID) -> None:
    category = await _get_or_404(db, category_id)

    children = await db.scalar(
        select(func.count()).select_from(Category).where(Category.parent_id == category.id)
    )
    if children:
        raise ConflictException(
            "Category has subcategories, move or delete them first", code="CATEGORY_HAS_CHILDREN"
        )

    posts = await db.scalar(
        select(func.count()).select_from(Post).where(Post.category_id == category.id)
    )
    if posts:
        raise ConflictException(
            "Category has posts, reassign them first", code="CATEGORY_HAS_POSTS"
        )

    await db.delete(category)
    await db.commit()


async def _get_or_404(db: AsyncSession, category_id: UUID) -> Category:
    category = await db.scalar(select(Category).where(Category.id == category_id))
    if category is None:
        raise NotFoundException("Category not found", code="CATEGORY_NOT_FOUND")
    return category


async def _creates_cycle(db: AsyncSession, category_id: UUID, new_parent_id: UUID) -> bool:
    parent_id = new_parent_id
    while parent_id is not None:
        if parent_id == category_id:
            return True
        parent = await db.scalar(select(Category).where(Category.id == parent_id))
        parent_id = parent.parent_id if parent else None
    return False
