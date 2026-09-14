from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException
from app.models import Category, Post, PostStatus, Series
from app.services import cache_service

TRACK_MODULES: dict[str, list[str]] = {
    "google-sheets": [
        "google-sheets-basics",
        "google-sheets-functions",
        "google-sheets-formulas",
        "google-sheets-intermediate-tutorials",
        "charts-in-google-sheets",
        "google-sheets-advanced-tutorials",
    ]
}


async def get_curriculum(db: AsyncSession, track: str) -> list[dict]:
    module_slugs = TRACK_MODULES.get(track)
    if module_slugs is None:
        raise NotFoundException("Track not found", code="TRACK_NOT_FOUND")

    cache_key = cache_service.curriculum_key(track)
    cached = await cache_service.get_json(cache_key)
    if cached is not None:
        return cached

    categories = {
        c.slug: c
        for c in (
            await db.scalars(select(Category).where(Category.slug.in_(module_slugs)))
        ).all()
    }
    series_rows = (
        await db.scalars(
            select(Series)
            .where(Series.category_id.in_([c.id for c in categories.values()]))
            .order_by(Series.order_index, Series.name)
        )
    ).all()

    lessons = (
        await db.execute(
            select(Post.series_id, Post.slug, Post.title, Post.reading_time_minutes)
            .where(
                Post.series_id.in_([s.id for s in series_rows]),
                Post.status == PostStatus.published,
                Post.deleted_at.is_(None),
            )
            .order_by(Post.series_order.asc().nulls_last(), Post.published_at.asc())
        )
    ).all()

    lessons_by_series: dict = {}
    for series_id, slug, title, reading_time in lessons:
        lessons_by_series.setdefault(series_id, []).append(
            {"slug": slug, "title": title, "reading_time_minutes": reading_time}
        )

    modules = []
    for module_slug in module_slugs:
        category = categories.get(module_slug)
        if category is None:
            continue
        topics = []
        module_lesson_count = 0
        for series in series_rows:
            if series.category_id != category.id:
                continue
            topic_lessons = lessons_by_series.get(series.id, [])
            if not topic_lessons:
                continue
            module_lesson_count += len(topic_lessons)
            topics.append(
                {
                    "slug": series.slug,
                    "name": series.name,
                    "lesson_count": len(topic_lessons),
                    "lessons": topic_lessons,
                }
            )
        if not topics:
            continue
        modules.append(
            {
                "slug": category.slug,
                "name": category.name,
                "description": category.description,
                "lesson_count": module_lesson_count,
                "topics": topics,
            }
        )

    await cache_service.set_json(cache_key, modules, cache_service.TREE_TTL)
    return modules
