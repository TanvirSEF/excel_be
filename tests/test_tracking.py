from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import delete, func, select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.redis_client import get_redis
from app.jobs.analytics_flusher import flush_analytics
from app.models import AnalyticsPageview, AnalyticsSession
from app.services import report_service, tracking_service
from app.utils.traffic_quality import INTERNAL, KNOWN_BOT, LIKELY_HUMAN, SUSPICIOUS, UNKNOWN

CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
SEED_PREFIX = "/seed-analytics"
pytestmark = pytest.mark.usefixtures("isolated_analytics_redis")


@pytest.fixture
async def created_sessions() -> AsyncIterator[list[UUID]]:
    ids: list[UUID] = []
    yield ids
    if ids:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(AnalyticsSession).where(AnalyticsSession.id.in_(ids)))
            await db.commit()
        r = get_redis()
        await r.delete(*[f"{tracking_service.EVENT_DEDUPE_PREFIX}{i}" for i in ids])


def track_event(session_id, visitor_id, page_id, **overrides) -> dict:
    body = {
        "event_id": str(uuid4()),
        "type": "pageview",
        "session_id": str(session_id),
        "visitor_id": str(visitor_id),
        "page_id": str(page_id),
        "path": f"/track-test-{uuid4().hex[:6]}/",
        "referrer": "https://www.google.com/",
        "screen_width": 1440,
        "screen_height": 900,
    }
    body.update(overrides)
    return body


def headers(user_agent: str = CHROME, ip: str = "203.0.113.7") -> dict:
    return {"user-agent": user_agent, "cf-connecting-ip": ip, "cf-ipcountry": "BD"}


async def send(client: httpx.AsyncClient, events: list[dict], **header_overrides) -> httpx.Response:
    return await client.post("/api/v1/track", json={"events": events}, headers=headers(**header_overrides))


async def stored_session(session_id) -> AnalyticsSession | None:
    async with AsyncSessionLocal() as db:
        return await db.get(AnalyticsSession, session_id)


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_pageview_and_pings_build_one_session(
    client: httpx.AsyncClient, admin_token: str, created_sessions: list[UUID]
):
    session_id, visitor_id, page_id = uuid4(), uuid4(), uuid4()
    created_sessions.append(session_id)
    path = f"/track-test-{uuid4().hex[:6]}/"

    first = track_event(session_id, visitor_id, page_id, path=path)
    pings = [
        track_event(session_id, visitor_id, page_id, path=path, type="ping", engaged_ms=ms, scroll=60, interacted=True)
        for ms in (15_000, 45_000)
    ]
    assert (await send(client, [first, *pings])).status_code == 204
    assert (await send(client, [first])).status_code == 204

    await flush_analytics()

    stored = await stored_session(session_id)
    assert stored is not None
    assert stored.page_count == 1
    assert stored.engaged_ms == 45_000
    assert stored.exit_path == path and stored.landing_path == path
    assert (stored.source, stored.medium) == ("google", "organic")
    assert (stored.device, stored.browser, stored.country) == ("desktop", "Chrome", "BD")
    assert stored.traffic_class == LIKELY_HUMAN and stored.class_confidence == "high"
    assert stored.interacted is True and stored.is_bot is False

    async with AsyncSessionLocal() as db:
        page = await db.get(AnalyticsPageview, page_id)
        pageviews = await db.scalar(
            select(func.count()).select_from(AnalyticsPageview).where(AnalyticsPageview.session_id == session_id)
        )
    assert page is not None and page.max_scroll == 60 and page.engaged_ms == 45_000
    assert pageviews == 1

    realtime = await client.get("/api/v1/analytics/realtime", headers=auth(admin_token))
    assert realtime.status_code == 200
    assert realtime.json()["active_5m"] >= 1
    assert await get_redis().zscore(tracking_service.LIVE_VISITORS, str(visitor_id)) is not None


async def test_second_pageview_in_same_session_updates_exit_page(
    client: httpx.AsyncClient, created_sessions: list[UUID]
):
    session_id, visitor_id = uuid4(), uuid4()
    created_sessions.append(session_id)
    landing, second = f"/track-test-{uuid4().hex[:6]}/", f"/track-test-{uuid4().hex[:6]}/"

    await send(client, [track_event(session_id, visitor_id, uuid4(), path=landing)])
    await flush_analytics()
    await send(client, [track_event(session_id, visitor_id, uuid4(), path=second, referrer="")])
    await flush_analytics()

    stored = await stored_session(session_id)
    assert stored.page_count == 2
    assert stored.landing_path == landing and stored.exit_path == second
    assert stored.traffic_class == LIKELY_HUMAN


