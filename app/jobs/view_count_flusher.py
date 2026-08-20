import json
import logging
from collections import Counter
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import insert, select, update

from app.core.database import AsyncSessionLocal
from app.core.redis_client import get_redis
from app.models import Post, PostView
from app.services.view_service import VIEW_QUEUE

logger = logging.getLogger(__name__)

BATCH_SIZE = 500
MAX_EVENTS_PER_RUN = 100_000


async def flush_view_counts() -> None:
    r = get_redis()
    total = 0
    while total < MAX_EVENTS_PER_RUN:
        batch = await r.rpop(VIEW_QUEUE, count=BATCH_SIZE)
        if not batch:
            break
        try:
            total += await _flush_batch(batch)
        except Exception:
            logger.exception("Failed to flush a batch of %d view events", len(batch))
    if total:
        logger.info("Flushed %d view events", total)


async def _flush_batch(batch: list) -> int:
    events = []
    for raw in batch:
        parsed = _parse_event(raw)
        if parsed is not None:
            events.append(parsed)
    if not events:
        return 0

    async with AsyncSessionLocal() as session:
        post_ids = {event["post_id"] for event in events}
        existing = set(
            await session.scalars(select(Post.id).where(Post.id.in_(post_ids)))
        )
        events = [event for event in events if event["post_id"] in existing]
        if not events:
            return 0

        counts = Counter(event["post_id"] for event in events)
        await session.execute(insert(PostView).values(events))
        for post_id, count in counts.items():
            await session.execute(
                update(Post).where(Post.id == post_id).values(view_count=Post.view_count + count)
            )
        await session.commit()
    return len(events)


def _parse_event(raw) -> dict | None:
    try:
        event = json.loads(raw)
        post_id = UUID(event["post_id"])
    except (ValueError, KeyError, TypeError):
        logger.warning("Dropping malformed view event")
        return None

    parsed = {"post_id": post_id}
    for field in ("ip_hash", "referrer", "user_agent"):
        value = event.get(field)
        parsed[field] = value if value else None

    viewed_at = event.get("viewed_at")
    if viewed_at:
        try:
            parsed["viewed_at"] = datetime.fromisoformat(viewed_at)
            return parsed
        except ValueError:
            pass
    parsed["viewed_at"] = datetime.now(timezone.utc)
    return parsed
