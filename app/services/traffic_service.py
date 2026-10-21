import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from time import time

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis_client import get_redis
from app.models import AnalyticsPageview, AnalyticsSession
from app.schemas.analytics import (
    LabelCount,
    MinuteViews,
    PageStat,
    RealtimeAnalytics,
    TrafficDay,
    TrafficOverview,
)
from app.services.tracking_service import (
    LIVE_VIEWS,
    LIVE_VIEWS_WINDOW_SECONDS,
    LIVE_VISITOR_PREFIX,
    LIVE_VISITORS,
    LIVE_WINDOW_SECONDS,
)

ENGAGED_SESSION_MS = 10_000
TOP_LIMIT = 10

S = AnalyticsSession
P = AnalyticsPageview


def _top(counter: Counter, limit: int = 5) -> list[LabelCount]:
    return [LabelCount(label=label, count=count) for label, count in counter.most_common(limit)]


async def realtime() -> RealtimeAnalytics:
    r = get_redis()
    now = time()
    visitor_ids = await r.zrangebyscore(LIVE_VISITORS, now - LIVE_WINDOW_SECONDS, "+inf")
    details = (
        await r.mget([f"{LIVE_VISITOR_PREFIX}{vid}" for vid in visitor_ids]) if visitor_ids else []
    )

    pages: Counter = Counter()
    sources: Counter = Counter()
    countries: Counter = Counter()
    devices: Counter = Counter()
    for raw in details:
        if raw is None:
            continue
        info = json.loads(raw)
        pages[info["path"]] += 1
        sources[info["source"]] += 1
        countries[info["country"] or "Unknown"] += 1
        devices[info["device"]] += 1

    view_scores = await r.zrangebyscore(
        LIVE_VIEWS, now - LIVE_VIEWS_WINDOW_SECONDS, "+inf", withscores=True
    )
    current_minute = int(now // 60)
    buckets = Counter(current_minute - int(score // 60) for _, score in view_scores)
    minutes = LIVE_VIEWS_WINDOW_SECONDS // 60
    series = [
        MinuteViews(
            minute=datetime.fromtimestamp((current_minute - ago) * 60, tz=timezone.utc),
            views=buckets.get(ago, 0),
        )
        for ago in range(minutes - 1, -1, -1)
    ]

    return RealtimeAnalytics(
        active_users=len(visitor_ids),
        views_per_minute=series,
        top_pages=_top(pages),
        top_sources=_top(sources),
        countries=_top(countries),
        devices=_top(devices),
    )


def _session_filter(query: Select, since: datetime, humans_only: bool) -> Select:
    query = query.where(S.started_at >= since)
    return query.where(S.is_bot.is_(False)) if humans_only else query


async def _label_counts(
    db: AsyncSession, column, since: datetime, humans_only: bool, null_label: str | None = None
) -> list[LabelCount]:
    label = func.coalesce(column, null_label) if null_label else column
    query = _session_filter(
        select(label, func.count()).group_by(label).order_by(func.count().desc()).limit(TOP_LIMIT),
        since,
        humans_only,
    )
    if not null_label:
        query = query.where(column.is_not(None))
    return [LabelCount(label=row[0], count=row[1]) for row in (await db.execute(query)).all()]


async def overview(db: AsyncSession, days: int, humans_only: bool) -> TrafficOverview:
    since = datetime.now(timezone.utc) - timedelta(days=days)

    totals = (
        await db.execute(
            _session_filter(
                select(
                    func.count(func.distinct(S.visitor_id)),
                    func.count(),
                    func.coalesce(func.sum(S.page_count), 0),
                    func.coalesce(func.avg(S.engaged_ms), 0),
                    func.count().filter(or_(S.engaged_ms >= ENGAGED_SESSION_MS, S.page_count >= 2)),
                ),
                since,
                humans_only,
            )
        )
    ).one()
    users, session_count, pageview_count, avg_engaged_ms, engaged_sessions = totals

    day = func.date(func.timezone("UTC", S.started_at))
    daily_rows = (
        await db.execute(
            _session_filter(
                select(
                    day.label("day"),
                    func.count(func.distinct(S.visitor_id)),
                    func.count(),
                    func.coalesce(func.sum(S.page_count), 0),
                ).group_by("day"),
                since,
                humans_only,
            )
        )
    ).all()
    by_day = {row[0]: row for row in daily_rows}
    today = datetime.now(timezone.utc).date()
    daily = []
    for offset in range(days - 1, -1, -1):
        date_ = today - timedelta(days=offset)
        row = by_day.get(date_)
        daily.append(
            TrafficDay(
                date=date_,
                users=row[1] if row else 0,
                sessions=row[2] if row else 0,
                pageviews=row[3] if row else 0,
            )
        )

    page_query = (
        select(P.path, func.count(), func.coalesce(func.avg(P.engaged_ms), 0))
        .join(S, S.id == P.session_id)
        .where(P.occurred_at >= since)
        .group_by(P.path)
        .order_by(func.count().desc())
        .limit(TOP_LIMIT)
    )
    if humans_only:
        page_query = page_query.where(S.is_bot.is_(False))
    top_pages = [
        PageStat(path=row[0], views=row[1], avg_engaged_seconds=round(float(row[2]) / 1000, 1))
        for row in (await db.execute(page_query)).all()
    ]

    return TrafficOverview(
        days=days,
        humans_only=humans_only,
        users=users,
        sessions=session_count,
        pageviews=pageview_count,
        avg_session_seconds=round(float(avg_engaged_ms) / 1000, 1),
        pages_per_session=round(pageview_count / session_count, 2) if session_count else 0,
        engagement_rate=round(engaged_sessions / session_count, 4) if session_count else 0,
        daily=daily,
        top_pages=top_pages,
        sources=await _label_counts(db, S.source, since, humans_only),
        devices=await _label_counts(db, S.device, since, humans_only),
        countries=await _label_counts(db, S.country, since, humans_only, null_label="Unknown"),
    )
