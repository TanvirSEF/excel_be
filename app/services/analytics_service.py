from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException, PermissionDeniedException
from app.models import Post, PostStatus, PostView, User, UserRole
from app.schemas.analytics import (
    DailyViews,
    OverviewAnalytics,
    PostAnalytics,
    ReferrerStat,
    TopPost,
    TrendingPost,
)

EDITORS = (UserRole.super_admin, UserRole.senior_editor)
ANALYTICS_VIEWERS = (UserRole.super_admin, UserRole.senior_editor, UserRole.seo_specialist)

utc_date = func.date(func.timezone("UTC", PostView.viewed_at))


async def post_analytics(db: AsyncSession, user: User, post_id: UUID) -> PostAnalytics:
    post = await db.scalar(
        select(Post).where(Post.id == post_id, Post.deleted_at.is_(None))
    )
    if post is None:
        raise NotFoundException("Post not found", code="POST_NOT_FOUND")

    if user.role not in EDITORS and post.author_id != user.id:
        raise PermissionDeniedException()

    now = datetime.now(timezone.utc)
    seven_days_ago = now - timedelta(days=7)
    thirty_days_ago = now - timedelta(days=30)

    daily_rows = (
        await db.execute(
            select(utc_date.label("day"), func.count())
            .where(PostView.post_id == post_id, PostView.viewed_at >= seven_days_ago)
            .group_by("day")
        )
    ).all()
    by_day = {row[0]: row[1] for row in daily_rows}

    today = now.date()
    series = [
        DailyViews(date=today - timedelta(days=offset), views=by_day.get(today - timedelta(days=offset), 0))
        for offset in range(6, -1, -1)
    ]

    views_30d = await db.scalar(
        select(func.count())
        .select_from(PostView)
        .where(PostView.post_id == post_id, PostView.viewed_at >= thirty_days_ago)
    )

    unique_visitors = await db.scalar(
        select(func.count(func.distinct(PostView.ip_hash))).where(
            PostView.post_id == post_id,
            PostView.viewed_at >= thirty_days_ago,
            PostView.ip_hash.is_not(None),
        )
    )

    referrer_rows = (
        await db.execute(
            select(PostView.referrer, func.count())
            .where(
                PostView.post_id == post_id,
                PostView.viewed_at >= thirty_days_ago,
                PostView.referrer.is_not(None),
                PostView.referrer != "",
            )
            .group_by(PostView.referrer)
            .order_by(func.count().desc())
            .limit(5)
        )
    ).all()

    return PostAnalytics(
        post_id=post.id,
        title=post.title,
        slug=post.slug,
        total_views=post.view_count,
        views_last_7_days=series,
        views_last_30_days=views_30d or 0,
        unique_visitors_30_days=unique_visitors or 0,
        top_referrers_30_days=[
            ReferrerStat(referrer=row[0], views=row[1]) for row in referrer_rows
        ],
    )


async def overview(db: AsyncSession) -> OverviewAnalytics:
    now = datetime.now(timezone.utc)
    seven_days_ago = now - timedelta(days=7)

    total_posts = await db.scalar(
        select(func.count()).select_from(Post).where(Post.deleted_at.is_(None))
    )
    published_posts = await db.scalar(
        select(func.count())
        .select_from(Post)
        .where(Post.deleted_at.is_(None), Post.status == PostStatus.published)
    )
    draft_posts = await db.scalar(
        select(func.count())
        .select_from(Post)
        .where(Post.deleted_at.is_(None), Post.status == PostStatus.draft)
    )
    total_views = await db.scalar(
        select(func.coalesce(func.sum(Post.view_count), 0))
        .where(Post.deleted_at.is_(None))
    )
    views_7d = await db.scalar(
        select(func.count()).select_from(PostView).where(PostView.viewed_at >= seven_days_ago)
    )

    top_rows = (
        await db.execute(
            select(Post.id, Post.title, Post.slug, func.count().label("views"))
            .join(PostView, PostView.post_id == Post.id)
            .where(Post.deleted_at.is_(None), PostView.viewed_at >= seven_days_ago)
            .group_by(Post.id, Post.title, Post.slug)
            .order_by(func.count().desc())
            .limit(5)
        )
    ).all()

    trending_rows = (
        await db.scalars(
            select(Post)
            .where(
                Post.deleted_at.is_(None),
                Post.status == PostStatus.published,
                Post.is_trending.is_(True),
            )
            .order_by(Post.view_count.desc())
            .limit(10)
        )
    ).all()

    return OverviewAnalytics(
        total_posts=total_posts or 0,
        published_posts=published_posts or 0,
        draft_posts=draft_posts or 0,
        total_views=total_views or 0,
        views_last_7_days=views_7d or 0,
        top_posts_7_days=[
            TopPost(post_id=row[0], title=row[1], slug=row[2], views=row[3]) for row in top_rows
        ],
        trending=[TrendingPost(id=p.id, title=p.title, slug=p.slug) for p in trending_rows],
    )