async def test_live_visitors_expire_after_five_minutes(
    client: httpx.AsyncClient, admin_token: str, created_sessions: list[UUID]
):
    session_id, visitor_id = uuid4(), uuid4()
    created_sessions.append(session_id)
    await send(client, [track_event(session_id, visitor_id, uuid4())])

    before = (await client.get("/api/v1/analytics/realtime", headers=auth(admin_token))).json()
    stale = datetime.now(timezone.utc).timestamp() - 600
    await get_redis().zadd(tracking_service.LIVE_VISITORS, {str(visitor_id): stale})
    after = (await client.get("/api/v1/analytics/realtime", headers=auth(admin_token))).json()

    assert after["active_5m"] == before["active_5m"] - 1
    assert after["active_30m"] == before["active_30m"]


async def test_known_bot_is_stored_but_excluded_from_live_and_clean_reports(
    client: httpx.AsyncClient, created_sessions: list[UUID]
):
    session_id, visitor_id = uuid4(), uuid4()
    created_sessions.append(session_id)
    await send(client, [track_event(session_id, visitor_id, uuid4())], user_agent="Mozilla/5.0 (compatible; Googlebot/2.1)")
    await flush_analytics()

    stored = await stored_session(session_id)
    assert stored.traffic_class == KNOWN_BOT and stored.ua_bot is True and stored.is_bot is True
    assert await get_redis().zscore(tracking_service.LIVE_VISITORS, str(visitor_id)) is None


async def test_headless_browser_is_suspicious_not_certain(client: httpx.AsyncClient, created_sessions: list[UUID]):
    session_id = uuid4()
    created_sessions.append(session_id)
    await send(client, [track_event(session_id, uuid4(), uuid4(), webdriver=True)])
    await flush_analytics()

    stored = await stored_session(session_id)
    assert stored.traffic_class == SUSPICIOUS
    assert stored.class_confidence == "medium"
    assert stored.ua_bot is False


async def test_internal_traffic_by_ip_and_by_client_flag(
    client: httpx.AsyncClient, created_sessions: list[UUID], monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "analytics_internal_ips", "198.51.100.0/24")
    by_ip, by_flag = uuid4(), uuid4()
    created_sessions.extend([by_ip, by_flag])

    await send(client, [track_event(by_ip, uuid4(), uuid4())], ip="198.51.100.20")
    await send(client, [track_event(by_flag, uuid4(), uuid4(), internal=True)], ip="203.0.113.50")
    await flush_analytics()

    assert (await stored_session(by_ip)).traffic_class == INTERNAL
    assert (await stored_session(by_flag)).traffic_class == INTERNAL


async def test_custom_events_are_stored_with_host_only(client: httpx.AsyncClient, created_sessions: list[UUID]):
    from app.models import AnalyticsEvent

    session_id, visitor_id, page_id = uuid4(), uuid4(), uuid4()
    created_sessions.append(session_id)
    click_id = uuid4()
    await send(client, [track_event(session_id, visitor_id, page_id)])
    await send(
        client,
        [
            track_event(
                session_id, visitor_id, page_id, type="event", name="outbound_click",
                target="https://example.org/private/path?token=secret",
            )
            | {"event_id": str(click_id)}
        ],
    )
    await flush_analytics()

    async with AsyncSessionLocal() as db:
        click = await db.get(AnalyticsEvent, click_id)
    assert click is not None and click.name == "outbound_click"
    assert click.target == "example.org"


async def test_ingest_validation_and_safety(client: httpx.AsyncClient):
    session_id, visitor_id, page_id = uuid4(), uuid4(), uuid4()
    queue_before = await get_redis().llen(tracking_service.ANALYTICS_QUEUE)

    assert (await client.post("/api/v1/track", json={"events": []}, headers=headers())).status_code == 422
    too_many = [track_event(session_id, visitor_id, page_id) for _ in range(21)]
    assert (await send(client, too_many)).status_code == 422
    bad_type = track_event(session_id, visitor_id, page_id, type="purchase")
    assert (await send(client, [bad_type])).status_code == 422
    unnamed = track_event(session_id, visitor_id, page_id, type="event")
    assert (await send(client, [unnamed])).status_code == 204
    dashboard = track_event(session_id, visitor_id, page_id, path="/dashboard/posts")
    assert (await send(client, [dashboard])).status_code == 204

    assert await get_redis().llen(tracking_service.ANALYTICS_QUEUE) == queue_before
    foreign = await client.post(
        "/api/v1/track",
        json={"events": [track_event(session_id, visitor_id, page_id)]},
        headers={**headers(), "origin": "https://evil.example"},
    )
    assert foreign.status_code == 403


