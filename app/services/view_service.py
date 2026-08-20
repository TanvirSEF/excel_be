import json
import logging
from datetime import datetime, timezone
from hashlib import sha256

from app.core.config import settings
from app.core.redis_client import get_redis
from app.utils.bot_detect import is_bot

logger = logging.getLogger(__name__)

VIEW_QUEUE = "views:queue"
BUFFER_TTL = 7 * 24 * 3600


def _hash_ip(ip: str | None) -> str | None:
    if not ip:
        return None
    salted = f"{ip}:{settings.secret_key}".encode()
    return sha256(salted).hexdigest()


async def register_view(
    post_id: str, user_agent: str | None, ip: str | None, referrer: str | None
) -> None:
    if is_bot(user_agent):
        return

    event = {
        "post_id": post_id,
        "ip_hash": _hash_ip(ip),
        "referrer": referrer,
        "user_agent": user_agent,
        "viewed_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        r = get_redis()
        async with r.pipeline(transaction=False) as pipe:
            pipe.lpush(VIEW_QUEUE, json.dumps(event))
            pipe.expire(VIEW_QUEUE, BUFFER_TTL)
            await pipe.execute()
    except Exception:
        logger.warning("Redis unavailable, view not counted for post %s", post_id)
