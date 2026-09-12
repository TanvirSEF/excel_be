"""One-time migration: replace <mark> with <kbd> in content_html for all posts.

After the WP XML migration, [su_highlight] shortcodes were stored as <mark> tags
in content_html.  Now that we want keyboard-key badge rendering, we need to:
  1. Replace <mark>...</mark> with <kbd>...</kbd> in every post's content_html.
  2. Rebuild content_json from the corrected HTML.
  3. Purge all post caches.

Run once, after deploying the updated shortcodes / html_to_blocks changes.

Usage (from excel_be/):
    PYTHONPATH=. venv/bin/python scripts/migrate_mark_to_kbd.py --dry-run
    PYTHONPATH=. venv/bin/python scripts/migrate_mark_to_kbd.py --all
    PYTHONPATH=. venv/bin/python scripts/migrate_mark_to_kbd.py --slug <slug>
"""
import argparse
import asyncio
import re

from sqlalchemy import select
from sqlalchemy.orm import load_only

from app.core.database import AsyncSessionLocal
from app.models import Post
from app.services import cache_service
from app.utils.html_to_blocks import convert
from app.utils.reading_time import reading_time_minutes
from app.utils.sanitize import sanitize_html

_MARK_RE = re.compile(r"<mark>(.*?)</mark>", re.DOTALL)

_LOAD_COLS = [
    Post.id, Post.slug, Post.content_html, Post.content_json, Post.reading_time_minutes
]


def _convert_marks_to_kbd(html: str) -> str:
    return _MARK_RE.sub(r"<kbd>\1</kbd>", html)


def _needs_migration(post: Post) -> bool:
    return bool(post.content_html and _MARK_RE.search(post.content_html))


async def migrate_post(db, post: Post) -> int:
    fixed_html = sanitize_html(_convert_marks_to_kbd(post.content_html or ""))
    doc = convert(fixed_html)
    post.content_html = fixed_html
    post.content_json = doc
    post.reading_time_minutes = reading_time_minutes(doc)
    return len(doc["blocks"])


async def purge_caches() -> None:
    try:
        await cache_service.delete_pattern("posts:*")
        await cache_service.delete_pattern("post:*")
    except Exception:
        pass


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    async with AsyncSessionLocal() as db:
        q = select(Post).options(load_only(*_LOAD_COLS)).order_by(Post.id)

        if args.slug:
            post = await db.scalar(q.where(Post.slug == args.slug))
            if post is None:
                print(f"post not found: {args.slug}")
                return
            if not _needs_migration(post):
                print(f"{args.slug}: no <mark> tags found, skipping")
                return
            count = await migrate_post(db, post)
            await db.commit()
            await purge_caches()
            print(f"migrated {post.slug}: {count} blocks")
            return

        print("fetching posts...")
        posts = (await db.scalars(q)).all()
        candidates = [p for p in posts if _needs_migration(p)]
        print(f"total posts: {len(posts)} | posts with <mark> tags: {len(candidates)}")

        if args.dry_run:
            for p in candidates[:20]:
                marks = _MARK_RE.findall(p.content_html or "")
                print(f"  - {p.slug}  ({len(marks)} mark tags)")
            if len(candidates) > 20:
                print(f"  ... and {len(candidates) - 20} more")
            return

        if not args.all:
            print("use --all to migrate all posts, or --slug for a single post")
            return

        fixed = 0
        for index, post in enumerate(candidates, 1):
            await migrate_post(db, post)
            fixed += 1
            if index % 50 == 0:
                await db.commit()
                print(f"progress: {index}/{len(candidates)}")
        await db.commit()
        await purge_caches()
        print(f"done: migrated {fixed} posts  (<mark> to <kbd>)")


if __name__ == "__main__":
    asyncio.run(main())
