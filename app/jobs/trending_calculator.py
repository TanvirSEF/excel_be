import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update

from app.core.database import AsyncSessionLocal
from app.models import Post, PostStatus, PostView
from app.services import cache_service

logger = logging.getLogger(__name__)

WINDOW_DAYS = 7
TOP_N = 10
MIN_VIEWS = 5


async def calculate_trending() -> None:
    since = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)

    async with AsyncSessionLocal() as session:
        published = (Post.status == PostStatus.published, Post.deleted_at.is_(None))

        # manually pinned posts stay trending regardless of view activity
        pinned_ids = (
            await session.scalars(
                select(Post.id).where(Post.is_trending_pinned.is_(True), *published)
            )
        ).all()

        top_ids: list = []
        slots = max(TOP_N - len(pinned_ids), 0)
        if slots:
            rows = (
                await session.execute(
                    select(PostView.post_id, func.count().label("views"))
                    .where(PostView.viewed_at >= since)
                    .group_by(PostView.post_id)
                    .having(func.count() >= MIN_VIEWS)
                    .order_by(func.count().desc())
                    .limit(slots)
                )
            ).all()
            top_ids = [row[0] for row in rows]

            # fill the remainder with all-time most-viewed posts so the
            # trending section stays full on low-traffic days
            remaining = slots - len(top_ids)
            if remaining > 0:
                excluded = pinned_ids + top_ids
                fillers = (
                    await session.scalars(
                        select(Post.id)
                        .where(*published, Post.id.not_in(excluded) if excluded else True)
                        .order_by(Post.view_count.desc())
                        .limit(remaining)
                    )
                ).all()
                top_ids.extend(fillers)

        trending_ids = pinned_ids + top_ids

        await session.execute(
            update(Post)
            .where(*published, Post.is_trending_pinned.is_(False))
            .values(is_trending=False)
        )
        if trending_ids:
            await session.execute(
                update(Post).where(Post.id.in_(trending_ids)).values(is_trending=True)
            )
        await session.commit()

    await cache_service.delete_pattern("posts:trending:*")

    logger.info("Marked %d posts as trending (%d pinned)", len(trending_ids), len(pinned_ids))
