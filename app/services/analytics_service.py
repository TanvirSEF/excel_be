import math
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException, PermissionDeniedException
from app.models import Post, PostStatus, PostView, User
from app.schemas.analytics import (
    DailyViews,
    OverviewAnalytics,
    PostAnalytics,
    ReferrerStat,
    TopPost,
    TrendingPost,
)
from app.schemas.traffic import (
    LiveTrafficStats,
    TrafficFeedResponse,
    TrafficLogItem,
)
from app.services import role_service

utc_date = func.date(func.timezone("UTC", PostView.viewed_at))


async def post_analytics(db: AsyncSession, user: User, post_id: UUID) -> PostAnalytics:
    post = await db.scalar(
        select(Post).where(Post.id == post_id, Post.deleted_at.is_(None))
    )
    if post is None:
        raise NotFoundException("Post not found", code="POST_NOT_FOUND")

    if post.author_id != user.id and not await role_service.has_permission(db, user, "posts:publish"):
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

    daily_rows = (
        await db.execute(
            select(utc_date.label("day"), func.count())
            .where(PostView.viewed_at >= seven_days_ago)
            .group_by("day")
        )
    ).all()
    by_day = {row[0]: row[1] for row in daily_rows}
    today = now.date()
    daily_series = [
        DailyViews(date=today - timedelta(days=offset), views=by_day.get(today - timedelta(days=offset), 0))
        for offset in range(6, -1, -1)
    ]

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
        daily_views_7_days=daily_series,
    )


async def traffic_feed(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 25,
    country_code: str | None = None,
    device_type: str | None = None,
    search: str | None = None,
) -> TrafficFeedResponse:
    now = datetime.now(timezone.utc)
    thirty_mins_ago = now - timedelta(minutes=30)
    today_start = datetime.combine(now.date(), datetime.min.time(), tzinfo=timezone.utc)

    conditions = [Post.deleted_at.is_(None)]
    if country_code and country_code.strip():
        conditions.append(PostView.country_code == country_code.strip().upper())
    if device_type and device_type.strip():
        conditions.append(PostView.device_type == device_type.strip().lower())
    if search and search.strip():
        pattern = f"%{search.strip()}%"
        conditions.append(
            Post.title.ilike(pattern) | PostView.ip_address.ilike(pattern) | PostView.city.ilike(pattern)
        )

    total = await db.scalar(
        select(func.count())
        .select_from(PostView)
        .join(Post, PostView.post_id == Post.id)
        .where(*conditions)
    ) or 0

    page = max(1, page)
    page_size = max(5, min(page_size, 100))
    offset = (page - 1) * page_size

    rows = (
        await db.execute(
            select(PostView, Post.title, Post.slug)
            .join(Post, PostView.post_id == Post.id)
            .where(*conditions)
            .order_by(PostView.viewed_at.desc())
            .offset(offset)
            .limit(page_size)
        )
    ).all()

    items = [
        TrafficLogItem(
            id=pv.id,
            viewed_at=pv.viewed_at,
            post_id=pv.post_id,
            post_title=title,
            post_slug=slug,
            ip_address=pv.ip_address,
            country=pv.country,
            country_code=pv.country_code,
            city=pv.city,
            device_type=pv.device_type,
            browser=pv.browser,
            os=pv.os,
            referrer=pv.referrer,
        )
        for pv, title, slug in rows
    ]

    active_30m = await db.scalar(
        select(func.count())
        .select_from(PostView)
        .where(PostView.viewed_at >= thirty_mins_ago)
    ) or 0

    today_views = await db.scalar(
        select(func.count())
        .select_from(PostView)
        .where(PostView.viewed_at >= today_start)
    ) or 0

    top_country_row = (
        await db.execute(
            select(PostView.country, PostView.country_code, func.count())
            .where(PostView.country.is_not(None), PostView.viewed_at >= today_start)
            .group_by(PostView.country, PostView.country_code)
            .order_by(func.count().desc())
            .limit(1)
        )
    ).first()

    top_device_row = (
        await db.execute(
            select(PostView.device_type, func.count())
            .where(PostView.device_type.is_not(None), PostView.viewed_at >= today_start)
            .group_by(PostView.device_type)
            .order_by(func.count().desc())
            .limit(1)
        )
    ).first()

    stats = LiveTrafficStats(
        active_30m=active_30m,
        today_views=today_views,
        top_country=top_country_row[0] if top_country_row else None,
        top_country_code=top_country_row[1] if top_country_row else None,
        top_device=top_device_row[0] if top_device_row else None,
    )

    total_pages = math.ceil(total / page_size) if total else 0

    return TrafficFeedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        stats=stats,
    )
