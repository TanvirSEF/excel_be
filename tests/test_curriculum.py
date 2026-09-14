from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal
from app.models import Category, Post, PostStatus, Series, User
from app.services import cache_service, curriculum_service

CATEGORY_SLUG = "google-sheets-basics"
SERIES_SLUG = "curriculum-test-topic"
POST_SLUGS = ["curriculum-test-l1", "curriculum-test-l2"]


@pytest.fixture(autouse=True)
async def curriculum_test_cleanup():
    await _cleanup()
    yield
    await _cleanup()


async def _cleanup():
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Post).where(Post.slug.like("curriculum-test-%")))
        await db.execute(delete(Series).where(Series.slug == SERIES_SLUG))
        await db.execute(delete(Category).where(Category.slug == CATEGORY_SLUG))
        await db.commit()
    await cache_service.delete_pattern("curriculum:*")


async def seed_curriculum():
    async with AsyncSessionLocal() as db:
        admin = await db.scalar(select(User).where(User.email == "admin@excelinsider.com"))
        category = Category(name="Google Sheets Basics", slug=CATEGORY_SLUG)
        db.add(category)
        await db.flush()

        series = Series(name="Curriculum Topic", slug=SERIES_SLUG, category_id=category.id)
        db.add(series)
        await db.flush()

        for index, slug in enumerate(POST_SLUGS):
            db.add(
                Post(
                    title=f"Curriculum lesson {index + 1}",
                    slug=slug,
                    content_json={"blocks": [{"type": "paragraph", "text": "body"}]},
                    author_id=admin.id,
                    category_id=category.id,
                    series_id=series.id,
                    series_order=len(POST_SLUGS) - index,
                    status=PostStatus.published,
                    published_at=datetime(2026, 1, index + 1, tzinfo=timezone.utc),
                )
            )
        await db.commit()


async def test_curriculum_returns_track_tree(client):
    await seed_curriculum()

    response = await client.get("/api/v1/curriculum/google-sheets")
    assert response.status_code == 200, response.text

    modules = response.json()
    module = next(m for m in modules if m["slug"] == CATEGORY_SLUG)
    assert module["name"] == "Google Sheets Basics"

    topic = next(t for t in module["topics"] if t["slug"] == SERIES_SLUG)
    assert topic["lesson_count"] == 2
    # series_order wins over published_at (seeded reversed)
    assert [lesson["slug"] for lesson in topic["lessons"]] == [
        POST_SLUGS[1],
        POST_SLUGS[0],
    ]


async def test_curriculum_unknown_track_is_404(client):
    response = await client.get("/api/v1/curriculum/not-a-track")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "TRACK_NOT_FOUND"


async def test_track_modules_configured():
    assert "google-sheets" in curriculum_service.TRACK_MODULES
    assert len(curriculum_service.TRACK_MODULES["google-sheets"]) == 6
