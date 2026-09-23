"""Convert standalone YouTube and video URLs into structured embed blocks across all posts.

Usage (from excel_be/):
    python scripts/convert_video_embeds.py --dry-run
    python scripts/convert_video_embeds.py --slug how-to-get-summary-statistics-in-excel
    python scripts/convert_video_embeds.py --all
"""
import argparse
import asyncio
import os
import sys

# Ensure working directory is excel_be root so .env is loaded correctly
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)

from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.models import Post
from app.services import cache_service
from app.utils.html_to_blocks import extract_video_url, convert
from app.utils.reading_time import reading_time_minutes
from app.utils.sanitize import sanitize_html
from app.utils.shortcodes import expand_shortcodes


def find_convertible_blocks(post: Post):
    blocks = (post.content_json or {}).get("blocks", [])
    conversions = []
    for idx, block in enumerate(blocks):
        if block.get("type") == "paragraph":
            text = (block.get("text") or "").strip()
            runs = block.get("content") or []
            video_url = extract_video_url(text, runs)
            if video_url:
                conversions.append((idx, text, video_url))
    return conversions


def transform_content_json(post: Post):
    blocks = list((post.content_json or {}).get("blocks", []))
    modified = False
    for idx, block in enumerate(blocks):
        if block.get("type") == "paragraph":
            text = (block.get("text") or "").strip()
            runs = block.get("content") or []
            video_url = extract_video_url(text, runs)
            if video_url:
                blocks[idx] = {"type": "embed", "url": video_url}
                modified = True
    if modified:
        return {"blocks": blocks}
    return None


async def purge_caches():
    try:
        await cache_service.delete_pattern("posts:*")
        await cache_service.delete_pattern("post:*")
    except Exception as exc:
        print(f"Warning: Failed to purge redis cache: {exc}")


async def main():
    parser = argparse.ArgumentParser(description="Convert video URLs into structured embed blocks")
    parser.add_argument("--slug", help="Convert a single post by slug")
    parser.add_argument("--all", action="store_true", help="Apply conversion to all matching posts")
    parser.add_argument("--dry-run", action="store_true", help="Preview matching posts without modifying database")
    args = parser.parse_args()

    if not (args.dry_run or args.all or args.slug):
        print("Please specify --dry-run, --all, or --slug <slug>")
        return

    async with AsyncSessionLocal() as db:
        if args.slug:
            post = await db.scalar(select(Post).where(Post.slug == args.slug))
            if not post:
                print(f"Post not found: {args.slug}")
                return
            candidates = [post]
        else:
            all_posts = (await db.scalars(select(Post).order_by(Post.id))).all()
            candidates = [p for p in all_posts if find_convertible_blocks(p)]

        print(f"Found {len(candidates)} posts with convertible video URLs:")
        for p in candidates:
            conversions = find_convertible_blocks(p)
            print(f"\n- [{p.slug}] \"{p.title}\"")
            for idx, old_text, new_url in conversions:
                print(f"    Block {idx}: paragraph -> embed: {new_url}")

        if args.dry_run:
            print("\n[Dry run complete. No database changes were made. Run with --all to apply.]")
            return

        print(f"\nApplying conversions to {len(candidates)} posts...")
        converted_count = 0
        for index, p in enumerate(candidates, 1):
            new_doc = transform_content_json(p)
            if new_doc:
                p.content_json = new_doc
                p.reading_time_minutes = reading_time_minutes(new_doc)
                converted_count += 1
            if index % 20 == 0:
                await db.commit()
                print(f"  Progress: {index}/{len(candidates)}")

        await db.commit()
        await purge_caches()
        print(f"\nSuccessfully converted {converted_count} posts and purged Redis cache!")


if __name__ == "__main__":
    asyncio.run(main())
