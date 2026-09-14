"""Import the WordPress ``google_sheet`` CPT hub pages as first lessons.

The old site's lesson sidebar linked to 40 custom-post-type hub pages
(``/google-sheets/<module>/<topic>/``) whose content never came over with the
WXR import — so sidebar items on the new site resolved to unrelated lessons.
This script imports each CPT page as a published post, assigns it to its
matching series as the first lesson (``series_order = 0``), creates the two
missing series (Refresh Data, Export Data), and redirects the old hub URLs to
the new lesson pages. Idempotent: existing slugs are skipped.
"""
import argparse
import asyncio
import html as html_lib
import logging
import os
import re
from datetime import datetime
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.core.config import settings
from app.core.database import AsyncSessionLocal, engine
from app.models import Category, Post, PostStatus, Redirect, Series, User
from app.services import cache_service, seo_service
from app.utils.html_to_blocks import convert
from app.utils.reading_time import reading_time_minutes
from app.utils.sanitize import sanitize_html
from app.utils.shortcodes import expand_shortcodes

logging.getLogger("httpx").setLevel(logging.WARNING)

MODULE_CATEGORIES = {
    "basics": "google-sheets-basics",
    "functions": "google-sheets-functions",
    "formulas": "google-sheets-formulas",
    "intermediate-tutorial": "google-sheets-intermediate-tutorials",
    "charts": "charts-in-google-sheets",
    "advanced-tutorials": "google-sheets-advanced-tutorials",
}

# legacy CPT topic slug -> series slug (verified against the live sidebar)
TOPIC_SERIES = {
    "introduction-to-google-sheets": "introduction",
    "format-cells": "cell-formatting",
    "gridlines": "gridlines",
    "number-formatting": "number-format",
    "formatting-dates": "date-format",
    "autofill": "autofill",
    "line-break": "line-break",
    "how-to-search-data": "search",
    "characters-and-emojis": "characters-and-emojis",
    "notes-and-comments": "notes-and-comments",
    "copy-and-paste": "copy-and-paste",
    "merge-cells": "merge-cells",
    "if": "if-google-sheets-functions",
    "sumif": "sumif-google-sheets-functions",
    "countif": "countif-google-sheets-functions",
    "arrayformula": "arrayformula",
    "query": "query",
    "multiplication": "multiplication",
    "average": "average",
    "percentage": "percentage",
    "rounding": "rounding-google-sheets-formulas",
    "date": "date",
    "time": "time",
    "split-cells": "split-cells",
    "remove-characters": "remove-characters-google-sheets-formulas",
    "compare-data": "compare-data-google-sheets-formulas",
    "remove-duplicates": "remove-duplicates-google-sheets-formulas",
    "protect-data": "protect-sheets",
    "data-validation": "data-validation-google-sheets-intermediate-tutorial",
    "drop-down-list": "drop-down-google-sheets-intermediate-tutorial",
    "refresh-data": "refresh-data",
    "print-data": "print-sheets-google-sheets-intermediate-tutorial",
    "export-data": "export-data",
    "version-history": "version-history",
    "formatting": "formatting-charts",
    "trendline": "trendline-google-sheets-charts",
    "sparkline": "sparkline",
    "send-email": "send-email-google-sheets-advanced",
    "what-if-analysis": "what-if-analysis-google-sheets-advanced",
    "pivot-table": "pivot-table",
}

# series that exist only as CPT hubs — created by this import
NEW_SERIES_NAMES = {
    "refresh-data": "Refresh Google Sheets Data",
    "export-data": "Export Data from Google Sheets",
}

TAG_RE = re.compile(r"<[^>]+>")


def _is_safe_test_database() -> bool:
    parsed = make_url(settings.database_url)
    return parsed.host in {"localhost", "127.0.0.1", "::1"} or "test" in (parsed.database or "")


def plain_text(rendered: str) -> str:
    return TAG_RE.sub("", html_lib.unescape(rendered or "")).strip()


