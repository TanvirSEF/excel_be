import pytest
from sqlalchemy import delete

from app.core.database import AsyncSessionLocal
from app.models import Comment

WRITER_EMAIL = "writer@test.com"
WRITER_PASSWORD = "WriterPass123!"


@pytest.fixture(autouse=True)
async def comments_test_cleanup():
    await _cleanup()
    yield
    await _cleanup()


async def _cleanup():
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Comment).where(Comment.comment_text.like("modq-test-%")))
        await db.commit()


async def login(client, email, password):
    response = await client.post("/api/v1/auth/login", data={"username": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def create_comment(client, post_id, marker) -> str:
    response = await client.post(
        f"/api/v1/posts/{post_id}/comments",
        json={"user_name": "Mod Queue", "user_email": "modq@example.com", "comment_text": marker},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_admin_list_requires_auth(client, published_post):
    response = await client.get("/api/v1/comments")
    assert response.status_code == 401


async def test_admin_list_forbidden_for_writers(client, published_post):
    headers = await login(client, WRITER_EMAIL, WRITER_PASSWORD)
    response = await client.get("/api/v1/comments", headers=headers)
    assert response.status_code == 403


async def test_admin_list_returns_queue_with_post_context(client, admin_token, published_post):
    await create_comment(client, str(published_post.id), "modq-test-pending")
    approved_id = await create_comment(client, str(published_post.id), "modq-test-approved")

    response = await client.patch(
        f"/api/v1/comments/{approved_id}/moderate",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"status": "approved"},
    )
    assert response.status_code == 200, response.text

    response = await client.get(
        "/api/v1/comments", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["page"] == 1
    assert body["total"] >= 2

    items = [i for i in body["items"] if i["comment_text"].startswith("modq-test-")]
    assert len(items) == 2
    by_marker = {i["comment_text"]: i for i in items}
    assert by_marker["modq-test-pending"]["status"] == "pending"
    assert by_marker["modq-test-approved"]["status"] == "approved"
    for item in items:
        assert item["post_id"] == str(published_post.id)
        assert item["post_title"] == published_post.title
        assert item["post_slug"] == published_post.slug
        assert item["user_email"] == "modq@example.com"


async def test_admin_list_filters_by_status(client, admin_token, published_post):
    spam_id = await create_comment(client, str(published_post.id), "modq-test-spam")
    await create_comment(client, str(published_post.id), "modq-test-unfiltered")

    response = await client.patch(
        f"/api/v1/comments/{spam_id}/moderate",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"status": "spam"},
    )
    assert response.status_code == 200, response.text

    response = await client.get(
        "/api/v1/comments",
        headers={"Authorization": f"Bearer {admin_token}"},
        params={"status": "spam"},
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert items, "expected at least one spam comment"
    assert all(i["status"] == "spam" for i in items)
    assert any(i["comment_text"] == "modq-test-spam" for i in items)


async def test_admin_list_paginates(client, admin_token, published_post):
    await create_comment(client, str(published_post.id), "modq-test-page")

    response = await client.get(
        "/api/v1/comments",
        headers={"Authorization": f"Bearer {admin_token}"},
        params={"page": 1, "page_size": 1},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] >= 1
    assert body["page_size"] == 1
    assert len(body["items"]) == 1
    assert body["total_pages"] == body["total"]
