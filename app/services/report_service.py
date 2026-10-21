import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel
from sqlalchemy import and_, case, func, literal, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.exceptions import ValidationException
from app.models import AnalyticsEvent, AnalyticsPageview, AnalyticsSession
from app.schemas.traffic_reports import (
    BreakdownReport,
    BreakdownRow,
    ClassRow,
    Dimension,
    DurationBucket,
    EventRow,
    EventsReport,
    EventTarget,
    MetricDefinition,
    OverviewReport,
    PageRow,
    PagesReport,
    PeriodInfo,
    QualityReport,
    ReasonRow,
    SessionRow,
    SessionsReport,
    Summary,
    TimePoint,
    TrafficFilter,
)
from app.services import cache_service
from app.utils.country_names import get_country_name
from app.utils.traffic_quality import CLEAN_CLASSES, EXCLUDED_CLASSES, LIKELY_HUMAN

S = AnalyticsSession
P = AnalyticsPageview
E = AnalyticsEvent

MAX_RANGE_DAYS = 400
DEFAULT_RANGE_DAYS = 7
HOURLY_MAX_DAYS = 2
SESSION_TIMEOUT = timedelta(minutes=30)
ENGAGED_MS = 10_000
BREAKDOWN_LIMIT = 25
HISTORICAL_TTL = 3600
LIVE_PERIOD_TTL = 60

DURATION_BUCKETS = (
    ("0-10s", 0, 10),
    ("10-30s", 10, 30),
    ("30-60s", 30, 60),
    ("1-3 min", 60, 180),
    ("3-10 min", 180, 600),
    ("10+ min", 600, None),
)

METHODOLOGY = [
    MetricDefinition(
        metric="Session",
        definition="A run of activity from one browser. A new session starts after 30 minutes without any event.",
    ),
    MetricDefinition(
        metric="Users",
        definition="Distinct pseudonymous visitor IDs stored in the visitor's browser. Not people: one person on two devices counts twice.",
    ),
    MetricDefinition(
        metric="Pageviews",
        definition="Page views recorded inside the period, including client-side navigations. Heartbeats and API calls are never counted.",
    ),
    MetricDefinition(
        metric="Engagement time",
        definition="Time a page was visible and the visitor was active (activity within the last 30 seconds). Hidden-tab and idle time is excluded.",
    ),
    MetricDefinition(
        metric="Session duration",
        definition="Last event minus first event. Only sessions with at least two events that have been inactive for 30+ minutes are measured; the rest are reported as excluded, never counted as zero.",
    ),
    MetricDefinition(
        metric="Median session duration",
        definition="The 50th percentile of the measured durations above, computed from the data, not derived from the average.",
    ),
    MetricDefinition(
        metric="Engaged session",
        definition="A session with 10+ seconds of engagement time or 2+ pageviews.",
    ),
    MetricDefinition(metric="Engagement rate", definition="Engaged sessions divided by sessions."),
    MetricDefinition(metric="Bounce rate", definition="1 minus the engagement rate."),
    MetricDefinition(
        metric="New users",
        definition="Users with no earlier session in the selected traffic filter before the period start.",
    ),
    MetricDefinition(
        metric="Traffic filter",
        definition="Clean = likely human + unknown. Likely human = interaction or sustained engagement evidence. Known bots, suspicious and internal traffic are excluded from both.",
    ),
]

QUALITY_LIMITATIONS = [
    "Only visitors that run JavaScript are observed, so most crawlers never appear here.",
    "Known bots are matched by user agent, which can be spoofed. Crawler identity is not verified by reverse DNS.",
    "Suspicious means automation signals were seen. It is an estimate, not proof.",
    "Unknown sessions passed browser checks but showed no interaction, for example a quick read and leave.",
    "Datacenter and proxy IP reputation is not used.",
]


