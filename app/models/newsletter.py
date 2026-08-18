import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class NewsletterStatus(str, enum.Enum):
    subscribed = "subscribed"
    unsubscribed = "unsubscribed"
    bounced = "bounced"


class NewsletterSubscriber(Base):
    __tablename__ = "newsletter_subscribers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    status: Mapped[NewsletterStatus] = mapped_column(
        Enum(NewsletterStatus, name="newsletter_status"),
        default=NewsletterStatus.subscribed,
        server_default=text("'subscribed'"),
    )
    source: Mapped[str | None] = mapped_column(String(50))
    subscribed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    unsubscribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
