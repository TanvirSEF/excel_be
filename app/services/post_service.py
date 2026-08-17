import html
import math
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ConflictException,
    NotFoundException,
    PermissionDeniedException,
    ValidationException,
)
from app.deps.pagination import PaginationParams
from app.models import Category, Post, PostStatus, PostTag, Tag, User, UserRole
from app.schemas.post import PostAdminItem, PostCreate, PostDetail, PostUpdate, SeoUpdate
from app.utils.reading_time import reading_time_minutes
from app.utils.slugify import slugify

EDITORS = (UserRole.super_admin, UserRole.senior_editor)
WRITERS = (UserRole.super_admin, UserRole.senior_editor, UserRole.technical_writer)
WRITER_EDITABLE = (PostStatus.draft, PostStatus.pending_review, PostStatus.rejected)


async def list_public(
    db: AsyncSession,
    pagination: PaginationParams,
    category: str | None = None,
    tag: str | None = None,
    trending: bool | None = None,
    author_id: UUID | None = None,
) -> dict:
    conditions = [Post.status == PostStatus.published, Post.deleted_at.is_(None)]

    if category is not None:
        cat = await db.scalar(select(Category).where(Category.slug == category))
        if cat is None:
            raise NotFoundException("Category not found", code="CATEGORY_NOT_FOUND")
        conditions.append(Post.category_id == cat.id)

    if tag is not None:
        conditions.append(
            Post.id.in_(
                select(PostTag.post_id).join(Tag, PostTag.tag_id == Tag.id).where(Tag.slug == tag)
            )
        )

    if trending is not None:
        conditions.append(Post.is_trending == trending)

    if author_id is not None:
        conditions.append(Post.author_id == author_id)

    return await _page(db, pagination, conditions)


async def get_by_slug(db: AsyncSession, slug: str) -> PostDetail:
    post = await db.scalar(
        select(Post).where(
            Post.slug == slug,
            Post.status == PostStatus.published,
            Post.deleted_at.is_(None),
        )
    )
    if post is None:
        raise NotFoundException("Post not found", code="POST_NOT_FOUND")
    return await _to_detail(db, post)


async def admin_list(
    db: AsyncSession,
    user: User,
    pagination: PaginationParams,
    status: PostStatus | None = None,
) -> dict:
    conditions = [Post.deleted_at.is_(None)]

    if user.role == UserRole.technical_writer:
        conditions.append(Post.author_id == user.id)
    if status is not None:
        conditions.append(Post.status == status)

    total = await db.scalar(select(func.count()).select_from(Post).where(*conditions))
    rows = (
        await db.execute(
            select(Post, User.name, Category.name)
            .join(User, Post.author_id == User.id)
            .outerjoin(Category, Post.category_id == Category.id)
            .where(*conditions)
            .order_by(Post.updated_at.desc())
            .offset(pagination.offset)
            .limit(pagination.page_size)
        )
    ).all()

    items = []
    for post, author_name, category_name in rows:
        item = PostAdminItem.model_validate(post)
        item.author_name = author_name
        item.category_name = category_name
        items.append(item)

    return {
        "items": items,
        "total": total,
        "page": pagination.page,
        "page_size": pagination.page_size,
        "total_pages": math.ceil(total / pagination.page_size) if total else 0,
    }


async def create(db: AsyncSession, user: User, data: PostCreate) -> PostDetail:
    if data.category_id is not None:
        await _category_or_404(db, data.category_id)

    if data.slug:
        slug = data.slug
        if await db.scalar(select(Post).where(Post.slug == slug)):
            raise ConflictException("Slug already taken", code="SLUG_TAKEN")
    else:
        slug = await _unique_slug(db, slugify(data.title))
        if not slug:
            raise ValidationException(
                "Could not generate a slug from this title", code="SLUG_REQUIRED"
            )

    post = Post(
        title=data.title,
        slug=slug,
        excerpt=data.excerpt,
        content_json=data.content_json,
        content_html=_render_html(data.content_json),
        featured_image_url=data.featured_image_url,
        author_id=user.id,
        category_id=data.category_id,
        status=PostStatus.draft,
        reading_time_minutes=reading_time_minutes(data.content_json),
        meta_title=data.meta_title,
        meta_description=data.meta_description,
        canonical_url=data.canonical_url,
        og_image_url=data.og_image_url,
        schema_type=data.schema_type or "TechArticle",
    )
    db.add(post)
    await db.commit()
    await db.refresh(post)
    return await _to_detail(db, post)


async def update(db: AsyncSession, user: User, post_id: UUID, data: PostUpdate) -> PostDetail:
    post = await _get_or_404(db, post_id)

    if not _can_edit(user, post):
        raise PermissionDeniedException()

    fields = data.model_fields_set

    if "slug" in fields and data.slug and data.slug != post.slug:
        if await db.scalar(select(Post).where(Post.slug == data.slug)):
            raise ConflictException("Slug already taken", code="SLUG_TAKEN")

    if "category_id" in fields and data.category_id is not None:
        await _category_or_404(db, data.category_id)

    for field in fields:
        setattr(post, field, getattr(data, field))

    if "content_json" in fields:
        post.content_html = _render_html(post.content_json)
        post.reading_time_minutes = reading_time_minutes(post.content_json)

    await db.commit()
    await db.refresh(post)
    return await _to_detail(db, post)


