import math

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException
from app.deps.pagination import PaginationParams
from app.models import Post, PostStatus, User
from app.schemas.author import AuthorOut


async def get_public_author(db: AsyncSession, user_id) -> AuthorOut:
    author = await db.scalar(select(User).where(User.id == user_id))
    if author is None or not author.is_active:
        raise NotFoundException("Author not found", code="AUTHOR_NOT_FOUND")

    post_count = await db.scalar(
        select(func.count()).select_from(Post).where(
            Post.author_id == author.id,
            Post.status == PostStatus.published,
            Post.deleted_at.is_(None),
        )
    )
    if not post_count:
        raise NotFoundException("Author not found", code="AUTHOR_NOT_FOUND")

    detail = AuthorOut.model_validate(author)
    detail.post_count = post_count
    return detail


async def list_posts(
    db: AsyncSession, user_id, pagination: PaginationParams
) -> dict:
    conditions = [
        Post.author_id == user_id,
        Post.status == PostStatus.published,
        Post.deleted_at.is_(None),
    ]

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
        "items": posts,
        "total": total,
        "page": pagination.page,
        "page_size": pagination.page_size,
        "total_pages": math.ceil(total / pagination.page_size) if total else 0,
    }
