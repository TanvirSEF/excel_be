import math

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps.pagination import PaginationParams
from app.models import Post, PostStatus

FUZZY_MIN_LENGTH = 3


async def search(db: AsyncSession, pagination: PaginationParams, q: str) -> dict:
    query = func.websearch_to_tsquery("english", q)
    match = Post.content_tsv.op("@@")(query)
    rank = func.ts_rank_cd(Post.content_tsv, query)

    result = await _page(db, pagination, match, (rank.desc(), Post.published_at.desc()))
    if result["total"] == 0 and len(q) >= FUZZY_MIN_LENGTH:
        fuzzy = Post.title.op("%>")(q)
        similarity = func.word_similarity(q, Post.title)
        result = await _page(db, pagination, fuzzy, (similarity.desc(),))
    return result


async def _page(
    db: AsyncSession,
    pagination: PaginationParams,
    match: ColumnElement[bool],
    order_by: tuple,
) -> dict:
    conditions = (Post.status == PostStatus.published, Post.deleted_at.is_(None), match)

    total = await db.scalar(select(func.count()).select_from(Post).where(*conditions))
    posts = (
        await db.scalars(
            select(Post)
            .where(*conditions)
            .order_by(*order_by)
            .offset(pagination.offset)
            .limit(pagination.page_size)
        )
    ).all()

    return {
        "items": posts,
        "total": total,
        "page": pagination.page,
        "page_size": pagination.page_size,
        "total_pages": math.ceil(total / pagination.page_size) if total else 0,
    }
