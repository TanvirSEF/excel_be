import json
import logging
from datetime import datetime, timezone
from hashlib import sha256

from app.core.config import settings
from app.core.redis_client import get_redis
from app.utils.bot_detect import is_bot
from app.utils.country_names import get_country_name
from app.utils.user_agent import parse_user_agent

logger = logging.getLogger(__name__)

VIEW_QUEUE = "views:queue"
DEDUPE_WINDOW_SECONDS = 1800
BUFFER_TTL = 7 * 24 * 3600


def _hash_ip(ip: str | None) -> str | None:
    if not ip:
        return None
    salted = f"{ip}:{settings.secret_key}".encode()
    return sha256(salted).hexdigest()


async def register_view(
    post_id: str,
    user_agent: str | None,
    ip: str | None,
    referrer: str | None,
    country_code: str | None = None,
    city: str | None = None,
    dedupe: bool = False,
) -> None:
    if is_bot(user_agent):
        return

    ip_hash = _hash_ip(ip)
    if dedupe and ip_hash and not await _claim_view(post_id, ip_hash):
        return

    device_type, browser, os = parse_user_agent(user_agent)
    country_name = get_country_name(country_code)

    event = {
        "post_id": post_id,
        "ip_hash": ip_hash,
        "country": country_name,
        "country_code": country_code.strip().upper() if country_code else None,
        "city": city.strip() if city else None,
        "device_type": device_type,
        "browser": browser,
        "os": os,
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


async def _claim_view(post_id: str, ip_hash: str) -> bool:
    try:
        claimed = await get_redis().set(
            f"views:seen:{post_id}:{ip_hash}", "1", nx=True, ex=DEDUPE_WINDOW_SECONDS
        )
    except Exception:
        return True
    return bool(claimed)
