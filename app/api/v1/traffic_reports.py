from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.deps.auth_deps import require_permission
from app.models import User
from app.schemas.traffic_reports import (
    BreakdownReport,
    Dimension,
    EventsReport,
    OverviewReport,
    PagesReport,
    QualityReport,
    RealtimeAnalytics,
    SessionsReport,
    TrafficFilter,
)
from app.services import realtime_service, report_service
from app.services.report_service import Period

router = APIRouter(prefix="/analytics", tags=["analytics"])


def report_period(
    from_date: date | None = Query(None, alias="from"),
    to_date: date | None = Query(None, alias="to"),
    tz: str = Query("UTC", max_length=64),
) -> Period:
    return report_service.resolve_period(from_date, to_date, tz)


@router.get("/realtime", response_model=RealtimeAnalytics)
async def realtime(user: User = Depends(require_permission("analytics:view"))) -> RealtimeAnalytics:
    return await realtime_service.realtime()


@router.get("/reports/overview", response_model=OverviewReport)
async def overview(
    period: Period = Depends(report_period),
    traffic: TrafficFilter = "clean",
    compare: bool = False,
    user: User = Depends(require_permission("analytics:view")),
    db: AsyncSession = Depends(get_db),
) -> OverviewReport:
    return await report_service.overview(db, period, traffic, compare)


@router.get("/reports/breakdown", response_model=BreakdownReport)
async def breakdown(
    dimension: Dimension,
    period: Period = Depends(report_period),
    traffic: TrafficFilter = "clean",
    limit: int = Query(10, ge=1, le=report_service.BREAKDOWN_LIMIT),
    user: User = Depends(require_permission("analytics:view")),
    db: AsyncSession = Depends(get_db),
) -> BreakdownReport:
    return await report_service.breakdown(db, period, traffic, dimension, limit)


@router.get("/reports/pages", response_model=PagesReport)
async def pages(
    period: Period = Depends(report_period),
    traffic: TrafficFilter = "clean",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    user: User = Depends(require_permission("analytics:view")),
    db: AsyncSession = Depends(get_db),
) -> PagesReport:
    return await report_service.pages(db, period, traffic, page, page_size)


@router.get("/reports/quality", response_model=QualityReport)
async def quality(
    period: Period = Depends(report_period),
    user: User = Depends(require_permission("analytics:view")),
    db: AsyncSession = Depends(get_db),
) -> QualityReport:
    return await report_service.quality(db, period)


@router.get("/reports/sessions", response_model=SessionsReport)
async def sessions(
    period: Period = Depends(report_period),
    traffic: TrafficFilter = "clean",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    user: User = Depends(require_permission("analytics:view")),
    db: AsyncSession = Depends(get_db),
) -> SessionsReport:
    return await report_service.session_rows(db, period, traffic, page, page_size)


@router.get("/reports/events", response_model=EventsReport)
async def events(
    period: Period = Depends(report_period),
    traffic: TrafficFilter = "clean",
    user: User = Depends(require_permission("analytics:view")),
    db: AsyncSession = Depends(get_db),
) -> EventsReport:
    return await report_service.events(db, period, traffic)