async def soft_delete(db: AsyncSession, user: User, post_id: UUID) -> None:
    post = await _get_or_404(db, post_id)

    if user.role not in EDITORS:
        raise PermissionDeniedException()

    post.deleted_at = datetime.now(timezone.utc)
    await db.commit()


async def submit_review(db: AsyncSession, user: User, post_id: UUID) -> PostDetail:
    post = await _get_or_404(db, post_id)

    if user.role not in EDITORS and post.author_id != user.id:
        raise PermissionDeniedException()

    if post.status not in (PostStatus.draft, PostStatus.rejected):
        raise ValidationException(
            f"Cannot submit a post in status '{post.status.value}' for review",
            code="INVALID_TRANSITION",
        )

    post.status = PostStatus.pending_review
    post.rejection_reason = None
    await db.commit()
    await db.refresh(post)
    return await _to_detail(db, post)


async def publish(db: AsyncSession, user: User, post_id: UUID) -> PostDetail:
    post = await _get_or_404(db, post_id)

    if user.role not in EDITORS:
        raise PermissionDeniedException()

    if post.status != PostStatus.pending_review:
        raise ValidationException(
            f"Cannot publish a post in status '{post.status.value}'",
            code="INVALID_TRANSITION",
        )

    post.status = PostStatus.published
    post.published_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(post)
    return await _to_detail(db, post)


async def reject(db: AsyncSession, user: User, post_id: UUID, reason: str) -> PostDetail:
    post = await _get_or_404(db, post_id)

    if user.role not in EDITORS:
        raise PermissionDeniedException()

    if post.status != PostStatus.pending_review:
        raise ValidationException(
            f"Cannot reject a post in status '{post.status.value}'",
            code="INVALID_TRANSITION",
        )

    post.status = PostStatus.rejected
    post.rejection_reason = reason
    await db.commit()
    await db.refresh(post)
    return await _to_detail(db, post)


async def schedule(
    db: AsyncSession, user: User, post_id: UUID, scheduled_at: datetime
) -> PostDetail:
    post = await _get_or_404(db, post_id)

    if user.role not in EDITORS:
        raise PermissionDeniedException()

    if post.status not in (PostStatus.draft, PostStatus.pending_review):
        raise ValidationException(
            f"Cannot schedule a post in status '{post.status.value}'",
            code="INVALID_TRANSITION",
        )

    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
    if scheduled_at <= datetime.now(timezone.utc):
        raise ValidationException(
            "scheduled_at must be in the future", code="INVALID_SCHEDULE"
        )

    post.status = PostStatus.scheduled
    post.scheduled_at = scheduled_at
    await db.commit()
    await db.refresh(post)
    return await _to_detail(db, post)


async def update_seo(db: AsyncSession, post_id: UUID, data: SeoUpdate) -> PostDetail:
    post = await _get_or_404(db, post_id)

    for field in data.model_fields_set:
        setattr(post, field, getattr(data, field))

    await db.commit()
    await db.refresh(post)
    return await _to_detail(db, post)


async def _page(db: AsyncSession, pagination: PaginationParams, conditions: list) -> dict:
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


async def _to_detail(db: AsyncSession, post: Post) -> PostDetail:
    author_name = await db.scalar(select(User.name).where(User.id == post.author_id))
    category = None
    if post.category_id is not None:
        category = await db.scalar(select(Category).where(Category.id == post.category_id))

    detail = PostDetail.model_validate(post)
    detail.author_name = author_name
    detail.category_name = category.name if category else None
    detail.category_slug = category.slug if category else None
    return detail


async def _get_or_404(db: AsyncSession, post_id: UUID) -> Post:
    post = await db.scalar(
        select(Post).where(Post.id == post_id, Post.deleted_at.is_(None))
    )
    if post is None:
        raise NotFoundException("Post not found", code="POST_NOT_FOUND")
    return post


async def _category_or_404(db: AsyncSession, category_id: UUID) -> None:
    category = await db.scalar(select(Category).where(Category.id == category_id))
    if category is None:
        raise NotFoundException("Category not found", code="CATEGORY_NOT_FOUND")


async def _unique_slug(db: AsyncSession, base: str) -> str | None:
    if not base:
        return None
    candidate = base
    counter = 2
    while await db.scalar(select(Post).where(Post.slug == candidate)):
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


def _can_edit(user: User, post: Post) -> bool:
    if user.role in EDITORS:
        return True
    return post.author_id == user.id and post.status in WRITER_EDITABLE


def _render_html(content_json: dict) -> str:
    rendered = []
    for block in content_json.get("blocks", []):
        if not isinstance(block, dict):
            continue
        if block.get("html"):
            rendered.append(block["html"])
            continue
        text = html.escape(block.get("text", ""))
        block_type = block.get("type", "paragraph")
        if block_type == "heading":
            rendered.append(f"<h2>{text}</h2>")
        elif block_type == "quote":
            rendered.append(f"<blockquote>{text}</blockquote>")
        elif block_type == "code":
            rendered.append(f"<pre><code>{text}</code></pre>")
        elif block_type == "list":
            items = "".join(f"<li>{html.escape(i)}</li>" for i in block.get("items", []))
            rendered.append(f"<ul>{items}</ul>")
        else:
            rendered.append(f"<p>{text}</p>")
    return "\n".join(rendered)
