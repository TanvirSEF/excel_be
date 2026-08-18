import logging

from sqlalchemy import func, update

from app.core.database import AsyncSessionLocal
from app.models import Post, PostStatus
from app.services.seo_service import invalidate_sitemap

logger = logging.getLogger(__name__)


async def publish_scheduled_posts() -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            update(Post)
            .where(
                Post.status == PostStatus.scheduled,
                Post.scheduled_at <= func.now(),
                Post.deleted_at.is_(None),
            )
            .values(status=PostStatus.published, published_at=func.now())
        )
        await session.commit()
        if result.rowcount:
            logger.info("Published %d scheduled posts", result.rowcount)
            await invalidate_sitemap()
