from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.analytics import LabelCount, MinuteViews

TrafficFilter = Literal["clean", "human", "all"]
Dimension = Literal["source", "referrer", "device", "browser", "os", "country", "landing"]


class RecentVisit(BaseModel):
    at: datetime
    visitor: str
    path: str
    country: str | None
    device: str
    browser: str
    os: str
    source: str
    returning: bool


class RealtimeAnalytics(BaseModel):
    active_5m: int
    active_30m: int
    pageviews_5m: int
    pageviews_30m: int
    new_visitors: int
    returning_visitors: int
    views_per_minute: list[MinuteViews]
    top_pages: list[LabelCount]
    top_sources: list[LabelCount]
    countries: list[LabelCount]
    devices: list[LabelCount]
    browsers: list[LabelCount]
    systems: list[LabelCount]
    recent: list[RecentVisit]
    window_seconds: int
    note: str


class PeriodInfo(BaseModel):
    from_date: date
    to_date: date
    days: int
    tz: str
    granularity: Literal["hour", "day"]


class Summary(BaseModel):
    sessions: int
    users: int
    new_users: int
    returning_users: int
    pageviews: int
    engaged_sessions: int
    single_page_sessions: int
    engagement_rate: float
    bounce_rate: float
    returning_rate: float
    pages_per_session: float
    avg_engagement_seconds: float
    avg_session_seconds: float | None
    median_session_seconds: float | None
    duration_sessions: int
    duration_excluded_sessions: int


class TimePoint(BaseModel):
    bucket: datetime
    users: int
    sessions: int
    pageviews: int


class DurationBucket(BaseModel):
    label: str
    sessions: int


class MetricDefinition(BaseModel):
    metric: str
    definition: str


class OverviewReport(BaseModel):
    period: PeriodInfo
    traffic: TrafficFilter
    summary: Summary
    previous: Summary | None
    series: list[TimePoint]
    duration_distribution: list[DurationBucket]
    methodology: list[MetricDefinition]


class BreakdownRow(BaseModel):
    label: str
    secondary: str | None = None
    sessions: int
    users: int
    engagement_rate: float
    bounce_rate: float


class BreakdownReport(BaseModel):
    dimension: Dimension
    rows: list[BreakdownRow]


class PageRow(BaseModel):
    path: str
    views: int
    users: int
    avg_engaged_seconds: float
    avg_scroll: float
    exits: int
    exit_rate: float


class PagesReport(BaseModel):
    rows: list[PageRow]
    total: int
    page: int
    page_size: int


class ClassRow(BaseModel):
    traffic_class: str
    sessions: int
    pageviews: int
    share: float


class ReasonRow(BaseModel):
    traffic_class: str
    reason: str
    confidence: str
    sessions: int


class QualityReport(BaseModel):
    observed_sessions: int
    estimated_human_sessions: int
    excluded_sessions: int
    classes: list[ClassRow]
    reasons: list[ReasonRow]
    limitations: list[str]


class SessionRow(BaseModel):
    id: str
    started_at: datetime
    duration_seconds: float | None
    engaged_seconds: float
    pages: int
    landing_path: str
    exit_path: str | None
    source: str
    medium: str
    device: str
    country: str | None
    traffic_class: str
    confidence: str | None
    reason: str | None


class SessionsReport(BaseModel):
    rows: list[SessionRow]
    total: int
    page: int
    page_size: int


class EventRow(BaseModel):
    name: str
    count: int
    sessions: int


class EventTarget(BaseModel):
    name: str
    target: str
    count: int


class EventsReport(BaseModel):
    rows: list[EventRow]
    targets: list[EventTarget]
