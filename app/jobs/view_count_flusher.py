import logging
from uuid import UUID

from sqlalchemy import update

from app.core.database import AsyncSessionLocal
from app.core.redis_client import get_redis
from app.models import Post, PostView

logger = logging.getLogger(__name__)


async def flush_view_counts() -> None:
    r = get_redis()
    cursor = 0
    flushed = 0
    while True:
        cursor, keys = await r.scan(cursor, match="views:pending:*", count=100)
        for key in keys:
            try:
                flushed += await _flush_one(key)
            except Exception:
                logger.exception("Failed to flush buffer %s", key)
        if cursor == 0:
            break
    if flushed:
        logger.info("Flushed view counts for %d posts", flushed)


async def _flush_one(key: str) -> int:
    post_id = key.removeprefix("views:pending:")
    count = await get_redis().getdel(key)
    if not count:
        return 0

    meta = await get_redis().hgetall(f"views:meta:{post_id}")
    await get_redis().delete(f"views:meta:{post_id}")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            update(Post).where(Post.id == UUID(post_id)).values(view_count=Post.view_count + int(count))
        )
        if not result.rowcount:
            return 0

        session.add(
            PostView(
                post_id=UUID(post_id),
                ip_hash=meta.get("ip_hash") or None,
                referrer=meta.get("referrer") or None,
                user_agent=meta.get("user_agent") or None,
            )
        )
        await session.commit()
    return 1
