from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal
from app.core.redis_client import get_redis
from app.jobs.analytics_flusher import flush_analytics
from app.models import AnalyticsPageview, AnalyticsSession
from app.services import tracking_service
from app.utils.bot_detect import BOT_THRESHOLD, bot_score
from app.utils.traffic_source import classify_source
from app.utils.ua_parse import parse_user_agent

CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


def test_bot_score_flags_headless_and_known_bots():
    assert bot_score(CHROME) == 0
    assert bot_score("Googlebot/2.1") == 100
    assert bot_score(None) == 100
    assert bot_score(CHROME, webdriver=True) >= BOT_THRESHOLD
    assert bot_score(CHROME, has_languages=False) < BOT_THRESHOLD
    assert bot_score(CHROME, has_languages=False, screen_width=0, screen_height=0) >= BOT_THRESHOLD
    assert bot_score(CHROME, burst=True) >= BOT_THRESHOLD


def test_parse_user_agent():
    assert parse_user_agent(CHROME) == ("desktop", "Chrome", "Windows")
    assert parse_user_agent(IPHONE) == ("mobile", "Safari", "iOS")


def test_classify_source():
    own = "excelinsider.com"
    assert classify_source(None, None, None, own) == ("(direct)", "(none)", None)
    assert classify_source("https://excelinsider.com/blog/", None, None, own)[0] == "(direct)"
    assert classify_source("https://www.google.com/", None, None, own)[:2] == ("google", "organic")
    assert classify_source("https://t.co/abc", None, None, own)[:2] == ("t", "social")
    assert classify_source("https://www.netflix.com/", None, None, own)[1] == "referral"
    assert classify_source("https://news.example.org/", None, None, own)[1] == "referral"
    assert classify_source(None, "Newsletter", "email", own)[:2] == ("newsletter", "email")


@pytest.fixture
async def tracking_cleanup():
    await _reset()
    yield
    await _reset()


async def _reset():
    r = get_redis()
    await r.delete(
        tracking_service.ANALYTICS_QUEUE,
        tracking_service.LIVE_VISITORS,
        tracking_service.LIVE_VIEWS,
    )
    async with AsyncSessionLocal() as db:
        await db.execute(delete(AnalyticsSession).where(AnalyticsSession.landing_path.like("/track-test%")))
        await db.commit()


def payload(session_id, visitor_id, page_id, **overrides) -> dict:
    body = {
        "type": "pageview",
        "session_id": str(session_id),
        "visitor_id": str(visitor_id),
        "page_id": str(page_id),
        "path": "/track-test/article/",
        "referrer": "https://www.google.com/",
        "screen_width": 1440,
        "screen_height": 900,
    }
    body.update(overrides)
    return body


def headers(user_agent: str = CHROME) -> dict:
    return {"user-agent": user_agent, "cf-connecting-ip": "203.0.113.7", "cf-ipcountry": "BD"}


@pytest.mark.usefixtures("tracking_cleanup")
async def test_pageview_and_pings_build_a_session(client: httpx.AsyncClient, admin_token: str):
    session_id, visitor_id, page_id = uuid4(), uuid4(), uuid4()

    response = await client.post(
        "/api/v1/track", json=payload(session_id, visitor_id, page_id), headers=headers()
    )
    assert response.status_code == 204
    for engaged in (15_000, 45_000):
        response = await client.post(
            "/api/v1/track",
            json=payload(
                session_id, visitor_id, page_id, type="ping", engaged_ms=engaged, scroll=60, interacted=True
            ),
            headers=headers(),
        )
        assert response.status_code == 204

    realtime = await client.get(
        "/api/v1/analytics/realtime", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert realtime.status_code == 200
    assert realtime.json()["active_users"] == 1
    assert realtime.json()["top_pages"][0] == {"label": "/track-test/article/", "count": 1}

    await flush_analytics()

    async with AsyncSessionLocal() as db:
        stored = await db.get(AnalyticsSession, session_id)
        page = await db.get(AnalyticsPageview, page_id)
    assert stored is not None and page is not None
    assert stored.page_count == 1
    assert stored.engaged_ms == 45_000
    assert stored.source == "google" and stored.medium == "organic"
    assert (stored.device, stored.browser, stored.os, stored.country) == ("desktop", "Chrome", "Windows", "BD")
    assert stored.interacted is True and stored.is_bot is False
    assert page.max_scroll == 60 and page.engaged_ms == 45_000

    traffic = await client.get(
        "/api/v1/analytics/traffic-overview?days=7", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert traffic.status_code == 200
    body = traffic.json()
    assert body["sessions"] >= 1 and body["pageviews"] >= 1
    assert any(row["path"] == "/track-test/article/" for row in body["top_pages"])


@pytest.mark.usefixtures("tracking_cleanup")
async def test_bot_traffic_is_stored_but_excluded_from_live_and_human_stats(
    client: httpx.AsyncClient, admin_token: str
):
    session_id = uuid4()
    response = await client.post(
        "/api/v1/track",
        json=payload(session_id, uuid4(), uuid4(), webdriver=True),
        headers=headers(),
    )
    assert response.status_code == 204

    realtime = await client.get(
        "/api/v1/analytics/realtime", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert realtime.json()["active_users"] == 0

    await flush_analytics()
    async with AsyncSessionLocal() as db:
        stored = await db.scalar(select(AnalyticsSession).where(AnalyticsSession.id == session_id))
    assert stored is not None and stored.is_bot is True


@pytest.mark.usefixtures("tracking_cleanup")
async def test_dashboard_paths_are_ignored(client: httpx.AsyncClient):
    session_id = uuid4()
    response = await client.post(
        "/api/v1/track",
        json=payload(session_id, uuid4(), uuid4(), path="/dashboard/posts"),
        headers=headers(),
    )
    assert response.status_code == 204
    assert await get_redis().llen(tracking_service.ANALYTICS_QUEUE) == 0


async def test_foreign_origin_is_rejected(client: httpx.AsyncClient):
    body = payload(uuid4(), uuid4(), uuid4())

    blocked = await client.post("/api/v1/track", json=body, headers={**headers(), "origin": "https://evil.example"})
    assert blocked.status_code == 403
    allowed = await client.post("/api/v1/track", json=body, headers={**headers(), "origin": "http://localhost:3000"})
    assert allowed.status_code == 204


async def test_analytics_endpoints_require_a_viewer_role(client: httpx.AsyncClient):
    assert (await client.get("/api/v1/analytics/realtime")).status_code == 401
    assert (await client.get("/api/v1/analytics/traffic-overview")).status_code == 401
