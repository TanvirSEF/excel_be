import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal
from app.core.redis_client import get_redis
from app.jobs.trending_calculator import calculate_trending
from app.jobs.view_count_flusher import flush_view_counts
from app.models import Post, PostStatus, PostView, User, UserRole
from app.services import analytics_service, view_service

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TestBrowser/1.0"


@pytest.fixture(autouse=True)
async def views_test_cleanup():
    await _cleanup()
    yield
    await _cleanup()
    await get_redis().delete(view_service.VIEW_QUEUE)


async def _cleanup():
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Post).where(Post.slug.like("views-test-%")))
        await db.commit()


async def create_viewed_post() -> Post:
    async with AsyncSessionLocal() as db:
        author = await db.scalar(select(User).where(User.role == UserRole.super_admin))
        post = Post(
            title="Views test post",
            slug=f"views-test-{uuid4().hex[:8]}",
            content_json={"blocks": [{"type": "paragraph", "text": "body"}]},
            author_id=author.id,
            status=PostStatus.published,
        )
        db.add(post)
        await db.commit()
        await db.refresh(post)
        return post


async def fetch_post(post_id) -> Post:
    async with AsyncSessionLocal() as db:
        return await db.scalar(select(Post).where(Post.id == post_id))


async def fetch_views(post_id) -> list[PostView]:
    async with AsyncSessionLocal() as db:
        return list(
            await db.scalars(
                select(PostView).where(PostView.post_id == post_id).order_by(PostView.id)
            )
        )


async def test_flush_records_every_view_with_its_own_metadata():
    post = await create_viewed_post()
    await view_service.register_view(str(post.id), UA, "203.0.113.1", "https://google.com")
    await view_service.register_view(str(post.id), UA, "203.0.113.2", "https://news.ycombinator.com")
    await view_service.register_view(str(post.id), UA, "203.0.113.1", "")

    await flush_view_counts()

    rows = await fetch_views(post.id)
    assert len(rows) == 3
    assert {row.referrer for row in rows} == {"https://google.com", "https://news.ycombinator.com", None}
    fresh = await fetch_post(post.id)
    assert fresh.view_count == 3


async def test_view_counts_accumulate_across_flushes():
    post = await create_viewed_post()
    await view_service.register_view(str(post.id), UA, "203.0.113.1", None)
    await view_service.register_view(str(post.id), UA, "203.0.113.2", None)
    await flush_view_counts()

    await view_service.register_view(str(post.id), UA, "203.0.113.3", None)
    await flush_view_counts()

    rows = await fetch_views(post.id)
    assert len(rows) == 3
    fresh = await fetch_post(post.id)
    assert fresh.view_count == 3


async def test_analytics_counts_match_flushed_events():
    post = await create_viewed_post()
    await view_service.register_view(str(post.id), UA, "203.0.113.1", "https://google.com")
    await view_service.register_view(str(post.id), UA, "203.0.113.1", "https://google.com")
    await view_service.register_view(str(post.id), UA, "203.0.113.9", "https://x.com")
    await flush_view_counts()

    async with AsyncSessionLocal() as db:
        admin = await db.scalar(select(User).where(User.role == UserRole.super_admin))
        result = await analytics_service.post_analytics(db, admin, post.id)

    assert result.total_views == 3
    assert result.views_last_30_days == 3
    assert result.unique_visitors_30_days == 2
    assert {stat.referrer for stat in result.top_referrers_30_days} == {"https://google.com", "https://x.com"}
    assert result.top_referrers_30_days[0].views == 2


async def test_trending_uses_real_event_counts():
    post = await create_viewed_post()
    quiet = await create_viewed_post()
    for _ in range(5):
        await view_service.register_view(str(post.id), UA, "203.0.113.1", None)
    await view_service.register_view(str(quiet.id), UA, "203.0.113.2", None)
    await flush_view_counts()

    await calculate_trending()

    assert (await fetch_post(post.id)).is_trending is True
    assert (await fetch_post(quiet.id)).is_trending is False


async def test_bot_views_are_not_queued():
    post = await create_viewed_post()
    await view_service.register_view(str(post.id), "Googlebot/2.1", "203.0.113.1", None)
    await view_service.register_view(str(post.id), None, "203.0.113.1", None)

    assert await get_redis().llen(view_service.VIEW_QUEUE) == 0


async def test_register_view_survives_redis_outage(monkeypatch):
    def broken():
        raise RuntimeError("redis down")

    monkeypatch.setattr(view_service, "get_redis", broken)
    post = await create_viewed_post()
    await view_service.register_view(str(post.id), UA, "203.0.113.1", None)


async def test_parallel_views_flush_exactly_once():
    post = await create_viewed_post()
    await asyncio.gather(
        *[
            view_service.register_view(str(post.id), UA, f"203.0.113.{i}", None)
            for i in range(50)
        ]
    )

    await asyncio.gather(flush_view_counts(), flush_view_counts())

    rows = await fetch_views(post.id)
    assert len(rows) == 50
    fresh = await fetch_post(post.id)
    assert fresh.view_count == 50


async def test_analytics_endpoints_report_flushed_views(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    post = await create_viewed_post()
    await view_service.register_view(str(post.id), UA, "203.0.113.1", "https://google.com")
    await view_service.register_view(str(post.id), UA, "203.0.113.2", None)
    await flush_view_counts()

    detail = await client.get(f"/api/v1/analytics/posts/{post.id}", headers=headers)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["post_id"] == str(post.id)
    assert body["total_views"] == 2
    assert body["views_last_30_days"] == 2
    assert body["unique_visitors_30_days"] == 2
    assert len(body["views_last_7_days"]) == 7
    assert body["views_last_7_days"][-1]["views"] == 2

    overview = await client.get("/api/v1/analytics/overview", headers=headers)
    assert overview.status_code == 200, overview.text
    assert any(top["post_id"] == str(post.id) for top in overview.json()["top_posts_7_days"])
