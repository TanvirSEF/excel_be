"""Rebuild content_json blocks and clean content_html for WP-migrated posts.

content_html is the source of truth: the original migration split WP HTML
into fragments losing inline runs, and plugin shortcodes (wpsm_*, su_*,
[sc]) were stored as literal text. This script expands those shortcodes,
re-sanitizes the HTML and rebuilds structured blocks from it.

Usage (from excel_be/):
    PYTHONPATH=. venv/bin/python scripts/rebuild_content_json.py --dry-run
    PYTHONPATH=. venv/bin/python scripts/rebuild_content_json.py --slug <slug>
    PYTHONPATH=. venv/bin/python scripts/rebuild_content_json.py --all
"""
import argparse
import asyncio

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models import Post
from app.services import cache_service
from app.utils.html_to_blocks import convert, is_broken
from app.utils.reading_time import reading_time_minutes
from app.utils.sanitize import sanitize_html
from app.utils.shortcodes import expand_shortcodes


def _clean_html(post):
    return sanitize_html(expand_shortcodes(post.content_html or ""))


def needs_rebuild(post):
    if not post.content_html:
        return False
    return _clean_html(post) != post.content_html or is_broken(post.content_json)


async def rebuild_post(db, post):
    clean = _clean_html(post)
    doc = convert(clean)
    post.content_html = clean
    post.content_json = doc
    post.reading_time_minutes = reading_time_minutes(doc)
    return len(doc["blocks"])


async def purge_caches():
    await cache_service.delete_pattern("posts:*")
    await cache_service.delete_pattern("post:*")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    async with AsyncSessionLocal() as db:
        if args.slug:
            post = await db.scalar(select(Post).where(Post.slug == args.slug))
            if post is None:
                print(f"post not found: {args.slug}")
                return
            count = await rebuild_post(db, post)
            await db.commit()
            await purge_caches()
            print(f"rebuilt {post.slug}: {count} blocks")
            return

        posts = (await db.scalars(select(Post).order_by(Post.id))).all()
        candidates = [p for p in posts if needs_rebuild(p)]
        print(f"total posts: {len(posts)} | needing rebuild: {len(candidates)}")

        if args.dry_run:
            for p in candidates[:10]:
                print(" -", p.slug)
            return

        if not args.all:
            print("use --all to rebuild, or --slug for a single post")
            return

        fixed = 0
        for index, post in enumerate(candidates, 1):
            await rebuild_post(db, post)
            fixed += 1
            if index % 50 == 0:
                await db.commit()
                print(f"progress: {index}/{len(candidates)}")
        await db.commit()
        await purge_caches()
        print(f"rebuilt {fixed} posts")


if __name__ == "__main__":
    asyncio.run(main())
