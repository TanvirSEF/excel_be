"""Backfill post categories from a live WordPress site via its REST API.

Fetches the category list and each post's category assignment from
``/wp-json/wp/v2``, creates missing categories by slug, and links posts
matched by slug. Content, images, comments, and redirects are untouched.
"""
import argparse
import asyncio
import html as html_lib

import httpx
from sqlalchemy import select

from app.core.database import AsyncSessionLocal, engine
from app.models import Category, Post
from app.services import cache_service

CHUNK_SIZE = 100


async def fetch_categories(client: httpx.AsyncClient) -> list[dict]:
    response = await client.get(
        "/wp-json/wp/v2/categories", params={"per_page": 100, "_fields": "id,name,slug"}
    )
    response.raise_for_status()
    return [c for c in response.json() if c["slug"] != "uncategorized"]


async def fetch_post_categories(client: httpx.AsyncClient) -> dict[str, int]:
    mapping: dict[str, int] = {}
    page = 1
    total_pages = None
    while total_pages is None or page <= total_pages:
        response = await client.get(
            "/wp-json/wp/v2/posts", params={"per_page": 100, "page": page, "_fields": "slug,categories"}
        )
        response.raise_for_status()
        if total_pages is None:
            total_pages = int(response.headers["X-WP-TotalPages"])
        for item in response.json():
            mapping[item["slug"]] = item["categories"][0]
        print(f"  fetched posts page {page}/{total_pages} (+{len(response.json())})", flush=True)
        page += 1
    return mapping


async def main():
    parser = argparse.ArgumentParser(description="Backfill categories from a live WordPress site")
    parser.add_argument("--wp-url", required=True, help="base URL of the WordPress site")
    parser.add_argument("--dry-run", action="store_true", help="report planned changes, no writes")
    args = parser.parse_args()

    stats = {"categories_created": 0, "posts_linked": 0, "posts_already_set": 0, "posts_missing": 0}

    async with httpx.AsyncClient(timeout=30, follow_redirects=True, base_url=args.wp_url) as client:
        wp_categories = await fetch_categories(client)
        post_categories = await fetch_post_categories(client)
    print(f"fetched {len(wp_categories)} categories, {len(post_categories)} posts from WordPress")

    async with AsyncSessionLocal() as db:
        category_by_slug: dict[str, Category] = {}
        for wp_cat in wp_categories:
            category = await db.scalar(select(Category).where(Category.slug == wp_cat["slug"]))
            if category is None:
                category = Category(name=html_lib.unescape(wp_cat["name"]), slug=wp_cat["slug"])
                db.add(category)
                stats["categories_created"] += 1
            category_by_slug[wp_cat["slug"]] = category
        await db.flush()

        category_by_wp_id = {wp_cat["id"]: category_by_slug[wp_cat["slug"]] for wp_cat in wp_categories}
        items = list(post_categories.items())

        for start in range(0, len(items), CHUNK_SIZE):
            chunk = items[start : start + CHUNK_SIZE]
            posts = (
                await db.scalars(select(Post).where(Post.slug.in_([slug for slug, _ in chunk])))
            ).all()
            post_by_slug = {post.slug: post for post in posts}

            for slug, wp_category_id in chunk:
                category = category_by_wp_id.get(wp_category_id)
                post = post_by_slug.get(slug)
                if post is None:
                    stats["posts_missing"] += 1
                elif category is None:
                    stats["posts_missing"] += 1
                elif post.category_id == category.id:
                    stats["posts_already_set"] += 1
                else:
                    post.category_id = category.id
                    stats["posts_linked"] += 1

            print(f"  processed {min(start + CHUNK_SIZE, len(items))}/{len(items)}", flush=True)
            if not args.dry_run:
                await db.commit()

        if args.dry_run:
            await db.rollback()
            print(f"\ndry run: {stats}")
            return

        await db.commit()

    await cache_service.delete_keys(cache_service.CATEGORY_TREE_KEY)
    await cache_service.delete_pattern("posts:*")
    await cache_service.delete_pattern("post:*")
    print(f"\ndone: {stats} (cache purged)")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
