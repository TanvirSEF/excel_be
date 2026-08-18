import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models import NewsletterStatus, NewsletterSubscriber

logger = logging.getLogger(__name__)

CONTACTS_ENDPOINT = "https://api.resend.com/contacts"
SYNC_BATCH_SIZE = 100


async def sync_newsletter_subscribers() -> None:
    if not settings.resend_api_key or not settings.resend_segment_id:
        return

    async with AsyncSessionLocal() as session:
        rows = (
            await session.scalars(
                select(NewsletterSubscriber)
                .where(NewsletterSubscriber.synced_at.is_(None))
                .limit(SYNC_BATCH_SIZE)
            )
        ).all()

        synced = 0
        for subscriber in rows:
            try:
                if subscriber.status == NewsletterStatus.subscribed:
                    await _upsert_contact(subscriber.email)
                else:
                    await _mark_unsubscribed(subscriber.email)
                subscriber.synced_at = datetime.now(timezone.utc)
                await session.commit()
                synced += 1
            except Exception:
                await session.rollback()
                logger.exception("Failed to sync subscriber %s", subscriber.email)

        if synced:
            logger.info("Synced %d newsletter subscribers to Resend", synced)


async def _upsert_contact(email: str) -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            CONTACTS_ENDPOINT,
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            json={
                "email": email,
                "unsubscribed": False,
                "segments": [{"id": settings.resend_segment_id}],
            },
        )
        response.raise_for_status()


async def _mark_unsubscribed(email: str) -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.patch(
            f"{CONTACTS_ENDPOINT}/{email}",
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            json={"unsubscribed": True},
        )
        # 404 means the contact was never pushed to Resend, nothing to update
        if response.status_code != 404:
            response.raise_for_status()