async def main():
    parser = argparse.ArgumentParser(description="Import google_sheet CPT hub pages as first lessons")
    parser.add_argument("--wp-url", required=True, help="base URL of the WordPress site")
    parser.add_argument("--author-email", required=True, help="backend user email that owns created posts")
    parser.add_argument("--dry-run", action="store_true", help="report planned creations, no writes")
    parser.add_argument("--no-images", action="store_true", help="skip image download/R2 upload")
    args = parser.parse_args()

    if os.environ.get("ALLOW_REMOTE_DB_TESTS") != "1" and not _is_safe_test_database():
        raise SystemExit(
            f"Refusing to run against remote database host {make_url(settings.database_url).host!r}. "
            "Point DATABASE_URL at a local database or set ALLOW_REMOTE_DB_TESTS=1 to override."
        )

    stats = {"created": 0, "series_created": 0, "redirects": 0, "skipped_existing": 0, "unmapped": 0}

    async with httpx.AsyncClient(
        timeout=30, follow_redirects=True, base_url=args.wp_url
    ) as client:
        response = await client.get(
            "/wp-json/wp/v2/google_sheet",
            params={"per_page": 100, "_fields": "slug,title,content,excerpt,date,link"},
        )
        response.raise_for_status()
        cpt_pages = response.json()
    print(f"fetched {len(cpt_pages)} google_sheet pages from WordPress", flush=True)

    from scripts.wp_import import MediaPipeline

    async with AsyncSessionLocal() as db:
        author = await db.scalar(select(User).where(User.email == args.author_email))
        if author is None:
            raise SystemExit(f"author email not found in database: {args.author_email}")

        categories = {
            c.slug: c for c in (await db.scalars(select(Category))).all()
        }
        series_by_slug = {s.slug: s for s in (await db.scalars(select(Series))).all()}
        media = MediaPipeline(db, author, enabled=not args.no_images)

        for page in cpt_pages:
            path = urlparse(page["link"]).path.strip("/").split("/")
            if len(path) != 3 or path[0] != "google-sheets" or path[1] not in MODULE_CATEGORIES:
                stats["unmapped"] += 1
                print(f"  ? unmapped URL: {page['link']}", flush=True)
                continue
            module_key, topic_slug = path[1], path[2]
            series_slug = TOPIC_SERIES.get(topic_slug)
            if series_slug is None:
                stats["unmapped"] += 1
                print(f"  ? unmapped topic: {topic_slug}", flush=True)
                continue

            existing = await db.scalar(select(Post).where(Post.slug == page["slug"]))
            if existing is not None:
                stats["skipped_existing"] += 1
                continue

            category = categories.get(MODULE_CATEGORIES[module_key])
            if category is None:
                stats["unmapped"] += 1
                continue

            series = series_by_slug.get(series_slug)
            if series is None and series_slug in NEW_SERIES_NAMES:
                series = Series(
                    name=NEW_SERIES_NAMES[series_slug],
                    slug=series_slug,
                    category_id=category.id,
                )
                db.add(series)
                await db.flush()
                series_by_slug[series_slug] = series
                stats["series_created"] += 1
                print(f"  + series {series_slug}", flush=True)
            if series is None:
                stats["unmapped"] += 1
                print(f"  ? series missing: {series_slug}", flush=True)
                continue

            content_html = sanitize_html(expand_shortcodes(page["content"]["rendered"] or ""))
            if media.enabled:
                for img_url in sorted(set(re.findall(r'<img[^>]+src="([^"]+)"', content_html))):
                    new_url = await media.remap(img_url)
                    if new_url != img_url:
                        content_html = content_html.replace(img_url, new_url)
            content_json = convert(content_html)

            post = Post(
                title=html_lib.unescape(page["title"]["rendered"] or "").strip() or "Untitled",
                slug=page["slug"],
                excerpt=plain_text(page["excerpt"]["rendered"])[:500] or None,
                content_json=content_json,
                content_html=content_html,
                author_id=author.id,
                category_id=category.id,
                series_id=series.id,
                series_order=0,
                status=PostStatus.published,
                reading_time_minutes=reading_time_minutes(content_json),
                published_at=datetime.fromisoformat(page["date"]),
            )
            db.add(post)

            old_path = urlparse(page["link"]).path.strip("/")
            exists = await db.scalar(select(Redirect).where(Redirect.old_path == old_path))
            if exists is None:
                db.add(
                    Redirect(
                        old_path=old_path,
                        new_path=f"/google-sheets/{page['slug']}",
                    )
                )
                stats["redirects"] += 1

            stats["created"] += 1
            print(f"  + {page['slug']} -> {series_slug}", flush=True)
            if not args.dry_run:
                await db.commit()

        if args.dry_run:
            await db.rollback()
        else:
            await db.commit()

        print(f"\n{'dry run' if args.dry_run else 'done'}: {stats}")
        print(f"images uploaded to R2: {media.uploaded}, failed: {len(media.failed)}")

        if not args.dry_run and stats["created"]:
            await cache_service.delete_pattern("posts:*")
            await cache_service.delete_pattern("post:*")
            await cache_service.delete_pattern("series:*")
            await cache_service.delete_pattern("curriculum:*")
            await seo_service.invalidate_sitemap()
            print("caches purged (posts, post, series, curriculum, sitemap)")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
