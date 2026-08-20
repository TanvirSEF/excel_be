from uuid import uuid4

import pytest
from sqlalchemy import delete

from app.core.database import AsyncSessionLocal
from app.core.redis_client import get_redis
from app.jobs.trending_calculator import calculate_trending
from app.models import Category, Post
from app.services import cache_service


@pytest.fixture(autouse=True)
async def cache_test_cleanup():
    await _cleanup()
    await _flush_caches()
    yield
    await _cleanup()
    await _flush_caches()


async def _cleanup():
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Post).where(Post.slug.like("cache-test-%")))
        await db.execute(delete(Category).where(Category.slug.like("cache-test-%")))
        await db.commit()


async def _flush_caches():
    await cache_service.delete_keys(cache_service.CATEGORY_TREE_KEY)
    await cache_service.delete_pattern("post:*")
    await cache_service.delete_pattern("posts:*")


async def publish_post(client, token) -> dict:
    marker = uuid4().hex[:8]
    headers = {"Authorization": f"Bearer {token}"}
    response = await client.post(
        "/api/v1/posts",
        headers=headers,
        json={
            "title": f"Cache test post {marker}",
            "slug": f"cache-test-{marker}",
            "content_json": {"blocks": [{"type": "paragraph", "text": "body"}]},
        },
    )
    assert response.status_code == 201, response.text
    post = response.json()
    await client.post(f"/api/v1/posts/{post['id']}/submit-review", headers=headers)
    published = await client.post(f"/api/v1/posts/{post['id']}/publish", headers=headers)
    assert published.status_code == 200, published.text
    return published.json()


async def test_category_tree_reflects_new_category(client, admin_token):
    first = await client.get("/api/v1/categories")
    assert first.status_code == 200

    created = await client.post(
        "/api/v1/categories",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": f"Cache category {uuid4().hex[:8]}"},
    )
    assert created.status_code == 201, created.text
    slug = created.json()["slug"]

    second = await client.get("/api/v1/categories")
    assert slug in {category["slug"] for category in second.json()}


async def test_post_detail_reflects_update(client, admin_token):
    post = await publish_post(client, admin_token)
    headers = {"Authorization": f"Bearer {admin_token}"}

    first = await client.get(f"/api/v1/posts/{post['slug']}")
    assert first.status_code == 200
    assert first.json()["title"] == post["title"]

    renamed = await client.patch(
        f"/api/v1/posts/{post['id']}", headers=headers, json={"title": "Renamed cache post"}
    )
    assert renamed.status_code == 200, renamed.text

    second = await client.get(f"/api/v1/posts/{post['slug']}")
    assert second.status_code == 200
    assert second.json()["title"] == "Renamed cache post"


async def test_home_list_reflects_publish(client, admin_token):
    first = await client.get("/api/v1/posts")
    assert first.status_code == 200

    post = await publish_post(client, admin_token)

    second = await client.get("/api/v1/posts")
    assert post["slug"] in {item["slug"] for item in second.json()["items"]}


async def test_home_list_reflects_title_update(client, admin_token):
    post = await publish_post(client, admin_token)
    headers = {"Authorization": f"Bearer {admin_token}"}

    first = await client.get("/api/v1/posts")
    assert post["slug"] in {item["slug"] for item in first.json()["items"]}

    renamed = await client.patch(
        f"/api/v1/posts/{post['id']}", headers=headers, json={"title": "Matrix renamed post"}
    )
    assert renamed.status_code == 200, renamed.text

    second = await client.get("/api/v1/posts")
    item = next(i for i in second.json()["items"] if i["slug"] == post["slug"])
    assert item["title"] == "Matrix renamed post"


async def test_slug_rename_retires_old_detail(client, admin_token):
    post = await publish_post(client, admin_token)
    headers = {"Authorization": f"Bearer {admin_token}"}

    first = await client.get(f"/api/v1/posts/{post['slug']}")
    assert first.status_code == 200

    renamed = await client.patch(
        f"/api/v1/posts/{post['id']}",
        headers=headers,
        json={"slug": f"cache-test-{uuid4().hex[:8]}"},
    )
    assert renamed.status_code == 200, renamed.text
    new_slug = renamed.json()["slug"]

    assert (await client.get(f"/api/v1/posts/{post['slug']}")).status_code == 404
    fresh = await client.get(f"/api/v1/posts/{new_slug}")
    assert fresh.status_code == 200


async def test_soft_delete_invalidates_detail_and_list(client, admin_token):
    post = await publish_post(client, admin_token)
    headers = {"Authorization": f"Bearer {admin_token}"}

    listed = await client.get("/api/v1/posts")
    assert post["slug"] in {item["slug"] for item in listed.json()["items"]}
    assert (await client.get(f"/api/v1/posts/{post['slug']}")).status_code == 200

    deleted = await client.delete(f"/api/v1/posts/{post['id']}", headers=headers)
    assert deleted.status_code == 200, deleted.text

    assert (await client.get(f"/api/v1/posts/{post['slug']}")).status_code == 404
    fresh = await client.get("/api/v1/posts")
    assert post["slug"] not in {item["slug"] for item in fresh.json()["items"]}


async def test_category_tree_reflects_rename(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    created = await client.post(
        "/api/v1/categories",
        headers=headers,
        json={"name": f"Cache category {uuid4().hex[:8]}"},
    )
    assert created.status_code == 201, created.text
    old = created.json()

    first = await client.get("/api/v1/categories")
    assert old["slug"] in {category["slug"] for category in first.json()}

    renamed = await client.patch(
        f"/api/v1/categories/{old['id']}",
        headers=headers,
        json={"name": "Matrix renamed category"},
    )
    assert renamed.status_code == 200, renamed.text

    second = await client.get("/api/v1/categories")
    slugs = {category["slug"] for category in second.json()}
    assert renamed.json()["slug"] in slugs


async def test_trending_keys_invalidated_on_recalc():
    await get_redis().set("posts:trending:1:20", "[]")
    await calculate_trending()
    assert await get_redis().get("posts:trending:1:20") is None


async def test_cache_helpers_fail_open(monkeypatch):
    def broken():
        raise RuntimeError("redis down")

    monkeypatch.setattr(cache_service, "get_redis", broken)
    assert await cache_service.get_json("any") is None
    await cache_service.set_json("any", {}, 60)
    await cache_service.delete_keys("any")
    await cache_service.delete_pattern("posts:*")
