import uuid
from datetime import datetime

from sqlalchemy import DateTime, SmallInteger, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Redirect(Base):
    __tablename__ = "redirects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    old_path: Mapped[str] = mapped_column(String(500), unique=True)
    new_path: Mapped[str] = mapped_column(String(500))
    redirect_type: Mapped[int] = mapped_column(SmallInteger, default=301, server_default=text("301"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
