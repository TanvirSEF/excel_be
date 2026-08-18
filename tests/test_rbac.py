from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal
from app.models import Comment, Post, PostStatus, User, UserRole


@pytest.fixture(autouse=True)
async def rbac_test_cleanup():
    await _cleanup()
    yield
    await _cleanup()


async def _cleanup():
    async with AsyncSessionLocal() as db:
        posts = (await db.scalars(select(Post).where(Post.slug.like("rbac-test-post-%")))).all()
        for post in posts:
            await db.execute(delete(Comment).where(Comment.post_id == post.id))
        await db.execute(delete(Post).where(Post.slug.like("rbac-test-post-%")))
        await db.commit()


async def login(client, email, password):
    response = await client.post("/api/v1/auth/login", data={"username": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def create_post(client, headers, marker):
    response = await client.post(
        "/api/v1/posts",
        headers=headers,
        json={
            "title": f"Rbac test post {marker}",
            "content_json": {"blocks": [{"type": "paragraph", "text": "body"}]},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_writer_cannot_publish(client):
    writer = await login(client, "writer@test.com", "WriterPass123!")
    post = await create_post(client, writer, uuid4().hex[:8])

    submit = await client.post(f"/api/v1/posts/{post['id']}/submit-review", headers=writer)
    assert submit.status_code == 200

    publish = await client.post(f"/api/v1/posts/{post['id']}/publish", headers=writer)
    assert publish.status_code == 403


async def test_editor_can_publish(client):
    editor = await login(client, "editor@test.com", "FinalPass789!!")
    post = await create_post(client, editor, uuid4().hex[:8])

    await client.post(f"/api/v1/posts/{post['id']}/submit-review", headers=editor)
    publish = await client.post(f"/api/v1/posts/{post['id']}/publish", headers=editor)
    assert publish.status_code == 200
    assert publish.json()["status"] == "published"


async def test_writer_admin_list_shows_only_own_posts(client):
    writer = await login(client, "writer@test.com", "WriterPass123!")
    created = await create_post(client, writer, uuid4().hex[:8])

    me = await client.get("/api/v1/auth/me", headers=writer)
    writer_name = me.json()["name"]

    response = await client.get("/api/v1/posts/admin", headers=writer)
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) >= 1
    assert {item["author_name"] for item in items} == {writer_name}
    assert any(item["slug"] == created["slug"] for item in items)


async def test_seo_update_role_matrix(client, published_post):
    seo = await login(client, "seo@test.com", "SeoPass12345!")
    response = await client.patch(
        f"/api/v1/posts/{published_post.id}/seo",
        headers=seo,
        json={"meta_title": "SEO optimized title"},
    )
    assert response.status_code == 200
    assert response.json()["meta_title"] == "SEO optimized title"

    writer = await login(client, "writer@test.com", "WriterPass123!")
    response = await client.patch(
        f"/api/v1/posts/{published_post.id}/seo",
        headers=writer,
        json={"meta_title": "Writer attempt"},
    )
    assert response.status_code == 403


async def test_comment_moderation_editor_only(client, published_post):
    comment = await client.post(
        f"/api/v1/posts/{published_post.id}/comments",
        json={"user_name": "Mod", "user_email": "mod@example.com", "comment_text": "to moderate"},
    )
    assert comment.status_code == 201
    comment_id = comment.json()["id"]

    writer = await login(client, "writer@test.com", "WriterPass123!")
    response = await client.patch(
        f"/api/v1/comments/{comment_id}/moderate", headers=writer, json={"status": "approved"}
    )
    assert response.status_code == 403

    editor = await login(client, "editor@test.com", "FinalPass789!!")
    response = await client.patch(
        f"/api/v1/comments/{comment_id}/moderate", headers=editor, json={"status": "approved"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "approved"
