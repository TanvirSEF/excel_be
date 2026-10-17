import math
import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException
from app.deps.pagination import PaginationParams
from app.models import Post, PostStatus, User
from app.schemas.author import AuthorOut

AUTHOR_SLUG_ALIASES: dict[str, str] = {
    "nehad-ulfat": "adminexcelinsider-com",
    "nehad-ulfat-rafid": "adminexcelinsider-com",
    "nehad": "adminexcelinsider-com",
    "mashrur-ibne-shams": "mashrur-ibna-shams",
    "ashfaqur-rumon": "ashfqur-rumon",
    "rudra-nil-utsa": "rudra-nil-utsha",
}


async def _resolve_user(db: AsyncSession, identifier: str | uuid.UUID) -> User:
    if isinstance(identifier, uuid.UUID):
        author = await db.scalar(select(User).where(User.id == identifier))
    else:
        ident_str = str(identifier).strip()
        parsed_uuid: uuid.UUID | None = None
        try:
            parsed_uuid = uuid.UUID(ident_str)
        except (ValueError, AttributeError):
            parsed_uuid = None

        if parsed_uuid is not None:
            author = await db.scalar(
                select(User).where(or_(User.id == parsed_uuid, User.slug == ident_str))
            )
        else:
            canonical_slug = AUTHOR_SLUG_ALIASES.get(ident_str.lower(), ident_str)
            author = await db.scalar(
                select(User).where(
                    or_(
                        User.slug == canonical_slug,
                        func.lower(User.slug) == canonical_slug.lower(),
                        User.slug == ident_str,
                    )
                )
            )

    if author is None or not author.is_active:
        raise NotFoundException("Author not found", code="AUTHOR_NOT_FOUND")
    return author


async def get_public_author(db: AsyncSession, identifier: str | uuid.UUID) -> AuthorOut:
    author = await _resolve_user(db, identifier)

    post_count = await db.scalar(
        select(func.count()).select_from(Post).where(
            Post.author_id == author.id,
            Post.status == PostStatus.published,
            Post.deleted_at.is_(None),
        )
    )
    if not post_count:
        raise NotFoundException("Author not found", code="AUTHOR_NOT_FOUND")

    return AuthorOut(
        id=author.id,
        slug=author.slug,
        name=author.name,
        avatar_url=author.avatar_url,
        bio=author.bio,
        joined_at=author.created_at,
        post_count=post_count,
        website_url=author.website_url,
        linkedin_url=author.linkedin_url,
        twitter_url=author.twitter_url,
        github_url=author.github_url,
    )


async def list_posts(
    db: AsyncSession, identifier: str | uuid.UUID, pagination: PaginationParams
) -> dict:
    author = await _resolve_user(db, identifier)
    conditions = [
        Post.author_id == author.id,
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

