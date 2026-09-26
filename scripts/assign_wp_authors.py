"""Assign posts to their original authors from WordPress.

Fetches published posts from the live WordPress REST API (https://excelinsider.com/wp-json/wp/v2/posts),
extracts the original author name from `yoast_head_json.author`, matches with the 31 local users
in the `users` table, and updates `posts.author_id` in PostgreSQL.

Usage:
    # Dry run (safe, reports matches without writing)
    python scripts/assign_wp_authors.py

    # Apply updates to database
    python scripts/assign_wp_authors.py --apply
"""
import argparse
import asyncio
import os
import sys
from collections import defaultdict

import httpx
from sqlalchemy import select, update
from sqlalchemy.engine import make_url

# Add backend directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import settings
from app.core.database import AsyncSessionLocal, engine
from app.models import Post, User
from app.services import cache_service

WP_API_URL = "https://excelinsider.com/wp-json/wp/v2/posts"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ExcelInsiderMigration/1.0"}

# Handle minor spelling / nickname differences between WP and DB
NAME_ALIASES = {
    "ashfaqur rumon": "ashfqur rumon",
    "ben yameen": "ben yameen jonayed",
}


async def main():
    parser = argparse.ArgumentParser(description="Assign original WordPress authors to posts")
    parser.add_argument("--apply", action="store_true", help="Apply updates to database (default is dry-run)")
    args = parser.parse_args()

    print("=" * 60)
    print("WordPress Author Assignment Pipeline")
    print(f"Mode: {'APPLY TO DATABASE' if args.apply else 'DRY RUN (no database writes)'}")
    print("=" * 60)

    # 1. Fetch all local users from database
    async with AsyncSessionLocal() as db:
        users = (await db.scalars(select(User))).all()
        user_by_name = {}
        for u in users:
            norm = u.name.strip().lower()
            user_by_name[norm] = u
            # Also allow matching without middle name / extra spaces
            parts = norm.split()
            if len(parts) >= 2:
                user_by_name[f"{parts[0]} {parts[-1]}"] = u

        print(f"Loaded {len(users)} users from application database.")

        # 2. Fetch all posts from WordPress REST API
        print(f"\nConnecting to WordPress REST API: {WP_API_URL} ...")
        wp_posts = []
        page = 1
        total_pages = 1

        async with httpx.AsyncClient(timeout=30, headers=HEADERS, follow_redirects=True) as client:
            while page <= total_pages:
                r = await client.get(
                    WP_API_URL,
                    params={
                        "per_page": 100,
                        "page": page,
                        "_fields": "slug,author,yoast_head_json.author",
                    },
                )
                if r.status_code != 200:
                    print(f"Failed to fetch page {page}: HTTP {r.status_code}")
                    break

                if total_pages == 1:
                    total_pages = int(r.headers.get("X-WP-TotalPages", 1))
                    total_items = int(r.headers.get("X-WP-Total", 0))
                    print(f"Total WordPress posts: {total_items} across {total_pages} pages.")

                items = r.json()
                wp_posts.extend(items)
                print(f"  Fetched page {page}/{total_pages} ({len(items)} posts)", flush=True)
                page += 1

        print(f"\nTotal WordPress posts fetched: {len(wp_posts)}")

        # 3. Match posts to authors
        author_post_counts = defaultdict(int)
        unmatched_posts = []
        slug_to_author = {}

        for item in wp_posts:
            slug = item.get("slug")
            wp_author = item.get("yoast_head_json", {}).get("author", "").strip()
            if not wp_author:
                unmatched_posts.append((slug, "No author in yoast_head_json"))
                continue

            norm_author = wp_author.lower()
            norm_author = NAME_ALIASES.get(norm_author, norm_author)
            target_user = user_by_name.get(norm_author)

            if not target_user:
                # Try first + last name match
                parts = norm_author.split()
                if len(parts) >= 2:
                    target_user = user_by_name.get(f"{parts[0]} {parts[-1]}")

            if target_user:
                author_post_counts[target_user.name] += 1
                slug_to_author[slug] = target_user.id
            else:
                unmatched_posts.append((slug, wp_author))

        # 4. Print Summary
        print("\n" + "=" * 60)
        print("MATCHING SUMMARY")
        print("=" * 60)
        print(f"Total matched posts: {len(slug_to_author)}")
        print(f"Total unmatched posts: {len(unmatched_posts)}")
        print("\nBreakdown by Author:")
        for name, count in sorted(author_post_counts.items(), key=lambda x: x[1], reverse=True):
            print(f"  - {name:<30}: {count:>4} posts")

        if unmatched_posts:
            print("\nUnmatched Posts sample:")
            for s, a in unmatched_posts[:15]:
                print(f"  - {s}: {a}")

        # 5. Apply if requested
        if args.apply:
            print("\nApplying author updates in batch to database...")
            from sqlalchemy import text
            values_list = list(slug_to_author.items())
            updated_count = 0
            chunk_size = 500
            for i in range(0, len(values_list), chunk_size):
                chunk = values_list[i : i + chunk_size]
                val_strs = [f"('{s}', '{uid}')" for s, uid in chunk]
                query = text(f"""
                    UPDATE posts AS p
                    SET author_id = v.author_id::uuid,
                        updated_at = now()
                    FROM (VALUES {','.join(val_strs)}) AS v(slug, author_id)
                    WHERE p.slug = v.slug;
                """)
                res = await db.execute(query)
                updated_count += res.rowcount

            await db.commit()
            print(f"\nSUCCESS: Updated {updated_count} posts in database!")

            # Purge cache
            await cache_service.delete_pattern("posts:*")
            await cache_service.delete_pattern("post:*")
            print("Purged redis post caches.")
        else:
            print("\n[!] This was a DRY RUN. No changes were made to the database.")
            print("To apply these changes, run with: python scripts/assign_wp_authors.py --apply")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
