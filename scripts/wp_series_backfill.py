"""Rebuild tutorial series from WordPress custom-permalink URLs.

The WP site encoded series grouping only in its custom permalinks
(``/<category>/<series>/<post>/``), so the WXR import could not carry it over.
This script reads every published post's ``slug`` + ``link`` from the WP REST
API, derives the series from the first two link segments, creates ``series``
rows, assigns each post ``series_id`` and a 1-based ``series_order``
(published_at ascending), and adds ``/<category>/<series>`` → ``/series/<slug>``
redirects for the old hub URLs.
"""
import argparse
import asyncio
import os
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.core.config import settings
from app.core.database import AsyncSessionLocal, engine
from app.models import Category, Post, PostStatus, Redirect, Series
from app.services import cache_service, seo_service

CHUNK_SIZE = 100


def _is_safe_test_database() -> bool:
    parsed = make_url(settings.database_url)
    return parsed.host in {"localhost", "127.0.0.1", "::1"} or "test" in (parsed.database or "")


async def fetch_wp_posts(client: httpx.AsyncClient) -> list[dict]:
    posts: list[dict] = []
    page, total_pages = 1, 1
    while page <= total_pages:
        response = await client.get(
            "/wp-json/wp/v2/posts",
            params={"per_page": 100, "page": page, "_fields": "slug,link"},
        )
        response.raise_for_status()
        if total_pages == 1:
            total_pages = int(response.headers["X-WP-TotalPages"])
        posts.extend(response.json())
        page += 1
    return posts


def series_slug_to_name(slug: str) -> str:
    return slug.replace("-", " ").replace("/", " ").strip().title()


async def main():
    parser = argparse.ArgumentParser(description="Rebuild series from WordPress permalinks")
    parser.add_argument("--wp-url", required=True, help="base URL of the WordPress site")
    parser.add_argument("--dry-run", action="store_true", help="report planned changes, no writes")
    args = parser.parse_args()

    if os.environ.get("ALLOW_REMOTE_DB_TESTS") != "1" and not _is_safe_test_database():
        raise SystemExit(
            f"Refusing to run against remote database host {make_url(settings.database_url).host!r}. "
            "Point DATABASE_URL at a local database or set ALLOW_REMOTE_DB_TESTS=1 to override."
        )

    stats = {
        "series_created": 0,
        "series_updated": 0,
        "posts_assigned": 0,
        "redirects_created": 0,
        "wp_only_posts": 0,
        "category_anomalies": 0,
        "slug_collisions": 0,
    }

    async with httpx.AsyncClient(
        timeout=30, follow_redirects=True, base_url=args.wp_url
    ) as client:
        wp_posts = await fetch_wp_posts(client)
    print(f"fetched {len(wp_posts)} posts from WordPress", flush=True)

    # series key -> wp post slugs; posts with flat URLs are skipped
    series_members: dict[tuple[str, str], list[str]] = defaultdict(list)
    flat_posts = 0
    for wp_post in wp_posts:
        path = urlparse(wp_post["link"]).path.strip("/").split("/")
        if len(path) == 3 and all(path):
            series_members[(path[0], path[1])].append(wp_post["slug"])
        else:
            flat_posts += 1

    print(
        f"series found: {len(series_members)} | posts in series URLs: "
        f"{sum(len(v) for v in series_members.values())} | flat-URL posts skipped: {flat_posts}",
        flush=True,
    )

    async with AsyncSessionLocal() as db:
        categories = {c.id: c for c in (await db.scalars(select(Category))).all()}
        all_slugs = {slug for members in series_members.values() for slug in members}
        slug_list = list(all_slugs)
        local_posts: dict[str, Post] = {}
        for start in range(0, len(slug_list), CHUNK_SIZE):
            chunk = slug_list[start : start + CHUNK_SIZE]
            for post in (
                await db.scalars(select(Post).where(Post.slug.in_(chunk)))
            ).all():
                local_posts[post.slug] = post

        missing = all_slugs - local_posts.keys()
        stats["wp_only_posts"] = len(missing)
        if missing:
            print(f"  posts missing locally (run wp_post_sync first): {sorted(missing)[:10]}", flush=True)

        taken_slugs: set[str] = {s.slug for s in (await db.scalars(select(Series))).all()}
        existing_redirects = {
            r.old_path for r in (await db.scalars(select(Redirect))).all()
        }

        series_rows: dict[tuple[str, str], Series] = {}
        collisions: list[tuple[str, str, str]] = []
        anomalies: list[str] = []

        sorted_keys = sorted(series_members.keys())
        for url_cat, series_slug in sorted_keys:
            members = [local_posts[s] for s in series_members[(url_cat, series_slug)] if s in local_posts]
            if not members:
                continue

            category_ids = {p.category_id for p in members}
            if len(category_ids) > 1:
                stats["category_anomalies"] += 1
                anomalies.append(f"{url_cat}/{series_slug}: {len(category_ids)} distinct categories")
            category_id = next(iter(category_ids))

            db_slug = series_slug
            if db_slug in taken_slugs:
                db_slug = f"{series_slug}-{url_cat}"
                if db_slug in taken_slugs:
                    db_slug = f"{db_slug}-{abs(hash((series_slug, url_cat))) % 1000}"
                stats["slug_collisions"] += 1
                collisions.append((f"{url_cat}/{series_slug}", db_slug))
            taken_slugs.add(db_slug)

            series = await db.scalar(select(Series).where(Series.slug == db_slug))
            if series is None:
                series = Series(
                    name=series_slug_to_name(series_slug),
                    slug=db_slug,
                    category_id=category_id,
                )
                db.add(series)
                stats["series_created"] += 1
            else:
                stats["series_updated"] += 1
            series.category_id = category_id
            await db.flush()
            series_rows[(url_cat, series_slug)] = series

            ordered = sorted(
                (p for p in members if p.status == PostStatus.published and p.deleted_at is None),
                key=lambda p: (p.published_at or datetime.min.replace(tzinfo=timezone.utc)),
            )
            for order, post in enumerate(ordered, start=1):
                post.series_id = series.id
                post.series_order = order
                stats["posts_assigned"] += 1

            hub_path = f"{url_cat}/{series_slug}"
            if hub_path not in existing_redirects:
                db.add(
                    Redirect(old_path=hub_path, new_path=f"/series/{db_slug}")
                )
                existing_redirects.add(hub_path)
                stats["redirects_created"] += 1

            if not args.dry_run:
                await db.commit()

        if args.dry_run:
            await db.rollback()
        else:
            await db.commit()

        if collisions:
            print(f"\nslug collisions (url path -> db slug): {len(collisions)}")
            for url_path, db_slug in collisions[:10]:
                print(f"  {url_path} -> {db_slug}")
        if anomalies:
            print(f"\ncategory anomalies: {len(anomalies)}")
            for line in anomalies[:10]:
                print(f"  {line}")

        print(f"\n{'dry run' if args.dry_run else 'done'}: {stats}")

        if not args.dry_run:
            await cache_service.delete_keys(cache_service.CATEGORY_TREE_KEY)
            await cache_service.delete_pattern("posts:*")
            await cache_service.delete_pattern("post:*")
            await cache_service.delete_pattern("series:*")
            await cache_service.delete_pattern("curriculum:*")
            await seo_service.invalidate_sitemap()
            print("caches purged (posts, post, series, category tree, sitemap)")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