@dataclass(frozen=True)
class Period:
    start: datetime
    end: datetime
    from_date: date
    to_date: date
    tz: str

    @property
    def days(self) -> int:
        return (self.to_date - self.from_date).days + 1

    @property
    def granularity(self) -> str:
        return "hour" if self.days <= HOURLY_MAX_DAYS else "day"

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    def previous(self) -> "Period":
        return build_period(
            self.from_date - timedelta(days=self.days),
            self.from_date - timedelta(days=1),
            self.tz,
        )


def build_period(from_date: date, to_date: date, tz_name: str) -> Period:
    try:
        zone = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValidationException("Unknown timezone", code="INVALID_TIMEZONE")
    if from_date > to_date:
        raise ValidationException("'from' must not be after 'to'", code="INVALID_RANGE")
    if (to_date - from_date).days + 1 > MAX_RANGE_DAYS:
        raise ValidationException(f"Range is limited to {MAX_RANGE_DAYS} days", code="RANGE_TOO_LARGE")
    start = datetime.combine(from_date, time.min, tzinfo=zone).astimezone(timezone.utc)
    end = datetime.combine(to_date + timedelta(days=1), time.min, tzinfo=zone).astimezone(timezone.utc)
    return Period(start=start, end=end, from_date=from_date, to_date=to_date, tz=tz_name)


def resolve_period(from_date: date | None, to_date: date | None, tz_name: str) -> Period:
    try:
        today = datetime.now(ZoneInfo(tz_name)).date()
    except (ZoneInfoNotFoundError, ValueError):
        raise ValidationException("Unknown timezone", code="INVALID_TIMEZONE")
    end_date = to_date or today
    start_date = from_date or end_date - timedelta(days=DEFAULT_RANGE_DAYS - 1)
    return build_period(start_date, end_date, tz_name)


def _traffic(session, traffic: TrafficFilter):
    if traffic == "human":
        return session.traffic_class == LIKELY_HUMAN
    if traffic == "clean":
        return session.traffic_class.in_(CLEAN_CLASSES)
    return true()


def _in_period(column, period: Period):
    return and_(column >= period.start, column < period.end)


