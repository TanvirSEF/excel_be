import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AssistantQuery(Base):
    __tablename__ = "assistant_queries"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    matched_articles_count: Mapped[int] = mapped_column(Integer, default=0)
    matched_calculators_count: Mapped[int] = mapped_column(Integer, default=0)
    user_feedback: Mapped[str | None] = mapped_column(
        String(16), nullable=True, index=True
    )
    client_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, index=True
    )
