import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, or_

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models import AnalyticsEvent, AnalyticsSession, PasswordResetToken, PostView, RefreshToken

logger = logging.getLogger(__name__)

REVOKED_RETENTION_DAYS = 7
RAW_VIEWS_RETENTION_DAYS = 90


async def cleanup_expired_tokens() -> None:
    now = datetime.now(timezone.utc)
    retention_cutoff = now - timedelta(days=REVOKED_RETENTION_DAYS)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            delete(RefreshToken).where(
                or_(
                    RefreshToken.expires_at < now,
                    and_(RefreshToken.revoked.is_(True), RefreshToken.created_at < retention_cutoff),
                )
            )
        )
        removed_refresh = result.rowcount
        result = await session.execute(
            delete(PasswordResetToken).where(
                or_(
                    PasswordResetToken.expires_at < now,
                    and_(PasswordResetToken.used.is_(True), PasswordResetToken.created_at < retention_cutoff),
                )
            )
        )
        removed_reset = result.rowcount
        await session.commit()

    if removed_refresh or removed_reset:
        logger.info("Cleaned %d refresh and %d reset tokens", removed_refresh, removed_reset)


async def prune_old_post_views() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=RAW_VIEWS_RETENTION_DAYS)

    async with AsyncSessionLocal() as session:
        result = await session.execute(delete(PostView).where(PostView.viewed_at < cutoff))
        await session.commit()

    if result.rowcount:
        logger.info("Pruned %d post views older than %d days", result.rowcount, RAW_VIEWS_RETENTION_DAYS)


async def prune_old_analytics() -> None:
    now = datetime.now(timezone.utc)
    session_cutoff = now - timedelta(days=settings.analytics_retention_days)
    event_cutoff = now - timedelta(days=settings.analytics_event_retention_days)

    async with AsyncSessionLocal() as session:
        events = await session.execute(delete(AnalyticsEvent).where(AnalyticsEvent.occurred_at < event_cutoff))
        sessions = await session.execute(
            delete(AnalyticsSession).where(AnalyticsSession.started_at < session_cutoff)
        )
        await session.commit()

    if events.rowcount or sessions.rowcount:
        logger.info(
            "Pruned %d analytics events and %d sessions past their retention window",
            events.rowcount,
            sessions.rowcount,
        )
