import logging
from xml.etree import ElementTree as ET

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.redis_client import get_redis
from app.models import Category, Post, PostStatus, Redirect, Series, User
from app.services.curriculum_service import TRACK_MODULES

logger = logging.getLogger(__name__)

SITEMAP_KEY = "seo:sitemap"
SITEMAP_TTL = 6 * 3600
SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
POST_URL = "/blog/{slug}/"
LESSON_URL = "/google-sheets/{slug}/"
CATEGORY_URL = "/category/{slug}/"
SERIES_URL = "/series/{slug}/"
AUTHOR_URL = "/author/{slug}/"

STATIC_PAGES = [
    "/",
    "/about/",
    "/about/nehad-ulfat/",
    "/contact/",
    "/google-sheets/",
    "/category/",
    "/spreadsheet-solutions/",
    "/custom-templates/",
    "/custom-tools/",
    "/privacy/",
    "/terms/",
    "/cookie-policy/",
]

CALCULATOR_PAGES = [
    "/calculator/",
    "/calculator/accounting/",
    "/calculator/finance/",
    "/calculator/statistics/",
    # Accounting calculators (15)
    "/calculator/accounting/retail-profit-margin/",
    "/calculator/accounting/wholesale-margin/",
    "/calculator/accounting/reverse-margin/",
    "/calculator/accounting/amazon-seller-commission/",
    "/calculator/accounting/salesperson-profitability/",
    "/calculator/accounting/payroll-with-overtime/",
    "/calculator/accounting/gross-up-payroll/",
    "/calculator/accounting/prorated-bonus/",
    "/calculator/accounting/sales-commission/",
    "/calculator/accounting/payroll-conversion/",
    "/calculator/accounting/retained-earnings/",
    "/calculator/accounting/cash-conversion-cycle/",
    "/calculator/accounting/debt-payoff-with-extra-payments/",
    "/calculator/accounting/debt-snowball-vs-avalanche/",
    "/calculator/accounting/marginal-propensity-to-consume/",
    # Finance calculators (17)
    "/calculator/finance/share-profit/",
    "/calculator/finance/dividend-reinvestment-plan/",
    "/calculator/finance/dividend-snowball/",
    "/calculator/finance/living-off-dividends/",
    "/calculator/finance/cost-of-equity/",
    "/calculator/finance/rental-property-roi/",
    "/calculator/finance/cash-on-cash-roi/",
    "/calculator/finance/solar-roi/",
    "/calculator/finance/marketing-roi/",
    "/calculator/finance/enterprise-seo-roi/",
    "/calculator/finance/b2b-roi/",
    "/calculator/finance/venture-capital/",
    "/calculator/finance/internal-rate-of-return/",
    "/calculator/finance/accounting-rate-of-return/",
    "/calculator/finance/holding-period-return/",
    "/calculator/finance/retirement-rate-of-return/",
    "/calculator/finance/savings-withdrawal/",
    # Statistics calculators (13)
    "/calculator/statistics/weighted-average-grade/",
    "/calculator/statistics/geometric-mean/",
    "/calculator/statistics/harmonic-mean/",
    "/calculator/statistics/interquartile-range/",
    "/calculator/statistics/coefficient-of-variation/",
    "/calculator/statistics/pooled-variance/",
    "/calculator/statistics/sample-size/",
    "/calculator/statistics/margin-of-error/",
    "/calculator/statistics/z-score-to-percentile/",
    "/calculator/statistics/p-value-from-z-score/",
    "/calculator/statistics/critical-z-value/",
    "/calculator/statistics/weighted-average-overtime/",
    "/calculator/statistics/volume-weighted-average-price/",
]


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
        authors = (
            await session.scalars(
                select(User)
                .join(Post, Post.author_id == User.id)
                .where(
                    User.is_active.is_(True),
                    User.slug.isnot(None),
                    Post.status == PostStatus.published,
                    Post.deleted_at.is_(None),
                )
                .distinct()
                .order_by(User.slug)
            )
        ).all()

    ET.register_namespace("", SITEMAP_NS)
    urlset = ET.Element(f"{{{SITEMAP_NS}}}urlset")

    def add_url(loc: str, lastmod=None) -> None:
        url = ET.SubElement(urlset, f"{{{SITEMAP_NS}}}url")
        ET.SubElement(url, f"{{{SITEMAP_NS}}}loc").text = loc
        if lastmod is not None:
            ET.SubElement(url, f"{{{SITEMAP_NS}}}lastmod").text = lastmod.date().isoformat()

    base = settings.frontend_url.rstrip("/")

    # Static hub and core service pages
    for path in STATIC_PAGES:
        add_url(base + path)

    # Calculators
    for path in CALCULATOR_PAGES:
        add_url(base + path)

    # Categories
    for category in categories:
        add_url(base + CATEGORY_URL.format(slug=category.slug), category.updated_at)

    # Series
    for series in series_rows:
        add_url(base + SERIES_URL.format(slug=series.slug), series.updated_at)

    # Authors
    for author in authors:
        add_url(base + AUTHOR_URL.format(slug=author.slug), author.updated_at)

    # Posts & Lessons
    track_categories = set(TRACK_MODULES.get("google-sheets", []))
    for post in posts:
        if post.canonical_url:
            post_path = post.canonical_url if post.canonical_url.startswith("/") else f"/{post.canonical_url}"
            if not post_path.endswith("/"):
                post_path = f"{post_path}/"
            add_url(base + post_path, post.updated_at)
        else:
            url = LESSON_URL if (post.category and post.category.slug in track_categories) else POST_URL
            add_url(base + url.format(slug=post.slug), post.updated_at)

    return ET.tostring(urlset, encoding="unicode", xml_declaration=True)


async def invalidate_sitemap() -> None:
    try:
        await get_redis().delete(SITEMAP_KEY)
    except Exception:
        logger.warning("Failed to invalidate sitemap cache")


async def find_redirect(db: AsyncSession, old_path: str) -> Redirect | None:
    clean = old_path.strip("/")
    redirect = await db.scalar(select(Redirect).where(Redirect.old_path == clean))
    if redirect is not None:
        new_path = redirect.new_path
        if new_path.startswith("/calculators"):
            new_path = new_path.replace("/calculators", "/calculator", 1)
        if not new_path.endswith("/") and "?" not in new_path:
            new_path = f"{new_path}/"
        redirect.new_path = new_path
        return redirect

    post = await db.scalar(
        select(Post).where(
            (Post.canonical_url == f"/{clean}")
            | (Post.canonical_url == clean)
            | (Post.canonical_url == f"/{clean}/"),
            Post.status == PostStatus.published,
            Post.deleted_at.is_(None),
        )
    )
    if post is not None:
        track_categories = set(TRACK_MODULES.get("google-sheets", []))
        if post.category and post.category.slug in track_categories:
            target_path = f"/google-sheets/{post.slug}/"
        else:
            target_path = f"/blog/{post.slug}/"
        return Redirect(old_path=clean, new_path=target_path, redirect_type=301)

    return None
