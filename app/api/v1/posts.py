from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.deps.auth_deps import get_current_user, require_role
from app.deps.pagination import PaginationParams
from app.models import PostStatus, User, UserRole
from app.schemas.common import Page
from app.schemas.comment import CommentCreate, CommentOut
from app.schemas.post import (
    PostAdminItem,
    PostCreate,
    PostDetail,
    PostListItem,
    PostUpdate,
    RejectRequest,
    ScheduleRequest,
    SeoUpdate,
)
from app.services import comment_service, post_service

router = APIRouter(prefix="/posts", tags=["posts"])

WRITERS = (UserRole.super_admin, UserRole.senior_editor, UserRole.technical_writer)
EDITORS = (UserRole.super_admin, UserRole.senior_editor)
SEO_ALLOWED = (UserRole.super_admin, UserRole.senior_editor, UserRole.seo_specialist)


@router.get("", response_model=Page[PostListItem])
async def list_public(
    pagination: PaginationParams = Depends(),
    category: str | None = Query(None),
    tag: str | None = Query(None),
    trending: bool | None = Query(None),
    author: UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await post_service.list_public(db, pagination, category, tag, trending, author)


@router.get("/admin", response_model=Page[PostAdminItem])
async def admin_list(
    pagination: PaginationParams = Depends(),
    status: PostStatus | None = Query(None),
    user: User = Depends(require_role(*WRITERS)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await post_service.admin_list(db, user, pagination, status)


@router.get("/{slug}", response_model=PostDetail)
async def get_by_slug(slug: str, request: Request, db: AsyncSession = Depends(get_db)) -> PostDetail:
    return await post_service.get_by_slug(
        db,
        slug,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
        referrer=request.headers.get("referer"),
    )


@router.get("/{post_id}/comments", response_model=list[CommentOut])
async def list_comments(
    post_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> list[CommentOut]:
    return await comment_service.list_for_post(db, post_id)


@router.post("/{post_id}/comments", status_code=201)
async def create_comment(
    post_id: UUID,
    data: CommentCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    ip = request.client.host if request.client else None
    comment = await comment_service.create(db, post_id, data, ip)
    return {"id": str(comment.id), "status": comment.status.value}


@router.post("", response_model=PostDetail, status_code=201)
async def create(
    data: PostCreate,
    user: User = Depends(require_role(*WRITERS)),
    db: AsyncSession = Depends(get_db),
) -> PostDetail:
    return await post_service.create(db, user, data)


@router.patch("/{post_id}", response_model=PostDetail)
async def update(
    post_id: UUID,
    data: PostUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PostDetail:
    return await post_service.update(db, user, post_id, data)


@router.delete("/{post_id}")
async def soft_delete(
    post_id: UUID,
    user: User = Depends(require_role(*EDITORS)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await post_service.soft_delete(db, user, post_id)
    return {"message": "Post deleted"}


@router.post("/{post_id}/submit-review", response_model=PostDetail)
async def submit_review(
    post_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PostDetail:
    return await post_service.submit_review(db, user, post_id)


@router.post("/{post_id}/publish", response_model=PostDetail)
async def publish(
    post_id: UUID,
    user: User = Depends(require_role(*EDITORS)),
    db: AsyncSession = Depends(get_db),
) -> PostDetail:
    return await post_service.publish(db, user, post_id)


@router.post("/{post_id}/reject", response_model=PostDetail)
async def reject(
    post_id: UUID,
    data: RejectRequest,
    user: User = Depends(require_role(*EDITORS)),
    db: AsyncSession = Depends(get_db),
) -> PostDetail:
    return await post_service.reject(db, user, post_id, data.reason)


@router.post("/{post_id}/schedule", response_model=PostDetail)
async def schedule_post(
    post_id: UUID,
    data: ScheduleRequest,
    user: User = Depends(require_role(*EDITORS)),
    db: AsyncSession = Depends(get_db),
) -> PostDetail:
    return await post_service.schedule(db, user, post_id, data.scheduled_at)


@router.patch("/{post_id}/seo", response_model=PostDetail)
async def update_seo(
    post_id: UUID,
    data: SeoUpdate,
    user: User = Depends(require_role(*SEO_ALLOWED)),
    db: AsyncSession = Depends(get_db),
) -> PostDetail:
    return await post_service.update_seo(db, post_id, data)