def _rate(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0


def _cache_ttl(period: Period) -> int:
    closed = period.end <= datetime.now(timezone.utc) - timedelta(hours=1)
    return HISTORICAL_TTL if closed else LIVE_PERIOD_TTL


async def _cached(model: type[BaseModel], name: str, params: dict, period: Period, build):
    digest = hashlib.sha1(json.dumps(params, sort_keys=True, default=str).encode()).hexdigest()[:20]
    key = f"report:{name}:{digest}"
    hit = await cache_service.get_json(key)
    if hit is not None:
        return model.model_validate(hit)
    result = await build()
    await cache_service.set_json(key, result.model_dump(mode="json"), _cache_ttl(period))
    return result


def _measured(now: datetime):
    elapsed = func.extract("epoch", S.last_seen_at - S.started_at)
    eligible = and_(S.last_seen_at > S.started_at, S.last_seen_at <= now - SESSION_TIMEOUT)
    return elapsed, eligible


async def summary(db: AsyncSession, period: Period, traffic: TrafficFilter) -> Summary:
    now = datetime.now(timezone.utc)
    elapsed, eligible = _measured(now)
    scope = and_(_in_period(S.started_at, period), _traffic(S, traffic))

    row = (
        await db.execute(
            select(
                func.count(),
                func.count(func.distinct(S.visitor_id)),
                func.coalesce(func.avg(S.engaged_ms), 0),
                func.count().filter(or_(S.engaged_ms >= ENGAGED_MS, S.page_count >= 2)),
                func.count().filter(S.page_count <= 1),
                func.avg(elapsed).filter(eligible),
                func.percentile_cont(0.5).within_group(elapsed).filter(eligible),
                func.count().filter(eligible),
            ).where(scope)
        )
    ).one()
    sessions, users, avg_engaged_ms, engaged, single, avg_duration, median_duration, measured = row

    earlier = aliased(S)
    seen_before = (
        select(literal(1))
        .where(
            earlier.visitor_id == S.visitor_id,
            earlier.started_at < period.start,
            _traffic(earlier, traffic),
        )
        .exists()
    )
    new_users = await db.scalar(
        select(func.count(func.distinct(S.visitor_id))).where(scope, ~seen_before)
    ) or 0

    pageviews = await db.scalar(
        select(func.count())
        .select_from(P)
        .join(S, S.id == P.session_id)
        .where(_in_period(P.occurred_at, period), _traffic(S, traffic))
    ) or 0

    return Summary(
        sessions=sessions,
        users=users,
        new_users=new_users,
        returning_users=users - new_users,
        pageviews=pageviews,
        engaged_sessions=engaged,
        single_page_sessions=single,
        engagement_rate=_rate(engaged, sessions),
        bounce_rate=round(1 - _rate(engaged, sessions), 4) if sessions else 0.0,
        returning_rate=_rate(users - new_users, users),
        pages_per_session=round(pageviews / sessions, 2) if sessions else 0.0,
        avg_engagement_seconds=round(float(avg_engaged_ms) / 1000, 1),
        avg_session_seconds=round(float(avg_duration), 1) if avg_duration is not None else None,
        median_session_seconds=round(float(median_duration), 1) if median_duration is not None else None,
        duration_sessions=measured,
        duration_excluded_sessions=sessions - measured,
    )


def _bucket(column, period: Period):
    return func.date_trunc(period.granularity, func.timezone(period.tz, column))


async def _series(db: AsyncSession, period: Period, traffic: TrafficFilter) -> list[TimePoint]:
    session_bucket = _bucket(S.started_at, period)
    session_rows = (
        await db.execute(
            select(
                session_bucket.label("bucket"),
                func.count(func.distinct(S.visitor_id)),
                func.count(),
            )
            .where(_in_period(S.started_at, period), _traffic(S, traffic))
            .group_by("bucket")
        )
    ).all()
    view_bucket = _bucket(P.occurred_at, period)
    view_rows = (
        await db.execute(
            select(view_bucket.label("bucket"), func.count())
            .select_from(P)
            .join(S, S.id == P.session_id)
            .where(_in_period(P.occurred_at, period), _traffic(S, traffic))
            .group_by("bucket")
        )
    ).all()

    users_by = {row[0]: (row[1], row[2]) for row in session_rows}
    views_by = {row[0]: row[1] for row in view_rows}

    step = timedelta(hours=1) if period.granularity == "hour" else timedelta(days=1)
    cursor = datetime.combine(period.from_date, time.min)
    last = datetime.combine(period.to_date + timedelta(days=1), time.min)
    points: list[TimePoint] = []
    while cursor < last:
        users, sessions = users_by.get(cursor, (0, 0))
        points.append(TimePoint(bucket=cursor, users=users, sessions=sessions, pageviews=views_by.get(cursor, 0)))
        cursor += step
    return points


async def _duration_distribution(
    db: AsyncSession, period: Period, traffic: TrafficFilter
) -> list[DurationBucket]:
    elapsed, eligible = _measured(datetime.now(timezone.utc))
    label = case(
        *[
            (and_(elapsed >= low, elapsed < high) if high is not None else elapsed >= low, name)
            for name, low, high in DURATION_BUCKETS
        ],
        else_="0-10s",
    ).label("bucket")
    rows = (
        await db.execute(
            select(label, func.count())
            .where(_in_period(S.started_at, period), _traffic(S, traffic), eligible)
            .group_by("bucket")
        )
    ).all()
    counts = dict(rows)
    return [DurationBucket(label=name, sessions=counts.get(name, 0)) for name, _, _ in DURATION_BUCKETS]


async def overview(
    db: AsyncSession, period: Period, traffic: TrafficFilter, compare: bool
) -> OverviewReport:
    async def build() -> OverviewReport:
        current = await summary(db, period, traffic)
        previous = await summary(db, period.previous(), traffic) if compare else None
        return OverviewReport(
            period=PeriodInfo(
                from_date=period.from_date,
                to_date=period.to_date,
                days=period.days,
                tz=period.tz,
                granularity=period.granularity,
            ),
            traffic=traffic,
            summary=current,
            previous=previous,
            series=await _series(db, period, traffic),
            duration_distribution=await _duration_distribution(db, period, traffic),
            methodology=METHODOLOGY,
        )

    params = {"p": [period.from_date, period.to_date, period.tz], "t": traffic, "c": compare}
    return await _cached(OverviewReport, "overview", params, period, build)


DIMENSIONS = {
    "source": (S.source, S.medium),
    "referrer": (S.referrer_host, None),
    "device": (S.device, None),
    "browser": (S.browser, None),
    "os": (S.os, None),
    "country": (S.country, None),
    "landing": (S.landing_path, None),
}


async def breakdown(
    db: AsyncSession, period: Period, traffic: TrafficFilter, dimension: Dimension, limit: int
) -> BreakdownReport:
    async def build() -> BreakdownReport:
        primary, secondary = DIMENSIONS[dimension]
        columns = [primary] + ([secondary] if secondary is not None else [])
        engaged_expr = or_(S.engaged_ms >= ENGAGED_MS, S.page_count >= 2)
        query = (
            select(
                *columns,
                func.count().label("sessions"),
                func.count(func.distinct(S.visitor_id)),
                func.count().filter(engaged_expr),
            )
            .where(_in_period(S.started_at, period), _traffic(S, traffic), primary.is_not(None))
            .group_by(*columns)
            .order_by(func.count().desc())
            .limit(limit)
        )
        rows = []
        for row in (await db.execute(query)).all():
            label = row[0]
            sessions, users, engaged = row[-3], row[-2], row[-1]
            if dimension == "country":
                label = get_country_name(label) or label
            rows.append(
                BreakdownRow(
                    label=label,
                    secondary=row[1] if secondary is not None else None,
                    sessions=sessions,
                    users=users,
                    engagement_rate=_rate(engaged, sessions),
                    bounce_rate=round(1 - _rate(engaged, sessions), 4),
                )
            )
        return BreakdownReport(dimension=dimension, rows=rows)

    params = {"p": [period.from_date, period.to_date, period.tz], "t": traffic, "d": dimension, "l": limit}
    return await _cached(BreakdownReport, "breakdown", params, period, build)


async def pages(
    db: AsyncSession, period: Period, traffic: TrafficFilter, page: int, page_size: int
) -> PagesReport:
    async def build() -> PagesReport:
        scope = and_(_in_period(P.occurred_at, period), _traffic(S, traffic))
        total = await db.scalar(
            select(func.count(func.distinct(P.path))).select_from(P).join(S, S.id == P.session_id).where(scope)
        ) or 0
        rows = (
            await db.execute(
                select(
                    P.path,
                    func.count(),
                    func.count(func.distinct(S.visitor_id)),
                    func.coalesce(func.avg(P.engaged_ms), 0),
                    func.coalesce(func.avg(P.max_scroll), 0),
                )
                .join(S, S.id == P.session_id)
                .where(scope)
                .group_by(P.path)
                .order_by(func.count().desc(), P.path)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        paths = [row[0] for row in rows]
        exits = {}
        if paths:
            exit_rows = (
                await db.execute(
                    select(S.exit_path, func.count())
                    .where(_in_period(S.started_at, period), _traffic(S, traffic), S.exit_path.in_(paths))
                    .group_by(S.exit_path)
                )
            ).all()
            exits = dict(exit_rows)
        return PagesReport(
            rows=[
                PageRow(
                    path=path,
                    views=views,
                    users=users,
                    avg_engaged_seconds=round(float(avg_engaged) / 1000, 1),
                    avg_scroll=round(float(avg_scroll), 1),
                    exits=exits.get(path, 0),
                    exit_rate=min(_rate(exits.get(path, 0), views), 1.0),
                )
                for path, views, users, avg_engaged, avg_scroll in rows
            ],
            total=total,
            page=page,
            page_size=page_size,
        )

    params = {"p": [period.from_date, period.to_date, period.tz], "t": traffic, "pg": page, "ps": page_size}
    return await _cached(PagesReport, "pages", params, period, build)


async def quality(db: AsyncSession, period: Period) -> QualityReport:
    async def build() -> QualityReport:
        scope = _in_period(S.started_at, period)
        class_rows = (
            await db.execute(
                select(S.traffic_class, func.count(), func.coalesce(func.sum(S.page_count), 0))
                .where(scope)
                .group_by(S.traffic_class)
            )
        ).all()
        total = sum(row[1] for row in class_rows)
        counts = {row[0]: row[1] for row in class_rows}
        reason_rows = (
            await db.execute(
                select(S.traffic_class, S.class_reason, S.class_confidence, func.count())
                .where(scope, S.class_reason.is_not(None))
                .group_by(S.traffic_class, S.class_reason, S.class_confidence)
                .order_by(func.count().desc())
                .limit(20)
            )
        ).all()
        return QualityReport(
            observed_sessions=total,
            estimated_human_sessions=counts.get(LIKELY_HUMAN, 0),
            excluded_sessions=sum(counts.get(name, 0) for name in EXCLUDED_CLASSES),
            classes=[
                ClassRow(traffic_class=name, sessions=count, pageviews=int(views), share=_rate(count, total))
                for name, count, views in sorted(class_rows, key=lambda item: -item[1])
            ],
            reasons=[
                ReasonRow(traffic_class=cls, reason=reason, confidence=confidence or "low", sessions=count)
                for cls, reason, confidence, count in reason_rows
            ],
            limitations=QUALITY_LIMITATIONS,
        )

    params = {"p": [period.from_date, period.to_date, period.tz]}
    return await _cached(QualityReport, "quality", params, period, build)


def public_session_id(session_id) -> str:
    return hashlib.sha256(str(session_id).encode()).hexdigest()[:8]


async def session_rows(
    db: AsyncSession, period: Period, traffic: TrafficFilter, page: int, page_size: int
) -> SessionsReport:
    scope = and_(_in_period(S.started_at, period), _traffic(S, traffic))
    total = await db.scalar(select(func.count()).select_from(S).where(scope)) or 0
    sessions = (
        await db.scalars(
            select(S).where(scope).order_by(S.started_at.desc()).offset((page - 1) * page_size).limit(page_size)
        )
    ).all()
    closed_before = datetime.now(timezone.utc) - SESSION_TIMEOUT
    rows = []
    for item in sessions:
        measurable = item.last_seen_at > item.started_at and item.last_seen_at <= closed_before
        rows.append(
            SessionRow(
                id=public_session_id(item.id),
                started_at=item.started_at,
                duration_seconds=(item.last_seen_at - item.started_at).total_seconds() if measurable else None,
                engaged_seconds=round(item.engaged_ms / 1000, 1),
                pages=item.page_count,
                landing_path=item.landing_path,
                exit_path=item.exit_path,
                source=item.source,
                medium=item.medium,
                device=item.device,
                country=item.country,
                traffic_class=item.traffic_class,
                confidence=item.class_confidence,
                reason=item.class_reason,
            )
        )
    return SessionsReport(rows=rows, total=total, page=page, page_size=page_size)


async def events(db: AsyncSession, period: Period, traffic: TrafficFilter) -> EventsReport:
    scope = and_(_in_period(E.occurred_at, period), _traffic(S, traffic))
    summary_rows = (
        await db.execute(
            select(E.name, func.count(), func.count(func.distinct(E.session_id)))
            .join(S, S.id == E.session_id)
            .where(scope)
            .group_by(E.name)
            .order_by(func.count().desc())
        )
    ).all()
    target_rows = (
        await db.execute(
            select(E.name, E.target, func.count())
            .join(S, S.id == E.session_id)
            .where(scope, E.target.is_not(None))
            .group_by(E.name, E.target)
            .order_by(func.count().desc())
            .limit(15)
        )
    ).all()
    return EventsReport(
        rows=[EventRow(name=name, count=count, sessions=sessions) for name, count, sessions in summary_rows],
        targets=[EventTarget(name=name, target=target, count=count) for name, target, count in target_rows],
    )
