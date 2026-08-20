import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal
from app.models import RefreshToken, User


@pytest.fixture(autouse=True)
async def auth_test_cleanup():
    await _cleanup()
    yield
    await _cleanup()


async def _cleanup():
    async with AsyncSessionLocal() as db:
        users = (await db.scalars(select(User).where(User.email.like("auth-test-%")))).all()
        if users:
            ids = [u.id for u in users]
            await db.execute(delete(RefreshToken).where(RefreshToken.user_id.in_(ids)))
            await db.execute(delete(User).where(User.id.in_(ids)))
            await db.commit()


async def register_user(client, token, role="seo_specialist"):
    marker = uuid4().hex[:8]
    response = await client.post(
        "/api/v1/auth/register",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "Auth Tester",
            "email": f"auth-test-{marker}@example.com",
            "password": "AuthPass123!",
            "role": role,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def login(client, email, password):
    return await client.post("/api/v1/auth/login", data={"username": email, "password": password})


async def test_register_requires_super_admin(client, admin_token):
    editor = await login(client, "editor@test.com", "FinalPass789!!")
    response = await client.post(
        "/api/v1/auth/register",
        headers={"Authorization": f"Bearer {editor.json()['access_token']}"},
        json={"name": "X", "email": "auth-test-x@example.com", "password": "AuthPass123!", "role": "seo_specialist"},
    )
    assert response.status_code == 403


async def test_register_login_flow(client, admin_token):
    created = await register_user(client, admin_token)
    assert created["role"] == "seo_specialist"

    response = await login(client, created["email"], "AuthPass123!")
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"] and body["refresh_token"]

    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["email"] == created["email"]


async def test_login_wrong_password(client, admin_token):
    created = await register_user(client, admin_token)
    response = await login(client, created["email"], "WrongPass999!")
    assert response.status_code == 401


async def test_refresh_rotation_revokes_old_token(client, admin_token):
    created = await register_user(client, admin_token)
    first = await login(client, created["email"], "AuthPass123!")
    refresh = first.json()["refresh_token"]

    rotated = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert rotated.status_code == 200
    new_refresh = rotated.json()["refresh_token"]
    assert new_refresh != refresh

    replay = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert replay.status_code == 401

    again = await client.post("/api/v1/auth/refresh", json={"refresh_token": new_refresh})
    assert again.status_code == 200


async def test_concurrent_refresh_rotation_single_use(client, admin_token):
    created = await register_user(client, admin_token)
    session = await login(client, created["email"], "AuthPass123!")
    refresh = session.json()["refresh_token"]

    responses = await asyncio.gather(
        client.post("/api/v1/auth/refresh", json={"refresh_token": refresh}),
        client.post("/api/v1/auth/refresh", json={"refresh_token": refresh}),
    )

    statuses = sorted(response.status_code for response in responses)
    assert statuses == [200, 401]


async def test_logout_revokes_refresh_token(client, admin_token):
    created = await register_user(client, admin_token)
    session = await login(client, created["email"], "AuthPass123!")
    refresh = session.json()["refresh_token"]

    response = await client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {session.json()['access_token']}"},
        json={"refresh_token": refresh},
    )
    assert response.status_code == 200

    replay = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert replay.status_code == 401


async def test_me_requires_token(client):
    response = await client.get("/api/v1/auth/me")
    assert response.status_code in (401, 403)


async def test_forgot_password_is_rate_limited(client):
    responses = [
        await client.post("/api/v1/auth/forgot-password", json={"email": "nobody@example.com"})
        for _ in range(6)
    ]
    assert [response.status_code for response in responses[:5]] == [200] * 5
    assert responses[5].status_code == 429
    assert responses[5].json()["error"]["code"] == "RATE_LIMITED"


async def test_reset_password_is_rate_limited(client):
    statuses = []
    for _ in range(11):
        response = await client.post(
            "/api/v1/auth/reset-password",
            json={"token": "not-a-real-token", "new_password": "NewPass123!"},
        )
        statuses.append(response.status_code)
    assert statuses[:10] == [400] * 10
    assert statuses[10] == 429
