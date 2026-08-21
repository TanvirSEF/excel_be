import math
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictException, NotFoundException
from app.deps.pagination import PaginationParams
from app.models import Comment, CommentStatus, Post, PostStatus
from app.schemas.comment import CommentAdminItem, CommentCreate, CommentOut


async def list_for_post(db: AsyncSession, post_id: UUID) -> list[CommentOut]:
    post = await db.scalar(
        select(Post).where(
            Post.id == post_id,
            Post.status == PostStatus.published,
            Post.deleted_at.is_(None),
        )
    )
    if post is None:
        raise NotFoundException("Post not found", code="POST_NOT_FOUND")

    comments = (
        await db.scalars(
            select(Comment)
            .where(Comment.post_id == post_id, Comment.status == CommentStatus.approved)
            .order_by(Comment.created_at)
        )
    ).all()

    children_of: dict[UUID | None, list[Comment]] = {}
    for comment in comments:
        children_of.setdefault(comment.parent_id, []).append(comment)

    def to_node(comment: Comment) -> CommentOut:
        out = CommentOut.model_validate(comment)
        out.children = [to_node(child) for child in children_of.get(comment.id, [])]
        return out

    return [to_node(root) for root in children_of.get(None, [])]


async def admin_list(
    db: AsyncSession,
    pagination: PaginationParams,
    status: CommentStatus | None = None,
) -> dict:
    conditions = []
    if status is not None:
        conditions.append(Comment.status == status)

    total = await db.scalar(select(func.count()).select_from(Comment).where(*conditions))
    rows = (
        await db.execute(
            select(Comment, Post.title, Post.slug)
            .join(Post, Comment.post_id == Post.id)
            .where(*conditions)
            .order_by(Comment.created_at.desc())
            .offset(pagination.offset)
            .limit(pagination.page_size)
        )
    ).all()

    items = []
    for comment, post_title, post_slug in rows:
        item = CommentAdminItem.model_validate(comment)
        item.post_title = post_title
        item.post_slug = post_slug
        items.append(item)

    return {
        "items": items,
        "total": total,
        "page": pagination.page,
        "page_size": pagination.page_size,
        "total_pages": math.ceil(total / pagination.page_size) if total else 0,
    }


async def create(db: AsyncSession, post_id: UUID, data: CommentCreate, ip: str | None) -> Comment:
    post = await db.scalar(
        select(Post).where(
            Post.id == post_id,
            Post.status == PostStatus.published,
            Post.deleted_at.is_(None),
        )
    )
    if post is None:
        raise NotFoundException("Post not found", code="POST_NOT_FOUND")

    if data.parent_id is not None:
        parent = await db.scalar(
            select(Comment).where(Comment.id == data.parent_id, Comment.post_id == post_id)
        )
        if parent is None:
            raise NotFoundException("Parent comment not found", code="COMMENT_NOT_FOUND")

    comment = Comment(
        post_id=post_id,
        parent_id=data.parent_id,
        user_name=data.user_name,
        user_email=data.user_email,
        comment_text=data.comment_text,
        status=CommentStatus.pending,
        ip_address=ip,
    )
    db.add(comment)
    await db.commit()
    await db.refresh(comment)
    return comment


async def moderate(db: AsyncSession, comment_id: UUID, status: CommentStatus) -> Comment:
    comment = await _get_or_404(db, comment_id)
    comment.status = status
    await db.commit()
    await db.refresh(comment)
    return comment


async def delete(db: AsyncSession, comment_id: UUID) -> None:
    comment = await _get_or_404(db, comment_id)

    replies = await db.scalar(
        select(func.count()).select_from(Comment).where(Comment.parent_id == comment_id)
    )
    if replies:
        raise ConflictException(
            "Comment has replies, delete them first", code="COMMENT_HAS_REPLIES"
        )

    await db.delete(comment)
    await db.commit()


async def _get_or_404(db: AsyncSession, comment_id: UUID) -> Comment:
    comment = await db.scalar(select(Comment).where(Comment.id == comment_id))
    if comment is None:
        raise NotFoundException("Comment not found", code="COMMENT_NOT_FOUND")
    return comment
