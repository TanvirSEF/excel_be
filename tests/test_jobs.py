from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal
from app.jobs.scheduled_publisher import publish_scheduled_posts
from app.models import AuditLog, Post, PostStatus, User, UserRole


@pytest.fixture(autouse=True)
async def jobs_test_cleanup():
    await _cleanup()
    yield
    await _cleanup()


async def _cleanup():
    async with AsyncSessionLocal() as db:
        posts = (await db.scalars(select(Post).where(Post.slug.like("jobs-test-%")))).all()
        if posts:
            ids = [post.id for post in posts]
            await db.execute(delete(AuditLog).where(AuditLog.entity_id.in_(ids)))
            await db.execute(delete(Post).where(Post.id.in_(ids)))
        await db.commit()


async def test_scheduled_publish_is_audited():
    async with AsyncSessionLocal() as db:
        author = await db.scalar(select(User).where(User.role == UserRole.super_admin))
        post = Post(
            title="Jobs test post",
            slug=f"jobs-test-{uuid4().hex[:8]}",
            content_json={"blocks": [{"type": "paragraph", "text": "body"}]},
            author_id=author.id,
            status=PostStatus.scheduled,
            scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        db.add(post)
        await db.commit()
        post_id, slug = post.id, post.slug

    await publish_scheduled_posts()

    async with AsyncSessionLocal() as db:
        fresh = await db.scalar(select(Post).where(Post.id == post_id))
        assert fresh.status == PostStatus.published
        assert fresh.published_at is not None

        log = await db.scalar(
            select(AuditLog).where(
                AuditLog.action == "post.publish", AuditLog.entity_id == post_id
            )
        )
    assert log is not None
    assert log.user_id is None
    assert log.meta == {"slug": slug, "source": "scheduler"}


async def test_scheduled_publisher_ignores_future_posts():
    async with AsyncSessionLocal() as db:
        author = await db.scalar(select(User).where(User.role == UserRole.super_admin))
        post = Post(
            title="Jobs future post",
            slug=f"jobs-test-future-{uuid4().hex[:8]}",
            content_json={"blocks": [{"type": "paragraph", "text": "body"}]},
            author_id=author.id,
            status=PostStatus.scheduled,
            scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        db.add(post)
        await db.commit()
        post_id = post.id

    await publish_scheduled_posts()

    async with AsyncSessionLocal() as db:
        fresh = await db.scalar(select(Post).where(Post.id == post_id))
    assert fresh.status == PostStatus.scheduled
