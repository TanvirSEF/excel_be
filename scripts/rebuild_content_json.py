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
import re
import sys
sys.path.insert(0, ".")

from sqlalchemy import func, select, update

from app.core.database import AsyncSessionLocal
from app.models import Post
from app.services import cache_service
from app.utils.html_to_blocks import convert, is_broken
from app.utils.reading_time import reading_time_minutes
from app.utils.sanitize import sanitize_html
from app.utils.shortcodes import expand_shortcodes


def _clean_html_str(html: str | None) -> str:
    return sanitize_html(expand_shortcodes(html or ""))


def _clean_html(post):
    return _clean_html_str(post.content_html)


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
    parser.add_argument(
        "--all-posts",
        action="store_true",
        help="rebuild every post unconditionally (e.g. after converter changes)",
    )
    parser.add_argument(
        "--skip-recent-hours",
        type=float,
        default=0,
        help="skip posts updated within the last N hours",
    )
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

        if not (args.all or args.all_posts or args.dry_run):
            print("use --all/--all-posts to rebuild, or --slug for a single post")
            return

        target_ids = (
            await db.scalars(
                select(Post.id).where(Post.content_html.isnot(None)).order_by(Post.id)
            )
        ).all()
        print(f"total posts with content_html: {len(target_ids)}")

        if args.dry_run:
            sample_slugs = (
                await db.scalars(
                    select(Post.slug)
                    .where(Post.id.in_(target_ids[:10]))
                    .order_by(Post.id)
                )
            ).all()
            for s in sample_slugs:
                print(" -", s)
            return

        BATCH_SIZE = 25
        fixed = 0
        from datetime import datetime, timezone
        now_utc = datetime.now(timezone.utc)

        for i in range(0, len(target_ids), BATCH_SIZE):
            batch_ids = target_ids[i : i + BATCH_SIZE]
            batch_rows = (
                await db.execute(
                    select(
                        Post.id,
                        Post.slug,
                        Post.content_html,
                        Post.content_json,
                        Post.updated_at,
                    )
                    .where(Post.id.in_(batch_ids))
                    .order_by(Post.id)
                )
            ).all()

            updates = []
            for r in batch_rows:
                if args.skip_recent_hours and r.updated_at:
                    updated_tz = (
                        r.updated_at
                        if r.updated_at.tzinfo is not None
                        else r.updated_at.replace(tzinfo=timezone.utc)
                    )
                    age_hours = (now_utc - updated_tz).total_seconds() / 3600
                    if age_hours < args.skip_recent_hours:
                        continue

                has_formatting_tags = bool(
                    r.content_html
                    and re.search(r"<(u|ins|sup|sub)[ >]|\[(wpsm_|sc)", r.content_html, re.I)
                )
                should_rebuild = (
                    args.all_posts
                    or has_formatting_tags
                    or (
                        bool(r.content_html)
                        and (
                            _clean_html_str(r.content_html) != r.content_html
                            or is_broken(r.content_json)
                        )
                    )
                )
                if should_rebuild:
                    clean = _clean_html_str(r.content_html)
                    doc = convert(clean)
                    rt = reading_time_minutes(doc)
                    updates.append((r.id, clean, doc, rt))

            if updates:
                for attempt in range(3):
                    try:
                        for p_id, clean, doc, rt in updates:
                            await db.execute(
                                update(Post)
                                .where(Post.id == p_id)
                                .values(
                                    content_html=clean,
                                    content_json=doc,
                                    reading_time_minutes=rt,
                                    updated_at=func.now(),
                                )
                            )
                        await db.commit()
                        fixed += len(updates)
                        break
                    except Exception as err:
                        await db.rollback()
                        if attempt == 2:
                            print(f"Failed batch after 3 attempts: {err}")
                            raise
                        print(f"Retry batch due to {type(err).__name__}, waiting 1s...")
                        await asyncio.sleep(1)

            print(
                f"progress: {min(i + BATCH_SIZE, len(target_ids))}/{len(target_ids)} (rebuilt: {fixed})"
            )

        await purge_caches()
        print(f"rebuilt {fixed} posts")


if __name__ == "__main__":
    asyncio.run(main())
