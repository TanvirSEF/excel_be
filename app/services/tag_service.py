from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictException, ValidationException
from app.models import Post, PostTag, Tag
from app.schemas.tag import TagCreate
from app.utils.slugify import slugify


async def list_tags(db: AsyncSession) -> list[Tag]:
    return (await db.scalars(select(Tag).order_by(Tag.name))).all()


async def create_tag(db: AsyncSession, data: TagCreate) -> Tag:
    slug = data.slug or slugify(data.name)
    if not slug:
        raise ValidationException("Could not generate a slug from this name", code="SLUG_REQUIRED")

    existing = await db.scalar(select(Tag).where(Tag.slug == slug))
    if existing is not None:
        raise ConflictException("Slug already taken", code="SLUG_TAKEN")

    tag = Tag(name=data.name, slug=slug)
    db.add(tag)
    await db.commit()
    await db.refresh(tag)
    return tag


async def sync_post_tags(db: AsyncSession, post: Post, names: list[str]) -> None:
    seen: set[str] = set()
    unique: list[str] = []
    for name in names:
        cleaned = name.strip()
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            unique.append(cleaned)

    await db.execute(delete(PostTag).where(PostTag.post_id == post.id))

    for name in unique:
        slug = slugify(name) or f"tag-{uuid4().hex[:8]}"
        tag = await db.scalar(select(Tag).where(Tag.slug == slug))
        if tag is None:
            tag = Tag(name=name, slug=slug)
            db.add(tag)
            await db.flush()
        db.add(PostTag(post_id=post.id, tag_id=tag.id))

    await db.commit()


async def post_tag_names(db: AsyncSession, post_id: UUID) -> list[str]:
    rows = await db.scalars(
        select(Tag.name)
        .join(PostTag, PostTag.tag_id == Tag.id)
        .where(PostTag.post_id == post_id)
        .order_by(Tag.name)
    )
    return list(rows)
