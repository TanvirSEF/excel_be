import logging

from sqlalchemy import func, select, update

from app.core.database import AsyncSessionLocal
from app.models import Post, PostStatus
from app.services import audit_service, cache_service
from app.services.seo_service import invalidate_sitemap

logger = logging.getLogger(__name__)


async def publish_scheduled_posts() -> None:
    async with AsyncSessionLocal() as session:
        due = (
            await session.execute(
                select(Post.id, Post.slug).where(
                    Post.status == PostStatus.scheduled,
                    Post.scheduled_at <= func.now(),
                    Post.deleted_at.is_(None),
                )
            )
        ).all()
        if not due:
            return

        await session.execute(
            update(Post)
            .where(Post.id.in_([row[0] for row in due]))
            .values(status=PostStatus.published, published_at=func.now())
        )
        for post_id, slug in due:
            audit_service.record(
                session, None, "post.publish", "post", post_id, {"slug": slug, "source": "scheduler"}
            )
        await session.commit()
        logger.info("Published %d scheduled posts", len(due))

    await cache_service.delete_keys(*[cache_service.post_detail_key(row[1]) for row in due])
    await cache_service.delete_pattern("posts:home:*")
    await invalidate_sitemap()
