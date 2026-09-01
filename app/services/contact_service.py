from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ContactMessage
from app.schemas.contact import ContactMessageCreate


async def create_message(db: AsyncSession, data: ContactMessageCreate) -> ContactMessage:
    message = ContactMessage(
        email=data.email,
        subject=data.subject,
        service=data.service,
        message=data.message,
    )
    db.add(message)
    await db.commit()
    await db.refresh(message)
    return message
