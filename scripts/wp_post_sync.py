"""Create posts that exist in WordPress but are missing locally.

The WXR import snapshot goes stale as new posts get published in WordPress.
This script walks the WP REST API, compares slugs against the local ``posts``
table, and creates the missing ones (status=publish only) through the same
content pipeline as the WXR importer — shortcode expansion, sanitisation,
block conversion, reading time, tags, category and a legacy-URL redirect.

``--publish-drafts`` additionally promotes posts that exist locally as
draft/pending_review (imported before WordPress published them) to published,
refreshing their content, category and tags from WordPress.
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
from app.models import Category, Post, PostStatus, Redirect, User
from app.services import cache_service, seo_service, tag_service
from app.utils.html_to_blocks import convert
from app.utils.reading_time import reading_time_minutes
from app.utils.sanitize import sanitize_html
from app.utils.shortcodes import expand_shortcodes

logging.getLogger("httpx").setLevel(logging.WARNING)

TAG_RE = re.compile(r"<[^>]+>")
CHUNK_SIZE = 100


def _is_safe_test_database() -> bool:
    parsed = make_url(settings.database_url)
    return parsed.host in {"localhost", "127.0.0.1", "::1"} or "test" in (parsed.database or "")


def plain_text(rendered: str) -> str:
    return TAG_RE.sub("", html_lib.unescape(rendered or "")).strip()


async def fetch_all(client: httpx.AsyncClient, path: str, fields: str) -> list[dict]:
    items: list[dict] = []
    page, total_pages = 1, 1
    while page <= total_pages:
        response = await client.get(
            path, params={"per_page": 100, "page": page, "_fields": fields}
        )
        response.raise_for_status()
        if total_pages == 1:
            total_pages = int(response.headers["X-WP-TotalPages"])
        items.extend(response.json())
        page += 1
    return items


async def main():
    parser = argparse.ArgumentParser(description="Create posts missing locally from WordPress")
    parser.add_argument("--wp-url", required=True, help="base URL of the WordPress site")
    parser.add_argument("--author-email", required=True, help="backend user email that owns created posts")
    parser.add_argument("--dry-run", action="store_true", help="report planned creations, no writes")
    parser.add_argument(
        "--publish-drafts",
        action="store_true",
        help="also promote local draft/pending_review posts to published from WordPress",
    )
    args = parser.parse_args()

    if os.environ.get("ALLOW_REMOTE_DB_TESTS") != "1" and not _is_safe_test_database():
        raise SystemExit(
            f"Refusing to run against remote database host {make_url(settings.database_url).host!r}. "
            "Point DATABASE_URL at a local database or set ALLOW_REMOTE_DB_TESTS=1 to override."
        )

    stats = {"created": 0, "promoted": 0, "redirects": 0, "existing_skipped": 0}

    async with httpx.AsyncClient(
        timeout=30, follow_redirects=True, base_url=args.wp_url
    ) as client:
        wp_posts = await fetch_all(
            client,
            "/wp-json/wp/v2/posts",
            "slug,title,content,excerpt,date,categories,tags,link,status",
        )
        wp_categories = {
            c["id"]: c["slug"]
            for c in await fetch_all(client, "/wp-json/wp/v2/categories", "id,slug")
        }
        wp_tags = {t["id"]: t["name"] for t in await fetch_all(client, "/wp-json/wp/v2/tags", "id,name")}
    print(f"fetched {len(wp_posts)} posts, {len(wp_categories)} categories, {len(wp_tags)} tags", flush=True)

    async with AsyncSessionLocal() as db:
        author = await db.scalar(select(User).where(User.email == args.author_email))
        if author is None:
            raise SystemExit(f"author email not found in database: {args.author_email}")

        local_posts: dict[str, Post] = {}
        all_wp_slugs = [p["slug"] for p in wp_posts]
        for start in range(0, len(all_wp_slugs), CHUNK_SIZE):
            chunk = all_wp_slugs[start : start + CHUNK_SIZE]
            for post in (await db.scalars(select(Post).where(Post.slug.in_(chunk)))).all():
                local_posts[post.slug] = post

        for wp_post in wp_posts:
            if wp_post.get("status") != "publish":
                continue

            existing = local_posts.get(wp_post["slug"])
            if existing is not None:
                promotable = existing.status in (PostStatus.draft, PostStatus.pending_review)
                if not (args.publish_drafts and promotable):
                    stats["existing_skipped"] += 1
                    continue

            content_html = sanitize_html(expand_shortcodes(wp_post["content"]["rendered"] or ""))
            content_json = convert(content_html)
            title = html_lib.unescape(wp_post["title"]["rendered"] or "").strip() or "Untitled"
            excerpt = plain_text(wp_post["excerpt"]["rendered"])[:500] or None

            category_id = None
            category_slugs = [wp_categories.get(cid) for cid in wp_post.get("categories", [])]
            for cat_slug in category_slugs:
                if cat_slug and cat_slug != "uncategorized":
                    category = await db.scalar(select(Category).where(Category.slug == cat_slug))
                    if category is not None:
                        category_id = category.id
                        break

            if existing is None:
                existing = Post(slug=wp_post["slug"], author_id=author.id, content_json=content_json)
                db.add(existing)
                stats["created"] += 1
                marker = "+"
            else:
                stats["promoted"] += 1
                marker = "↑"

            existing.title = title
            existing.excerpt = excerpt
            existing.content_json = content_json
            existing.content_html = content_html
            existing.category_id = category_id
            existing.status = PostStatus.published
            existing.reading_time_minutes = reading_time_minutes(content_json)
            existing.published_at = datetime.fromisoformat(wp_post["date"])
            await db.flush()

            tag_names = [wp_tags[tid] for tid in wp_post.get("tags", []) if tid in wp_tags]
            if tag_names:
                await tag_service.sync_post_tags(db, existing, tag_names)

            old_path = urlparse(wp_post["link"]).path.strip("/")
            if old_path and old_path != wp_post["slug"]:
                redirect = await db.scalar(select(Redirect).where(Redirect.old_path == old_path))
                if redirect is None:
                    db.add(Redirect(old_path=old_path, new_path=f"/blog/{wp_post['slug']}"))
                    stats["redirects"] += 1

            print(f"  {marker} {wp_post['slug']}", flush=True)
            if not args.dry_run:
                await db.commit()

        if args.dry_run:
            await db.rollback()
        else:
            await db.commit()

        print(f"\n{'dry run' if args.dry_run else 'done'}: {stats}")

        if not args.dry_run and (stats["created"] or stats["promoted"]):
            await cache_service.delete_pattern("posts:*")
            await cache_service.delete_pattern("post:*")
            await seo_service.invalidate_sitemap()
            print("caches purged (posts, post, sitemap)")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
