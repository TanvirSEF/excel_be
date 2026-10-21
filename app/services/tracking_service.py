import ipaddress
import json
import logging
from datetime import datetime, timezone
from hashlib import sha256
from time import time
from urllib.parse import urlparse

from app.core.config import settings
from app.core.redis_client import get_redis
from app.schemas.track import TrackEvent
from app.utils.bot_detect import BOT_THRESHOLD, bot_score, is_bot
from app.utils.datacenter import is_datacenter_ip
from app.utils.traffic_source import classify_source
from app.utils.user_agent import parse_user_agent

logger = logging.getLogger(__name__)

ANALYTICS_QUEUE = "analytics:queue"
LIVE_VISITORS = "analytics:live:visitors"
LIVE_VIEWS = "analytics:live:views"
LIVE_RECENT = "analytics:live:recent"
LIVE_VISITOR_PREFIX = "analytics:live:v:"
EVENT_DEDUPE_PREFIX = "analytics:evt:"
ACTIVE_WINDOW_SECONDS = 300
LIVE_RETENTION_SECONDS = 1800
RECENT_EVENTS_LIMIT = 50
EVENT_DEDUPE_TTL = 3600
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


def is_internal_ip(ip: str | None) -> bool:
    if not ip:
        return False
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for entry in settings.internal_networks:
        try:
            if address in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            logger.warning("Ignoring invalid ANALYTICS_INTERNAL_IPS entry %r", entry)
    return False


def _event_target(name: str | None, target: str | None) -> str | None:
    if not target:
        return None
    parsed = urlparse(target if "://" in target else f"//{target}")
    if name in ("file_download", "calculator_use"):
        return parsed.path[:255] or None
    return parsed.hostname[:255] if parsed.hostname else None


async def _is_burst(visitor_id: str) -> bool:
    r = get_redis()
    key = f"analytics:rate:{visitor_id}"
    count = await r.incr(key)
    if count == 1:
        await r.expire(key, 60)
    return count > BURST_PAGEVIEWS_PER_MINUTE


async def _unseen_events(events: list[TrackEvent]) -> list[TrackEvent]:
    try:
        r = get_redis()
        async with r.pipeline(transaction=False) as pipe:
            for event in events:
                pipe.set(f"{EVENT_DEDUPE_PREFIX}{event.event_id}", "1", nx=True, ex=EVENT_DEDUPE_TTL)
            claimed = await pipe.execute()
    except Exception:
        logger.warning("Redis unavailable, skipping event deduplication")
        return events
    return [event for event, ok in zip(events, claimed) if ok]


async def ingest_batch(
    events: list[TrackEvent],
    user_agent: str | None,
    ip: str | None,
    country: str | None,
    city: str | None,
) -> int:
    fresh = await _unseen_events(events)
    internal_ip = is_internal_ip(ip)
    datacenter = await is_datacenter_ip(ip)
    accepted = 0
    for event in fresh:
        if await _ingest(event, user_agent, ip, internal_ip, datacenter, country, city):
            accepted += 1
    return accepted


async def _ingest(
    payload: TrackEvent,
    user_agent: str | None,
    ip: str | None,
    internal_ip: bool,
    datacenter: bool,
    country: str | None,
    city: str | None,
) -> bool:
    path = normalize_path(payload.path)
    if path is None:
        return False
    if payload.type == "event" and payload.name is None:
        return False

    is_pageview = payload.type == "pageview"
    burst = False
    if is_pageview:
        try:
            burst = await _is_burst(str(payload.visitor_id))
        except Exception:
            logger.warning("Redis unavailable, skipping burst check")

    ua_bot = is_bot(user_agent)
    score = bot_score(
        user_agent,
        webdriver=payload.webdriver,
        has_languages=payload.has_languages,
        screen_width=payload.screen_width,
        screen_height=payload.screen_height,
        burst=burst,
        datacenter=datacenter,
    )
    internal = internal_ip or payload.internal
    device, browser, system = parse_user_agent(user_agent)
    own_host = urlparse(settings.frontend_url).hostname or ""
    source, medium, ref_host = classify_source(
        payload.referrer, payload.utm_source, payload.utm_medium, own_host
    )
    country_code = country.upper() if country and len(country) == 2 else None

    event = {
        "event_id": str(payload.event_id),
        "type": payload.type,
        "name": payload.name,
        "target": _event_target(payload.name, payload.target),
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
        "ua_bot": ua_bot,
        "internal": internal,
        "device": device or "unknown",
        "browser": browser or "Unknown",
        "os": system or "Unknown",
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
            if not ua_bot and not internal and score < BOT_THRESHOLD and payload.type != "event":
                _touch_live(pipe, event, is_pageview)
            await pipe.execute()
    except Exception:
        logger.warning("Redis unavailable, analytics event dropped")
        return False
    return True


def _touch_live(pipe, event: dict, is_pageview: bool) -> None:
    now = time()
    visitor_id = event["visitor_id"]
    summary = {
        "path": event["path"],
        "country": event["country"],
        "device": event["device"],
        "browser": event["browser"],
        "os": event["os"],
        "source": event["source"],
        "returning": event["returning"],
    }
    pipe.zadd(LIVE_VISITORS, {visitor_id: now})
    pipe.zremrangebyscore(LIVE_VISITORS, 0, now - LIVE_RETENTION_SECONDS)
    pipe.set(f"{LIVE_VISITOR_PREFIX}{visitor_id}", json.dumps(summary), ex=LIVE_RETENTION_SECONDS)
    if is_pageview:
        pipe.zadd(LIVE_VIEWS, {event["page_id"]: now})
        pipe.zremrangebyscore(LIVE_VIEWS, 0, now - LIVE_RETENTION_SECONDS)
        pipe.lpush(
            LIVE_RECENT,
            json.dumps(
                {
                    "at": event["occurred_at"],
                    "visitor": sha256(visitor_id.encode()).hexdigest()[:8],
                    **summary,
                }
            ),
        )
        pipe.ltrim(LIVE_RECENT, 0, RECENT_EVENTS_LIMIT - 1)
