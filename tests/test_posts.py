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
