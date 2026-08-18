import pytest

from app.deps.rate_limit import parse_rate


def test_parse_rate():
    assert parse_rate("5/15minutes") == (5, 900)
    assert parse_rate("3/10minutes") == (3, 600)
    assert parse_rate("5/1hour") == (5, 3600)
    assert parse_rate("10/30seconds") == (10, 30)
    with pytest.raises(ValueError):
        parse_rate("5/15parsecs")
    with pytest.raises(ValueError):
        parse_rate("five/15minutes")


async def test_login_rate_limited_after_five_attempts(client):
    codes = []
    for _ in range(6):
        response = await client.post(
            "/api/v1/auth/login", data={"username": "admin@excelinsider.com", "password": "wrong"}
        )
        codes.append(response.status_code)

    assert codes[:5] == [401] * 5
    assert codes[5] == 429


async def test_login_rate_limit_response_shape(client):
    for _ in range(5):
        await client.post("/api/v1/auth/login", data={"username": "admin@excelinsider.com", "password": "wrong"})
    response = await client.post(
        "/api/v1/auth/login", data={"username": "admin@excelinsider.com", "password": "wrong"}
    )
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "RATE_LIMITED"
    assert int(response.headers["Retry-After"]) >= 1


async def test_login_works_after_flush(client):
    for _ in range(5):
        await client.post("/api/v1/auth/login", data={"username": "admin@excelinsider.com", "password": "wrong"})
    blocked = await client.post(
        "/api/v1/auth/login", data={"username": "admin@excelinsider.com", "password": "AdminPass123!"}
    )
    assert blocked.status_code == 429

    from app.core.redis_client import get_redis
    r = get_redis()
    cursor, keys = await r.scan(0, match="ratelimit:login:*", count=100)
    await r.delete(*keys)

    ok = await client.post(
        "/api/v1/auth/login", data={"username": "admin@excelinsider.com", "password": "AdminPass123!"}
    )
    assert ok.status_code == 200
    assert "access_token" in ok.json()


async def test_comment_rate_limited_after_three(client, published_post):
    payload = {
        "user_name": "Tester",
        "user_email": "tester@example.com",
        "comment_text": "nice post",
    }
    codes = []
    for _ in range(4):
        response = await client.post(f"/api/v1/posts/{published_post.id}/comments", json=payload)
        codes.append(response.status_code)

    assert codes[:3] == [201] * 3
    assert codes[3] == 429


async def test_newsletter_rate_limited_after_five(client):
    codes = []
    for i in range(6):
        response = await client.post(
            "/api/v1/newsletter/subscribe", json={"email": f"rl-test-{i}@example.com"}
        )
        codes.append(response.status_code)

    assert codes[:5] == [201] * 5
    assert codes[5] == 429


async def test_rate_limit_skipped_when_redis_down(client, monkeypatch):
    import app.deps.rate_limit as rate_limit_module

    def broken_get_redis():
        raise ConnectionError("redis down")

    monkeypatch.setattr(rate_limit_module, "get_redis", broken_get_redis)

    response = await client.post(
        "/api/v1/auth/login", data={"username": "admin@excelinsider.com", "password": "AdminPass123!"}
    )
    assert response.status_code == 200
    assert "access_token" in response.json()
