import json
import logging

from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)

CATEGORY_TREE_KEY = "cat:tree"
POST_TTL = 300
LIST_TTL = 120
TREE_TTL = 600


def post_detail_key(slug: str) -> str:
    return f"post:{slug}"


def home_list_key(page: int, page_size: int) -> str:
    return f"posts:home:{page}:{page_size}"


def trending_list_key(page: int, page_size: int) -> str:
    return f"posts:trending:{page}:{page_size}"


async def get_json(key: str):
    try:
        raw = await get_redis().get(key)
    except Exception:
        logger.warning("Cache read failed for %s", key)
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        logger.warning("Cache entry %s is corrupt, treating as a miss", key)
        return None


async def set_json(key: str, value, ttl: int) -> None:
    try:
        await get_redis().set(key, json.dumps(value), ex=ttl)
    except Exception:
        logger.warning("Cache write failed for %s", key)


async def delete_keys(*keys: str) -> None:
    if not keys:
        return
    try:
        await get_redis().delete(*keys)
    except Exception:
        logger.warning("Cache delete failed for %s", keys)


async def delete_pattern(pattern: str) -> None:
    try:
        r = get_redis()
        cursor = 0
        while True:
            cursor, keys = await r.scan(cursor, match=pattern, count=100)
            if keys:
                await r.delete(*keys)
            if cursor == 0:
                return
    except Exception:
        logger.warning("Cache pattern delete failed for %s", pattern)
