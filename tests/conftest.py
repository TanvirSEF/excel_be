from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal, engine
from app.core.redis_client import close_redis, get_redis
from app.core.security import hash_password
from app.main import app
from app.models import Category, Post, PostStatus, User, UserRole

EDITOR_EMAIL = "editor@test.com"
EDITOR_PASSWORD = "FinalPass789!!"

TEST_USERS = [
    ("Senior Editor", "editor@test.com", "FinalPass789!!", UserRole.senior_editor),
    ("Test Writer", "writer@test.com", "WriterPass123!", UserRole.technical_writer),
    ("Test SEO", "seo@test.com", "SeoPass12345!", UserRole.seo_specialist),
]


@pytest.fixture(scope="session", autouse=True)
async def ensure_test_users() -> None:
    async with AsyncSessionLocal() as db:
        for name, email, password, role in TEST_USERS:
            existing = await db.scalar(select(User).where(User.email == email))
            if existing is None:
                db.add(
                    User(
                        name=name,
                        email=email,
                        password_hash=hash_password(password),
                        role=role,
                        is_active=True,
                        is_verified=True,
                    )
                )
        await db.commit()


@pytest.fixture(autouse=True)
async def flush_rate_keys() -> None:
    await _flush("ratelimit:*")
    yield
    await _flush("ratelimit:*")


async def _flush(pattern: str) -> None:
    r = get_redis()
    cursor = 0
    while True:
        cursor, keys = await r.scan(cursor, match=pattern, count=100)
        if keys:
            await r.delete(*keys)
        if cursor == 0:
            return


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await close_redis()
    await engine.dispose()


@pytest.fixture
async def admin_token(client: httpx.AsyncClient) -> str:
    response = await client.post(
        "/api/v1/auth/login", data={"username": "admin@excelinsider.com", "password": "AdminPass123!"}
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


@pytest.fixture
async def published_post(admin_token: str) -> AsyncIterator[Post]:
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Post).where(Post.slug == "rate-limit-test-post"))
        await db.execute(delete(Category).where(Category.slug == "rate-limit-test"))
        await db.commit()

        category = Category(name="Rate Limit Test", slug="rate-limit-test")
        db.add(category)
        await db.flush()
        author = await db.scalar(select(User).where(User.role == UserRole.super_admin))
        post = Post(
            title="Rate limit test post",
            slug="rate-limit-test-post",
            content_json={"blocks": [{"type": "paragraph", "text": "body"}]},
            author_id=author.id,
            category_id=category.id,
            status=PostStatus.published,
        )
        db.add(post)
        await db.commit()
        await db.refresh(post)

    yield post

    async with AsyncSessionLocal() as db:
        await db.execute(delete(Post).where(Post.slug == "rate-limit-test-post"))
        await db.execute(delete(Category).where(Category.slug == "rate-limit-test"))
        await db.commit()
