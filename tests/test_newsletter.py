from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal
from app.core.security import NEWSLETTER_UNSUB, create_access_token, create_unsubscribe_token, decode_token
from app.models import NewsletterStatus, NewsletterSubscriber
from app.services import newsletter_service


@pytest.fixture(autouse=True)
async def newsletter_test_cleanup():
    await _cleanup()
    yield
    await _cleanup()


async def _cleanup():
    async with AsyncSessionLocal() as db:
        await db.execute(delete(NewsletterSubscriber).where(NewsletterSubscriber.email.like("nl-test-%")))
        await db.commit()


@pytest.fixture(autouse=True)
def no_welcome_email(monkeypatch):
    async def fake_send(to, subject, html):
        return None

    monkeypatch.setattr(newsletter_service, "send_email", fake_send)


async def test_subscribe_creates_pending_sync_row(client):
    email = f"nl-test-{uuid4().hex[:8]}@example.com"
    response = await client.post("/api/v1/newsletter/subscribe", json={"email": email, "source": "footer"})
    assert response.status_code == 201
    assert response.json()["message"] == "Subscribed"

    async with AsyncSessionLocal() as db:
        row = await db.scalar(select(NewsletterSubscriber).where(NewsletterSubscriber.email == email))
    assert row is not None
    assert row.status == NewsletterStatus.subscribed
    assert row.source == "footer"
    assert row.synced_at is None


async def test_subscribe_is_idempotent(client):
    email = f"nl-test-{uuid4().hex[:8]}@example.com"
    first = await client.post("/api/v1/newsletter/subscribe", json={"email": email})
    second = await client.post("/api/v1/newsletter/subscribe", json={"email": email})
    assert first.status_code == 201 and second.status_code == 201
    assert second.json() == first.json()

    async with AsyncSessionLocal() as db:
        count = len(
            (await db.scalars(select(NewsletterSubscriber).where(NewsletterSubscriber.email == email))).all()
        )
    assert count == 1


async def test_unsubscribe_lifecycle(client):
    email = f"nl-test-{uuid4().hex[:8]}@example.com"
    await client.post("/api/v1/newsletter/subscribe", json={"email": email})

    token = create_unsubscribe_token(email)
    response = await client.post("/api/v1/newsletter/unsubscribe", json={"token": token})
    assert response.status_code == 200

    async with AsyncSessionLocal() as db:
        row = await db.scalar(select(NewsletterSubscriber).where(NewsletterSubscriber.email == email))
    assert row.status == NewsletterStatus.unsubscribed
    assert row.unsubscribed_at is not None
    assert row.synced_at is None

    again = await client.post("/api/v1/newsletter/unsubscribe", json={"token": token})
    assert again.status_code == 200


async def test_resubscribe_reactivates(client):
    email = f"nl-test-{uuid4().hex[:8]}@example.com"
    await client.post("/api/v1/newsletter/subscribe", json={"email": email})
    token = create_unsubscribe_token(email)
    await client.post("/api/v1/newsletter/unsubscribe", json={"token": token})

    response = await client.post("/api/v1/newsletter/subscribe", json={"email": email})
    assert response.status_code == 201

    async with AsyncSessionLocal() as db:
        row = await db.scalar(select(NewsletterSubscriber).where(NewsletterSubscriber.email == email))
    assert row.status == NewsletterStatus.subscribed
    assert row.unsubscribed_at is None


async def test_unsubscribe_rejects_bad_tokens(client):
    garbage = await client.post("/api/v1/newsletter/unsubscribe", json={"token": "not.a.jwt"})
    assert garbage.status_code == 401

    access = create_access_token("someone", "super_admin")
    wrong_type = await client.post("/api/v1/newsletter/unsubscribe", json={"token": access})
    assert wrong_type.status_code == 401


async def test_unsubscribe_token_payload_shape():
    email = f"nl-test-{uuid4().hex[:8]}@example.com"
    payload = decode_token(create_unsubscribe_token(email))
    assert payload["sub"] == email
    assert payload["type"] == NEWSLETTER_UNSUB
