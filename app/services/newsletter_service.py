import logging
from datetime import datetime, timezone

import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import UnauthorizedException
from app.core.security import (
    NEWSLETTER_UNSUB,
    create_unsubscribe_token,
    decode_token,
)
from app.models import NewsletterStatus, NewsletterSubscriber
from app.services.email_service import send_email, welcome_email_template

logger = logging.getLogger(__name__)


async def subscribe(db: AsyncSession, email: str, source: str | None) -> None:
    normalized = email.lower()
    subscriber = await db.scalar(
        select(NewsletterSubscriber).where(NewsletterSubscriber.email == normalized)
    )
    if subscriber is not None and subscriber.status == NewsletterStatus.subscribed:
        return

    if subscriber is None:
        subscriber = NewsletterSubscriber(email=normalized, source=source)
        db.add(subscriber)
    else:
        subscriber.status = NewsletterStatus.subscribed
        subscriber.subscribed_at = datetime.now(timezone.utc)
        subscriber.unsubscribed_at = None
    subscriber.synced_at = None
    await db.commit()

    token = create_unsubscribe_token(normalized)
    link = f"{settings.frontend_url}/newsletter/unsubscribe?token={token}"
    try:
        await send_email(normalized, "Welcome to Excel Insider", welcome_email_template(link))
    except Exception:
        logger.warning("Welcome email failed for %s", normalized)


async def unsubscribe(db: AsyncSession, token: str) -> None:
    try:
        payload = decode_token(token)
    except jwt.PyJWTError:
        raise UnauthorizedException("Invalid unsubscribe link", code="INVALID_TOKEN")

    email = payload.get("sub")
    if payload.get("type") != NEWSLETTER_UNSUB or not email:
        raise UnauthorizedException("Invalid unsubscribe link", code="INVALID_TOKEN")

    subscriber = await db.scalar(
        select(NewsletterSubscriber).where(NewsletterSubscriber.email == email)
    )
    if subscriber is None or subscriber.status != NewsletterStatus.subscribed:
        return

    subscriber.status = NewsletterStatus.unsubscribed
    subscriber.unsubscribed_at = datetime.now(timezone.utc)
    subscriber.synced_at = None
    await db.commit()
