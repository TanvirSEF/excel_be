from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal
from app.core.exceptions import ValidationException
from app.core.security import hash_password
from app.models import AuditLog, Comment, Post, PostStatus, RefreshToken, User, UserRole
from app.schemas.user import UserUpdate
from app.services import user_service


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
        users = (await db.scalars(select(User).where(User.email.like("rbac-test-%")))).all()
        if users:
            user_ids = [user.id for user in users]
            await db.execute(delete(AuditLog).where(AuditLog.entity_id.in_(user_ids)))
            await db.execute(delete(RefreshToken).where(RefreshToken.user_id.in_(user_ids)))
            await db.execute(delete(User).where(User.id.in_(user_ids)))
        await db.commit()


async def login(client, email, password):
    response = await client.post("/api/v1/auth/login", data={"username": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def register_rbac_user(client, admin_token, role="seo_specialist") -> dict:
    marker = uuid4().hex[:8]
    response = await client.post(
        "/api/v1/auth/register",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "name": "Rbac Tester",
            "email": f"rbac-test-{marker}@example.com",
            "password": "RbacPass123!",
            "role": role,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_super_admin_cannot_deactivate_self_via_patch(client, admin_token):
    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {admin_token}"})
    own_id = me.json()["id"]

    response = await client.patch(
        f"/api/v1/users/{own_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"is_active": False},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "SELF_DEACTIVATION"

    fresh = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {admin_token}"})
    assert fresh.status_code == 200


async def test_patch_deactivation_revokes_sessions(client, admin_token):
    created = await register_rbac_user(client, admin_token)
    session = await client.post(
        "/api/v1/auth/login",
        data={"username": created["email"], "password": "RbacPass123!"},
    )
    refresh = session.json()["refresh_token"]

    response = await client.patch(
        f"/api/v1/users/{created['id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"is_active": False},
    )
    assert response.status_code == 200, response.text

    replay = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert replay.status_code == 401


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


async def _make_sole_super_admin() -> tuple[User, User]:
    marker = uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        seeded = await db.scalar(select(User).where(User.email == "admin@excelinsider.com"))
        seeded.is_active = False
        target = User(
            name="Rbac Tester",
            email=f"rbac-test-{marker}@example.com",
            password_hash=hash_password("RbacPass123!"),
            role=UserRole.super_admin,
            is_active=True,
            is_verified=True,
        )
        db.add(target)
        await db.commit()
        await db.refresh(target)
        return seeded, target


async def _restore_super_admins(seeded: User, target: User) -> None:
    async with AsyncSessionLocal() as db:
        seeded = await db.scalar(select(User).where(User.id == seeded.id))
        seeded.is_active = True
        stale = await db.scalar(select(User).where(User.id == target.id))
        if stale is not None:
            await db.delete(stale)
        await db.commit()


async def test_last_super_admin_cannot_be_demoted():
    seeded, target = await _make_sole_super_admin()
    try:
        async with AsyncSessionLocal() as db:
            current = User(id=seeded.id, role=UserRole.super_admin)
            with pytest.raises(ValidationException) as exc:
                await user_service.update_user(
                    db, current, target.id, UserUpdate(role=UserRole.senior_editor)
                )
            assert exc.value.code == "LAST_SUPER_ADMIN"
    finally:
        await _restore_super_admins(seeded, target)


async def test_last_super_admin_cannot_be_deactivated_via_patch():
    seeded, target = await _make_sole_super_admin()
    try:
        async with AsyncSessionLocal() as db:
            current = User(id=seeded.id, role=UserRole.super_admin)
            with pytest.raises(ValidationException) as exc:
                await user_service.update_user(
                    db, current, target.id, UserUpdate(is_active=False)
                )
            assert exc.value.code == "LAST_SUPER_ADMIN"
    finally:
        await _restore_super_admins(seeded, target)


async def test_last_super_admin_cannot_be_deactivated():
    seeded, target = await _make_sole_super_admin()
    try:
        async with AsyncSessionLocal() as db:
            current = User(id=seeded.id, role=UserRole.super_admin)
            with pytest.raises(ValidationException) as exc:
                await user_service.deactivate_user(db, current, target.id)
            assert exc.value.code == "LAST_SUPER_ADMIN"
    finally:
        await _restore_super_admins(seeded, target)


async def test_super_admin_demotes_when_another_remains():
    marker = uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        seeded = await db.scalar(select(User).where(User.email == "admin@excelinsider.com"))
        target = User(
            name="Rbac Tester",
            email=f"rbac-test-{marker}@example.com",
            password_hash=hash_password("RbacPass123!"),
            role=UserRole.super_admin,
            is_active=True,
            is_verified=True,
        )
        db.add(target)
        await db.commit()
        await db.refresh(target)
        current = User(id=seeded.id, role=UserRole.super_admin)
        updated = await user_service.update_user(
            db, current, target.id, UserUpdate(role=UserRole.seo_specialist)
        )
        assert updated.role == UserRole.seo_specialist
