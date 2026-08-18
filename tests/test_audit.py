from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, or_, select

from app.core.database import AsyncSessionLocal
from app.models import AuditLog, Post, User


@pytest.fixture(autouse=True)
async def audit_test_cleanup():
    await _cleanup()
    yield
    await _cleanup()


async def _cleanup():
    async with AsyncSessionLocal() as db:
        posts = (await db.scalars(select(Post).where(Post.slug.like("audit-test-post-%")))).all()
        post_ids = [p.id for p in posts]
        users = (await db.scalars(select(User).where(User.email.like("audit-test-%")))).all()
        user_ids = [u.id for u in users]

        conditions = []
        if post_ids:
            conditions.append(AuditLog.entity_id.in_(post_ids))
        if user_ids:
            conditions.append(AuditLog.entity_id.in_(user_ids))
        if conditions:
            await db.execute(delete(AuditLog).where(or_(*conditions)))
        if post_ids:
            await db.execute(delete(Post).where(Post.id.in_(post_ids)))
        if user_ids:
            await db.execute(delete(User).where(User.id.in_(user_ids)))
        await db.commit()


async def latest_audit(action: str) -> AuditLog | None:
    async with AsyncSessionLocal() as db:
        return await db.scalar(
            select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id.desc()).limit(1)
        )


async def register_test_user(client: AsyncClient, token: str, role: str = "technical_writer") -> dict:
    marker = uuid4().hex[:8]
    response = await client.post(
        "/api/v1/auth/register",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "Audit Tester",
            "email": f"audit-test-{marker}@example.com",
            "password": "AuditPass123!",
            "role": role,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_register_creates_audit(client, admin_token):
    created = await register_test_user(client, admin_token)

    log = await latest_audit("user.create")
    assert log is not None
    assert log.entity_type == "user"
    assert str(log.entity_id) == created["id"]
    assert log.meta == {"role": "technical_writer"}


async def test_role_change_audited_with_before_after(client, admin_token):
    created = await register_test_user(client, admin_token, role="technical_writer")

    response = await client.patch(
        f"/api/v1/users/{created['id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"role": "seo_specialist"},
    )
    assert response.status_code == 200, response.text

    log = await latest_audit("user.role_change")
    assert log is not None
    assert log.meta["changes"]["role"] == {"before": "technical_writer", "after": "seo_specialist"}


async def test_self_update_audited(client, admin_token):
    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {admin_token}"})
    user_id = me.json()["id"]
    original_name = me.json()["name"]

    response = await client.patch(
        f"/api/v1/users/{user_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "Renamed Admin"},
    )
    assert response.status_code == 200, response.text

    log = await latest_audit("user.update")
    assert log is not None
    assert log.meta["changes"]["name"]["after"] == "Renamed Admin"

    await client.patch(
        f"/api/v1/users/{user_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": original_name},
    )


async def test_deactivate_audited(client, admin_token):
    created = await register_test_user(client, admin_token)

    response = await client.delete(
        f"/api/v1/users/{created['id']}", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200, response.text

    log = await latest_audit("user.deactivate")
    assert log is not None
    assert str(log.entity_id) == created["id"]


async def test_post_publish_audited(client, admin_token):
    marker = uuid4().hex[:8]
    headers = {"Authorization": f"Bearer {admin_token}"}

    response = await client.post(
        "/api/v1/posts",
        headers=headers,
        json={"title": f"Audit test post {marker}", "content_json": {"blocks": [{"type": "paragraph", "text": "body"}]}},
    )
    assert response.status_code == 201, response.text
    post = response.json()

    await client.post(f"/api/v1/posts/{post['id']}/submit-review", headers=headers)
    response = await client.post(f"/api/v1/posts/{post['id']}/publish", headers=headers)
    assert response.status_code == 200, response.text

    log = await latest_audit("post.publish")
    assert log is not None
    assert str(log.entity_id) == post["id"]
    assert log.meta["slug"] == post["slug"]


async def test_audit_list_requires_super_admin(client, admin_token):
    login = await client.post(
        "/api/v1/auth/login", data={"username": "editor@test.com", "password": "FinalPass789!!"}
    )
    editor_token = login.json()["access_token"]

    response = await client.get("/api/v1/audit-logs", headers={"Authorization": f"Bearer {editor_token}"})
    assert response.status_code == 403

    response = await client.get("/api/v1/audit-logs", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    assert len(body["items"]) >= 1
    item = body["items"][0]
    assert item["action"]
    assert item["actor_name"]
    assert "created_at" in item


async def test_audit_list_filters(client, admin_token):
    await register_test_user(client, admin_token)

    response = await client.get(
        "/api/v1/audit-logs",
        params={"entity_type": "user", "action": "user.create"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    for item in response.json()["items"]:
        assert item["entity_type"] == "user"
        assert item["action"] == "user.create"
