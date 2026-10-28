import logging

import httpx
from sqlalchemy import func, select, update

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models import Post, PostStatus
from app.services import audit_service, cache_service
from app.services.seo_service import invalidate_sitemap

logger = logging.getLogger(__name__)


async def _revalidate_frontend(slugs: list[str]) -> None:
    if not settings.frontend_url or not slugs:
        return
    url = f"{settings.frontend_url.rstrip('/')}/api/revalidate"
    headers = {"Content-Type": "application/json"}
    if settings.revalidate_secret:
        headers["x-revalidate-secret"] = settings.revalidate_secret

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            for slug in slugs:
                await client.post(
                    url,
                    json={"slug": slug, "revalidateHome": True},
                    headers=headers,
                )
    except Exception:
        logger.warning("Could not trigger frontend ISR revalidation for %s", slugs, exc_info=True)


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

    slugs = [row[1] for row in due if row[1]]
    await _revalidate_frontend(slugs)
