import json
from collections import Counter
from datetime import datetime, timezone
from time import time

from app.core.redis_client import get_redis
from app.schemas.analytics import LabelCount, MinuteViews
from app.schemas.traffic_reports import RealtimeAnalytics, RecentVisit
from app.services.tracking_service import (
    ACTIVE_WINDOW_SECONDS,
    LIVE_RECENT,
    LIVE_RETENTION_SECONDS,
    LIVE_VIEWS,
    LIVE_VISITOR_PREFIX,
    LIVE_VISITORS,
)

RECENT_SHOWN = 20
NOTE = (
    "Estimate from first-party browser events. A visitor is active while they have sent an event "
    "in the last 5 minutes. Bots and internal traffic are excluded; visitors with no interaction yet "
    "may still include a few unrecognised automated browsers."
)


def _top(counter: Counter, limit: int = 5) -> list[LabelCount]:
    return [LabelCount(label=label, count=count) for label, count in counter.most_common(limit)]


async def realtime() -> RealtimeAnalytics:
    r = get_redis()
    now = time()
    active_cutoff = now - ACTIVE_WINDOW_SECONDS
    window_start = now - LIVE_RETENTION_SECONDS

    visitor_ids = await r.zrangebyscore(LIVE_VISITORS, active_cutoff, "+inf")
    active_30m = await r.zcount(LIVE_VISITORS, window_start, "+inf")
    details = await r.mget([f"{LIVE_VISITOR_PREFIX}{vid}" for vid in visitor_ids]) if visitor_ids else []

    pages: Counter = Counter()
    sources: Counter = Counter()
    countries: Counter = Counter()
    devices: Counter = Counter()
    browsers: Counter = Counter()
    systems: Counter = Counter()
    returning = 0
    for raw in details:
        if raw is None:
            continue
        info = json.loads(raw)
        pages[info.get("path", "/")] += 1
        sources[info.get("source", "(direct)")] += 1
        countries[info.get("country") or "Unknown"] += 1
        devices[info.get("device", "unknown")] += 1
        browsers[info.get("browser", "Unknown")] += 1
        systems[info.get("os", "Unknown")] += 1
        returning += 1 if info.get("returning") else 0

    view_scores = await r.zrangebyscore(LIVE_VIEWS, window_start, "+inf", withscores=True)
    current_minute = int(now // 60)
    buckets = Counter(current_minute - int(score // 60) for _, score in view_scores)
    minutes = LIVE_RETENTION_SECONDS // 60
    series = [
        MinuteViews(
            minute=datetime.fromtimestamp((current_minute - ago) * 60, tz=timezone.utc),
            views=buckets.get(ago, 0),
        )
        for ago in range(minutes - 1, -1, -1)
    ]
    pageviews_5m = sum(1 for _, score in view_scores if score >= active_cutoff)

    recent_raw = await r.lrange(LIVE_RECENT, 0, RECENT_SHOWN - 1)
    recent = []
    for item in recent_raw:
        try:
            recent.append(RecentVisit(**json.loads(item)))
        except (ValueError, TypeError):
            continue

    active = len(visitor_ids)
    return RealtimeAnalytics(
        active_5m=active,
        active_30m=active_30m,
        pageviews_5m=pageviews_5m,
        pageviews_30m=len(view_scores),
        new_visitors=active - returning,
        returning_visitors=returning,
        views_per_minute=series,
        top_pages=_top(pages),
        top_sources=_top(sources),
        countries=_top(countries),
        devices=_top(devices),
        browsers=_top(browsers),
        systems=_top(systems),
        recent=recent,
        window_seconds=ACTIVE_WINDOW_SECONDS,
        note=NOTE,
    )
