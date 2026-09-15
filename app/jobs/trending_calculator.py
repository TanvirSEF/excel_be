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
        rows = (
            await session.execute(
                select(PostView.post_id, func.count().label("views"))
                .where(PostView.viewed_at >= since)
                .group_by(PostView.post_id)
                .having(func.count() >= MIN_VIEWS)
                .order_by(func.count().desc())
                .limit(TOP_N)
            )
        ).all()
        top_ids = [row[0] for row in rows]

        # fill the remainder with all-time most-viewed posts so the
        # trending section stays full on low-traffic days
        if len(top_ids) < TOP_N:
            fillers = (
                await session.scalars(
                    select(Post.id)
                    .where(
                        Post.status == PostStatus.published,
                        Post.deleted_at.is_(None),
                        Post.id.not_in(top_ids) if top_ids else True,
                    )
                    .order_by(Post.view_count.desc())
                    .limit(TOP_N - len(top_ids))
                )
            ).all()
            top_ids.extend(fillers)

        await session.execute(
            update(Post)
            .where(Post.status == PostStatus.published, Post.deleted_at.is_(None))
            .values(is_trending=False)
        )
        if top_ids:
            await session.execute(
                update(Post).where(Post.id.in_(top_ids)).values(is_trending=True)
            )
        await session.commit()

    await cache_service.delete_pattern("posts:trending:*")

    if top_ids:
        logger.info("Marked %d posts as trending", len(top_ids))
