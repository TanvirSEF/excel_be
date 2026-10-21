import json
import logging
from datetime import datetime, timezone
from hashlib import sha256
from time import time
from urllib.parse import urlparse

from app.core.config import settings
from app.core.redis_client import get_redis
from app.schemas.track import TrackPayload
from app.utils.bot_detect import BOT_THRESHOLD, bot_score
from app.utils.traffic_source import classify_source
from app.utils.ua_parse import parse_user_agent

logger = logging.getLogger(__name__)

ANALYTICS_QUEUE = "analytics:queue"
LIVE_VISITORS = "analytics:live:visitors"
LIVE_VIEWS = "analytics:live:views"
LIVE_VISITOR_PREFIX = "analytics:live:v:"
LIVE_WINDOW_SECONDS = 300
LIVE_VIEWS_WINDOW_SECONDS = 1800
BUFFER_TTL = 7 * 24 * 3600
BURST_PAGEVIEWS_PER_MINUTE = 30
IGNORED_PREFIXES = ("/dashboard", "/api", "/login", "/_next")


def normalize_path(raw: str) -> str | None:
    path = urlparse(raw).path or "/"
    if not path.startswith("/"):
        path = f"/{path}"
    if path.startswith(IGNORED_PREFIXES):
        return None
    return path[:300]


def _hash_ip(ip: str | None) -> str | None:
    if not ip:
        return None
    return sha256(f"{ip}:{settings.secret_key}".encode()).hexdigest()


async def _is_burst(visitor_id: str) -> bool:
    r = get_redis()
    key = f"analytics:rate:{visitor_id}"
    count = await r.incr(key)
    if count == 1:
        await r.expire(key, 60)
    return count > BURST_PAGEVIEWS_PER_MINUTE


async def ingest(
    payload: TrackPayload,
    user_agent: str | None,
    ip: str | None,
    country: str | None,
    city: str | None,
) -> None:
    path = normalize_path(payload.path)
    if path is None:
        return

    is_pageview = payload.type == "pageview"
    burst = False
    if is_pageview:
        try:
            burst = await _is_burst(str(payload.visitor_id))
        except Exception:
            logger.warning("Redis unavailable, skipping burst check")

    score = bot_score(
        user_agent,
        webdriver=payload.webdriver,
        has_languages=payload.has_languages,
        screen_width=payload.screen_width,
        screen_height=payload.screen_height,
        burst=burst,
    )
    device, browser, system = parse_user_agent(user_agent)
    own_host = urlparse(settings.frontend_url).hostname or ""
    source, medium, ref_host = classify_source(
        payload.referrer, payload.utm_source, payload.utm_medium, own_host
    )
    country_code = country.upper() if country and len(country) == 2 else None

    event = {
        "type": payload.type,
        "session_id": str(payload.session_id),
        "visitor_id": str(payload.visitor_id),
        "page_id": str(payload.page_id),
        "path": path,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "engaged_ms": payload.engaged_ms,
        "scroll": payload.scroll,
        "interacted": payload.interacted,
        "returning": payload.returning,
        "bot_score": score,
        "device": device,
        "browser": browser,
        "os": system,
        "country": country_code,
        "city": city[:100] if city else None,
        "ip_hash": _hash_ip(ip),
        "source": source,
        "medium": medium,
        "referrer_host": ref_host,
        "utm_campaign": payload.utm_campaign,
    }

    try:
        r = get_redis()
        async with r.pipeline(transaction=False) as pipe:
            pipe.lpush(ANALYTICS_QUEUE, json.dumps(event))
            pipe.expire(ANALYTICS_QUEUE, BUFFER_TTL)
            if score < BOT_THRESHOLD:
                _touch_live(pipe, event, is_pageview)
            await pipe.execute()
    except Exception:
        logger.warning("Redis unavailable, analytics event dropped")


def _touch_live(pipe, event: dict, is_pageview: bool) -> None:
    now = time()
    visitor_id = event["visitor_id"]
    pipe.zadd(LIVE_VISITORS, {visitor_id: now})
    pipe.zremrangebyscore(LIVE_VISITORS, 0, now - LIVE_WINDOW_SECONDS * 2)
    pipe.set(
        f"{LIVE_VISITOR_PREFIX}{visitor_id}",
        json.dumps(
            {
                "path": event["path"],
                "country": event["country"],
                "device": event["device"],
                "source": event["source"],
            }
        ),
        ex=LIVE_WINDOW_SECONDS * 2,
    )
    if is_pageview:
        pipe.zadd(LIVE_VIEWS, {event["page_id"]: now})
        pipe.zremrangebyscore(LIVE_VIEWS, 0, now - LIVE_VIEWS_WINDOW_SECONDS)
