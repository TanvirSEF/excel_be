import logging
from xml.etree import ElementTree as ET

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.redis_client import get_redis
from app.models import Category, Post, PostStatus, Redirect, Series
from app.services.curriculum_service import TRACK_MODULES

logger = logging.getLogger(__name__)

SITEMAP_KEY = "seo:sitemap"
SITEMAP_TTL = 6 * 3600
SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
POST_URL = "/blog/{slug}"
LESSON_URL = "/google-sheets/{slug}"
CATEGORY_URL = "/blog/category/{slug}"
SERIES_URL = "/series/{slug}"


async def get_sitemap() -> str:
    try:
        cached = await get_redis().get(SITEMAP_KEY)
        if cached:
            return cached
    except Exception:
        logger.warning("Redis unavailable, building sitemap uncached")
        return await build_sitemap()

    return await rebuild_sitemap()


async def rebuild_sitemap() -> str:
    xml = await build_sitemap()
    try:
        await get_redis().set(SITEMAP_KEY, xml, ex=SITEMAP_TTL)
    except Exception:
        logger.warning("Failed to cache sitemap")
    return xml


async def build_sitemap() -> str:
    async with AsyncSessionLocal() as session:
        posts = (
            await session.scalars(
                select(Post)
                .where(Post.status == PostStatus.published, Post.deleted_at.is_(None))
                .order_by(Post.published_at.desc())
            )
        ).all()
        categories = (await session.scalars(select(Category).order_by(Category.slug))).all()
        series_rows = (await session.scalars(select(Series).order_by(Series.slug))).all()

    ET.register_namespace("", SITEMAP_NS)
    urlset = ET.Element(f"{{{SITEMAP_NS}}}urlset")

    def add_url(loc: str, lastmod=None) -> None:
        url = ET.SubElement(urlset, f"{{{SITEMAP_NS}}}url")
        ET.SubElement(url, f"{{{SITEMAP_NS}}}loc").text = loc
        if lastmod is not None:
            ET.SubElement(url, f"{{{SITEMAP_NS}}}lastmod").text = lastmod.date().isoformat()

    base = settings.frontend_url.rstrip("/")
    add_url(base + "/")
    for category in categories:
        add_url(base + CATEGORY_URL.format(slug=category.slug), category.updated_at)
    for series in series_rows:
        add_url(base + SERIES_URL.format(slug=series.slug), series.updated_at)
    track_categories = set(TRACK_MODULES.get("google-sheets", []))
    for post in posts:
        url = LESSON_URL if (post.category and post.category.slug in track_categories) else POST_URL
        add_url(base + url.format(slug=post.slug), post.updated_at)

    return ET.tostring(urlset, encoding="unicode", xml_declaration=True)


async def invalidate_sitemap() -> None:
    try:
        await get_redis().delete(SITEMAP_KEY)
    except Exception:
        logger.warning("Failed to invalidate sitemap cache")


async def find_redirect(db: AsyncSession, old_path: str) -> Redirect | None:
    return await db.scalar(select(Redirect).where(Redirect.old_path == old_path.lstrip("/")))
