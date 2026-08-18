import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, or_

from app.core.database import AsyncSessionLocal
from app.models import PasswordResetToken, PostView, RefreshToken

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
