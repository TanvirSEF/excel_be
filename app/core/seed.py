import asyncio
import logging

from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.security import hash_password
from app.models import User, UserRole

logger = logging.getLogger(__name__)


async def seed_super_admin() -> None:
    email = settings.first_admin_email.strip().lower()
    password = settings.first_admin_password

    if not email or not password:
        logger.warning("FIRST_ADMIN_EMAIL or FIRST_ADMIN_PASSWORD not set, skipping seed")
        return

    async with AsyncSessionLocal() as session:
        existing = await session.scalar(select(User).where(User.email == email))

        if existing is not None:
            if existing.role == UserRole.super_admin:
                logger.info("Super admin %s already exists, nothing to do", email)
            else:
                logger.warning("User %s exists with role %s, skipping seed", email, existing.role.value)
            return

        any_admin = await session.scalar(
            select(User).where(User.role == UserRole.super_admin).limit(1)
        )
        if any_admin is not None:
            logger.info("Another super admin already exists, skipping seed")
            return

        session.add(
            User(
                name="Super Admin",
                email=email,
                password_hash=hash_password(password),
                role=UserRole.super_admin,
                is_active=True,
                is_verified=True,
            )
        )
        await session.commit()
        logger.info("Created super admin %s", email)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(seed_super_admin())
