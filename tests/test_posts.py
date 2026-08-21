from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal
from app.models import Post, PostTag, Tag, User, UserRole
from app.schemas.post import PostUpdate
from app.services import post_service, tag_service


@pytest.fixture(autouse=True)
async def posts_test_cleanup():
    await _cleanup()
    yield
    await _cleanup()


async def _cleanup():
    async with AsyncSessionLocal() as db:
        posts = (await db.scalars(select(Post).where(Post.slug.like("posts-test-%")))).all()
        for post in posts:
            await db.execute(delete(PostTag).where(PostTag.post_id == post.id))
        await db.execute(delete(Post).where(Post.slug.like("posts-test-%")))
        for tag_name in ("Alpha Tag", "Beta Tag", "Gamma Tag"):
            await db.execute(delete(Tag).where(Tag.name == tag_name))
        await db.commit()


async def create_post(client: httpx.AsyncClient, token: str, **overrides) -> dict:
    payload = {
        "title": f"Posts test {uuid4().hex[:8]}",
        "content_json": {"blocks": [{"type": "paragraph", "text": "body"}]},
    }
    payload.update(overrides)
    response = await client.post(
        "/api/v1/posts",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def current_tags(post_id: UUID) -> list[str]:
    async with AsyncSessionLocal() as db:
        return await tag_service.post_tag_names(db, post_id)


async def login(client: httpx.AsyncClient, email: str, password: str) -> dict:
    response = await client.post("/api/v1/auth/login", data={"username": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def get_token(client: httpx.AsyncClient, email: str, password: str) -> str:
    response = await client.post("/api/v1/auth/login", data={"username": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


async def test_get_by_id_returns_draft_for_editor(client, admin_token):
    post = await create_post(client, admin_token, slug=f"posts-test-{uuid4().hex[:8]}")

    response = await client.get(
        f"/api/v1/posts/{post['id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == post["id"]
    assert body["status"] == "draft"
    assert body["content_json"] == {"blocks": [{"type": "paragraph", "text": "body"}]}


async def test_get_by_id_allows_writer_for_own_post(client):
    writer_token = await get_token(client, "writer@test.com", "WriterPass123!")
    post = await create_post(client, writer_token, slug=f"posts-test-{uuid4().hex[:8]}")

    response = await client.get(
        f"/api/v1/posts/{post['id']}", headers={"Authorization": f"Bearer {writer_token}"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["id"] == post["id"]


async def test_get_by_id_scopes_writers_to_own_posts(client, admin_token):
    writer_headers = await login(client, "writer@test.com", "WriterPass123!")
    post = await create_post(client, admin_token, slug=f"posts-test-{uuid4().hex[:8]}")

    response = await client.get(f"/api/v1/posts/{post['id']}", headers=writer_headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERMISSION_DENIED"


async def test_get_by_id_forbidden_for_seo_specialist(client, admin_token):
    seo_headers = await login(client, "seo@test.com", "SeoPass12345!")
    post = await create_post(client, admin_token, slug=f"posts-test-{uuid4().hex[:8]}")

    response = await client.get(f"/api/v1/posts/{post['id']}", headers=seo_headers)
    assert response.status_code == 403


async def test_get_by_id_requires_auth(client, admin_token):
    post = await create_post(client, admin_token, slug=f"posts-test-{uuid4().hex[:8]}")

    response = await client.get(f"/api/v1/posts/{post['id']}")
    assert response.status_code == 401


async def test_get_by_id_unknown_uuid_returns_404(client, admin_token):
    response = await client.get(
        f"/api/v1/posts/{uuid4()}", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "POST_NOT_FOUND"


async def test_get_by_non_uuid_path_still_resolves_as_slug(client, admin_token):
    response = await client.get("/api/v1/posts/posts-test-not-a-uuid")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "POST_NOT_FOUND"


async def test_create_with_tags_persists_single_transaction(client, admin_token):
    post = await create_post(
        client, admin_token, slug=f"posts-test-{uuid4().hex[:8]}", tags=["Alpha Tag", "Beta Tag"]
    )
    assert post["tags"] == ["Alpha Tag", "Beta Tag"]
    assert await current_tags(UUID(post["id"])) == ["Alpha Tag", "Beta Tag"]


async def test_update_replaces_tags(client, admin_token):
    post = await create_post(
        client, admin_token, slug=f"posts-test-{uuid4().hex[:8]}", tags=["Alpha Tag"]
    )

    response = await client.patch(
        f"/api/v1/posts/{post['id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"tags": ["Beta Tag"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["tags"] == ["Beta Tag"]
    assert await current_tags(UUID(post["id"])) == ["Beta Tag"]


async def test_failed_update_after_tag_sync_rolls_back_tags(client, admin_token, monkeypatch):
    post = await create_post(
        client, admin_token, slug=f"posts-test-{uuid4().hex[:8]}", tags=["Alpha Tag"]
    )

    def broken_render(content_json):
        raise RuntimeError("render failed")

    monkeypatch.setattr(post_service, "_render_html", broken_render)

    async with AsyncSessionLocal() as db:
        admin = await db.scalar(select(User).where(User.role == UserRole.super_admin))
        with pytest.raises(RuntimeError):
            await post_service.update(
                db,
                admin,
                UUID(post["id"]),
                PostUpdate(tags=["Gamma Tag"], content_json={"blocks": []}),
            )

    assert await current_tags(UUID(post["id"])) == ["Alpha Tag"]
