import math

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException
from app.deps.pagination import PaginationParams
from app.models import Category, Post, PostStatus, Series
from app.schemas.series import SeriesOut
from app.services import cache_service


async def list_series(db: AsyncSession, category_slug: str | None = None) -> list[dict]:
    cache_key = cache_service.series_list_key(category_slug)
    cached = await cache_service.get_json(cache_key)
    if cached is not None:
        return cached

    category = None
    if category_slug is not None:
        category = await db.scalar(select(Category).where(Category.slug == category_slug))
        if category is None:
            raise NotFoundException("Category not found", code="CATEGORY_NOT_FOUND")

    published_count = (
        select(Post.series_id.label("series_id"), func.count().label("post_count"))
        .where(
            Post.series_id.is_not(None),
            Post.status == PostStatus.published,
            Post.deleted_at.is_(None),
        )
        .group_by(Post.series_id)
        .subquery()
    )

    query = (
        select(Series, func.coalesce(published_count.c.post_count, 0).label("post_count"))
        .outerjoin(published_count, published_count.c.series_id == Series.id)
        .order_by(Series.order_index, Series.name)
    )
    if category is not None:
        query = query.where(Series.category_id == category.id)

    rows = (await db.execute(query)).all()

    items = []
    for series, post_count in rows:
        item = SeriesOut.model_validate(series)
        item.post_count = post_count
        items.append(item.model_dump(mode="json"))

    await cache_service.set_json(cache_key, items, cache_service.TREE_TTL)
    return items


async def get_by_slug(db: AsyncSession, slug: str, pagination: PaginationParams) -> dict:
    series = await db.scalar(select(Series).where(Series.slug == slug))
    if series is None:
        raise NotFoundException("Series not found", code="SERIES_NOT_FOUND")

    conditions = [
        Post.series_id == series.id,
        Post.status == PostStatus.published,
        Post.deleted_at.is_(None),
    ]

    total = await db.scalar(select(func.count()).select_from(Post).where(*conditions))
    posts = (
        await db.scalars(
            select(Post)
            .where(*conditions)
            .order_by(Post.series_order.asc().nulls_last(), Post.published_at.asc())
            .offset(pagination.offset)
            .limit(pagination.page_size)
        )
    ).all()

    series_out = SeriesOut.model_validate(series)
    series_out.post_count = total

    return {
        "series": series_out,
        "posts": {
            "items": posts,
            "total": total,
            "page": pagination.page,
            "page_size": pagination.page_size,
            "total_pages": math.ceil(total / pagination.page_size) if total else 0,
        },
    }