@pytest.fixture
async def seeded_period(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    async def no_cache(key: str):
        return None

    monkeypatch.setattr(report_service.cache_service, "get_json", no_cache)

    def at(day: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
        return datetime(2020, 1, day, hour, minute, second, tzinfo=timezone.utc)

    returning_visitor, first_visitor = uuid4(), uuid4()
    rows = [
        dict(v=first_visitor, start=at(6, 10), end=at(6, 10, 10), engaged=120_000, pages=3, cls=LIKELY_HUMAN, ua_bot=False),
        dict(v=uuid4(), start=at(6, 11), end=at(6, 11), engaged=2_000, pages=1, cls=UNKNOWN, ua_bot=False),
        dict(v=uuid4(), start=at(7, 9), end=at(7, 9, 1), engaged=30_000, pages=2, cls=LIKELY_HUMAN, ua_bot=False),
        dict(v=first_visitor, start=at(7, 12), end=at(7, 12, 0, 20), engaged=15_000, pages=1, cls=LIKELY_HUMAN, ua_bot=False),
        dict(v=returning_visitor, start=at(8, 8), end=at(8, 8, 5), engaged=12_000, pages=1, cls=UNKNOWN, ua_bot=False),
        dict(v=returning_visitor, start=at(4, 12), end=at(4, 12, 1), engaged=20_000, pages=1, cls=LIKELY_HUMAN, ua_bot=False),
        dict(v=uuid4(), start=at(7, 15), end=at(7, 15, 30), engaged=0, pages=1, cls=KNOWN_BOT, ua_bot=True),
    ]
    session_ids = []
    async with AsyncSessionLocal() as db:
        for index, row in enumerate(rows):
            session_id = uuid4()
            session_ids.append(session_id)
            path = f"{SEED_PREFIX}/page-{index % 2}/"
            db.add(
                AnalyticsSession(
                    id=session_id, visitor_id=row["v"], started_at=row["start"], last_seen_at=row["end"],
                    engaged_ms=row["engaged"], page_count=row["pages"], landing_path=path, exit_path=path,
                    source="google", medium="organic", device="desktop", browser="Chrome", os="Windows",
                    country="BD", bot_score=100 if row["ua_bot"] else 0, ua_bot=row["ua_bot"],
                    is_bot=row["ua_bot"], traffic_class=row["cls"],
                )
            )
        await db.flush()
        for session_id, row in zip(session_ids, rows):
            for offset in range(row["pages"]):
                db.add(
                    AnalyticsPageview(
                        id=uuid4(), session_id=session_id, path=f"{SEED_PREFIX}/page-{offset % 2}/",
                        occurred_at=row["start"] + timedelta(seconds=offset), engaged_ms=1000, max_scroll=50,
                    )
                )
        await db.commit()
    yield
    async with AsyncSessionLocal() as db:
        await db.execute(delete(AnalyticsSession).where(AnalyticsSession.id.in_(session_ids)))
        await db.commit()


REPORT_PARAMS = {"from": "2020-01-06", "to": "2020-01-08", "tz": "UTC"}


async def test_overview_metrics_are_calculated_from_documented_rules(
    client: httpx.AsyncClient, admin_token: str, seeded_period: None
):
    response = await client.get(
        "/api/v1/analytics/reports/overview",
        params={**REPORT_PARAMS, "compare": "true"},
        headers=auth(admin_token),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    summary = body["summary"]

    assert summary["sessions"] == 5
    assert summary["users"] == 4
    assert summary["new_users"] == 3 and summary["returning_users"] == 1
    assert summary["returning_rate"] == 0.25
    assert summary["pageviews"] == 8
    assert summary["single_page_sessions"] == 3
    assert summary["engaged_sessions"] == 4
    assert summary["engagement_rate"] == 0.8 and summary["bounce_rate"] == 0.2
    assert summary["pages_per_session"] == 1.6

    assert summary["duration_sessions"] == 4
    assert summary["duration_excluded_sessions"] == 1
    assert summary["avg_session_seconds"] == 245.0
    assert summary["median_session_seconds"] == 180.0
    assert summary["avg_engagement_seconds"] == round((120 + 2 + 30 + 15 + 12) / 5, 1)

    distribution = {row["label"]: row["sessions"] for row in body["duration_distribution"]}
    assert distribution == {"0-10s": 0, "10-30s": 1, "30-60s": 0, "1-3 min": 1, "3-10 min": 1, "10+ min": 1}

    assert [point["sessions"] for point in body["series"]] == [2, 2, 1]
    assert [point["pageviews"] for point in body["series"]] == [4, 3, 1]

    previous = body["previous"]
    assert previous["sessions"] == 1 and previous["users"] == 1
    assert body["methodology"]


async def test_traffic_filters_change_what_is_counted(client: httpx.AsyncClient, admin_token: str, seeded_period: None):
    async def sessions_for(traffic: str) -> int:
        response = await client.get(
            "/api/v1/analytics/reports/overview",
            params={**REPORT_PARAMS, "traffic": traffic},
            headers=auth(admin_token),
        )
        return response.json()["summary"]["sessions"]

    assert await sessions_for("clean") == 5
    assert await sessions_for("human") == 3
    assert await sessions_for("all") == 6


async def test_quality_report_separates_known_bots(client: httpx.AsyncClient, admin_token: str, seeded_period: None):
    response = await client.get("/api/v1/analytics/reports/quality", params=REPORT_PARAMS, headers=auth(admin_token))
    body = response.json()
    by_class = {row["traffic_class"]: row["sessions"] for row in body["classes"]}

    assert by_class == {LIKELY_HUMAN: 3, UNKNOWN: 2, KNOWN_BOT: 1}
    assert body["observed_sessions"] == 6
    assert body["estimated_human_sessions"] == 3
    assert body["excluded_sessions"] == 1
    assert body["limitations"]


async def test_pages_breakdown_sessions_and_events_reports(
    client: httpx.AsyncClient, admin_token: str, seeded_period: None
):
    pages = (
        await client.get(
            "/api/v1/analytics/reports/pages",
            params={**REPORT_PARAMS, "page_size": 2},
            headers=auth(admin_token),
        )
    ).json()
    assert pages["total"] >= 2 and len(pages["rows"]) == 2
    assert pages["rows"][0]["views"] >= pages["rows"][1]["views"]

    breakdown = (
        await client.get(
            "/api/v1/analytics/reports/breakdown",
            params={**REPORT_PARAMS, "dimension": "country"},
            headers=auth(admin_token),
        )
    ).json()
    assert breakdown["rows"][0]["label"] == "Bangladesh"

    listing = (
        await client.get(
            "/api/v1/analytics/reports/sessions",
            params={**REPORT_PARAMS, "page_size": 3},
            headers=auth(admin_token),
        )
    ).json()
    assert listing["total"] == 5 and len(listing["rows"]) == 3
    assert all(len(row["id"]) == 8 for row in listing["rows"])
    unmeasured = [row for row in listing["rows"] if row["duration_seconds"] is None]
    assert all(row["pages"] <= 1 for row in unmeasured)

    events = await client.get("/api/v1/analytics/reports/events", params=REPORT_PARAMS, headers=auth(admin_token))
    assert events.status_code == 200


async def test_report_validation_errors(client: httpx.AsyncClient, admin_token: str):
    reversed_range = await client.get(
        "/api/v1/analytics/reports/overview",
        params={"from": "2020-01-08", "to": "2020-01-06"},
        headers=auth(admin_token),
    )
    assert reversed_range.status_code == 400
    too_long = await client.get(
        "/api/v1/analytics/reports/overview",
        params={"from": "2018-01-01", "to": "2020-01-06"},
        headers=auth(admin_token),
    )
    assert too_long.status_code == 400
    bad_zone = await client.get(
        "/api/v1/analytics/reports/overview", params={"tz": "Nowhere/Land"}, headers=auth(admin_token)
    )
    assert bad_zone.status_code == 400
    bad_filter = await client.get(
        "/api/v1/analytics/reports/overview", params={"traffic": "bots-only"}, headers=auth(admin_token)
    )
    assert bad_filter.status_code == 422


async def test_reports_require_an_authorized_role(client: httpx.AsyncClient):
    for path in ("realtime", "reports/overview", "reports/quality", "reports/sessions", "reports/pages"):
        assert (await client.get(f"/api/v1/analytics/{path}")).status_code == 401

    login = await client.post(
        "/api/v1/auth/login", data={"username": "writer@test.com", "password": "WriterPass123!"}
    )
    writer = login.json()["access_token"]
    for path in ("realtime", "reports/overview"):
        assert (await client.get(f"/api/v1/analytics/{path}", headers=auth(writer))).status_code == 403
