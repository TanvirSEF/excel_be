import json
from collections.abc import AsyncIterator

import httpx
import pytest

from app.core.config import settings
from app.core.redis_client import get_redis
from app.models import Post
from app.services import cache_service, view_service

pytestmark = pytest.mark.usefixtures("isolated_analytics_redis")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def beacon_headers(ip: str = "203.0.113.21") -> dict:
    return {"user-agent": UA, "cf-connecting-ip": ip, "cf-ipcountry": "BD"}


async def queued_for(post: Post) -> list[dict]:
    raw = await get_redis().lrange(view_service.VIEW_QUEUE, 0, -1)
    return [event for event in map(json.loads, raw) if event["post_id"] == str(post.id)]


@pytest.fixture
async def clean_queue(published_post: Post) -> AsyncIterator[Post]:
    r = get_redis()

    async def purge() -> None:
        for raw in await r.lrange(view_service.VIEW_QUEUE, 0, -1):
            if json.loads(raw)["post_id"] == str(published_post.id):
                await r.lrem(view_service.VIEW_QUEUE, 0, raw)
        for key in [key async for key in r.scan_iter(f"views:seen:{published_post.id}:*")]:
            await r.delete(key)
        await cache_service.delete_keys(cache_service.post_detail_key(published_post.slug))

    await purge()
    yield published_post
    await purge()


async def test_reading_a_post_through_the_api_no_longer_counts_a_view(client: httpx.AsyncClient, clean_queue: Post):
    response = await client.get(f"/api/v1/posts/{clean_queue.slug}", headers=beacon_headers())
    assert response.status_code == 200
    assert await queued_for(clean_queue) == []


async def test_view_beacon_counts_once_per_visitor_window(client: httpx.AsyncClient, clean_queue: Post):
    url = f"/api/v1/posts/{clean_queue.slug}/view"

    assert (await client.post(url, headers=beacon_headers())).status_code == 204
    assert (await client.post(url, headers=beacon_headers())).status_code == 204
    assert len(await queued_for(clean_queue)) == 1

    assert (await client.post(url, headers=beacon_headers(ip="203.0.113.22"))).status_code == 204
    events = await queued_for(clean_queue)
    assert len(events) == 2
    assert all("ip_address" not in event for event in events)


async def test_view_beacon_rejects_foreign_origins_and_bots(client: httpx.AsyncClient, clean_queue: Post):
    url = f"/api/v1/posts/{clean_queue.slug}/view"

    foreign = await client.post(url, headers={**beacon_headers(), "origin": "https://evil.example"})
    assert foreign.status_code == 403
    bot = await client.post(url, headers={**beacon_headers(), "user-agent": "Googlebot/2.1"})
    assert bot.status_code == 204
    assert await queued_for(clean_queue) == []


async def test_view_beacon_is_rate_limited(
    client: httpx.AsyncClient, clean_queue: Post, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "rate_limit_view", "2/1minute")
    url = f"/api/v1/posts/{clean_queue.slug}/view"
    headers = beacon_headers(ip="203.0.113.99")

    statuses = [(await client.post(url, headers=headers)).status_code for _ in range(3)]
    assert statuses == [204, 204, 429]
