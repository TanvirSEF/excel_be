from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal
from app.models import Category, Post, PostStatus, Series, User
from app.services import cache_service

CATEGORY_SLUG = "series-test-cat"
SERIES_SLUG = "series-test-s"
POST_SLUGS = ["series-test-p1", "series-test-p2", "series-test-p3"]


@pytest.fixture(autouse=True)
async def series_test_cleanup():
    await _cleanup()
    yield
    await _cleanup()


async def _cleanup():
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Post).where(Post.slug.like("series-test-%")))
        await db.execute(delete(Series).where(Series.slug == SERIES_SLUG))
        await db.execute(delete(Category).where(Category.slug == CATEGORY_SLUG))
        await db.commit()
    await cache_service.delete_pattern("series:*")


async def seed_series():
    async with AsyncSessionLocal() as db:
        admin = await db.scalar(select(User).where(User.email == "admin@excelinsider.com"))
        category = Category(name="Series Test", slug=CATEGORY_SLUG)
        db.add(category)
        await db.flush()

        series = Series(name="Series Test Lessons", slug=SERIES_SLUG, category_id=category.id)
        db.add(series)
        await db.flush()

        # series_order deliberately different from insertion/published order
        orders = {POST_SLUGS[0]: 3, POST_SLUGS[1]: 1, POST_SLUGS[2]: 2}
        for index, slug in enumerate(POST_SLUGS):
            db.add(
                Post(
                    title=f"Series test lesson {index + 1}",
                    slug=slug,
                    content_json={"blocks": [{"type": "paragraph", "text": "body"}]},
                    author_id=admin.id,
                    category_id=category.id,
                    series_id=series.id,
                    series_order=orders[slug],
                    status=PostStatus.published,
                    published_at=datetime(2026, 1, index + 1, tzinfo=timezone.utc),
                )
            )
        await db.commit()
        return series.id


async def test_series_list_includes_post_count(client):
    await seed_series()

    response = await client.get(f"/api/v1/series?category={CATEGORY_SLUG}")
    assert response.status_code == 200, response.text

    items = response.json()
    assert len(items) == 1
    assert items[0]["slug"] == SERIES_SLUG
    assert items[0]["post_count"] == 3
    assert items[0]["category"]["slug"] == CATEGORY_SLUG


async def test_series_detail_orders_by_series_order(client):
    await seed_series()

    response = await client.get(f"/api/v1/series/{SERIES_SLUG}")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["series"]["post_count"] == 3
    assert [post["slug"] for post in body["posts"]["items"]] == [
        POST_SLUGS[1],
        POST_SLUGS[2],
        POST_SLUGS[0],
    ]


async def test_series_unknown_slug_is_404(client):
    response = await client.get("/api/v1/series/series-test-missing")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SERIES_NOT_FOUND"


async def test_post_detail_carries_series_context(client):
    await seed_series()

    response = await client.get(f"/api/v1/posts/{POST_SLUGS[1]}")
    assert response.status_code == 200, response.text

    series = response.json()["series"]
    assert series["slug"] == SERIES_SLUG
    assert series["position"] == 1
    assert series["total"] == 3
    assert series["prev"] is None
    assert series["next"]["slug"] == POST_SLUGS[2]
