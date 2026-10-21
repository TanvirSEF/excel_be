import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, SmallInteger, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AnalyticsSession(Base):
    __tablename__ = "analytics_sessions"
    __table_args__ = (
        Index("ix_analytics_sessions_started_at", "started_at"),
        Index("ix_analytics_sessions_visitor_id", "visitor_id"),
        Index("ix_analytics_sessions_class_started_at", "traffic_class", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    visitor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    engaged_ms: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    page_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    landing_path: Mapped[str] = mapped_column(String(300))
    source: Mapped[str] = mapped_column(String(40))
    medium: Mapped[str] = mapped_column(String(40))
    referrer_host: Mapped[str | None] = mapped_column(String(255))
    utm_campaign: Mapped[str | None] = mapped_column(String(100))
    device: Mapped[str] = mapped_column(String(16))
    browser: Mapped[str] = mapped_column(String(32))
    os: Mapped[str] = mapped_column(String(32))
    country: Mapped[str | None] = mapped_column(String(2))
    city: Mapped[str | None] = mapped_column(String(100))
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    is_returning: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    interacted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    bot_score: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    is_bot: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    ua_bot: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_internal: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    traffic_class: Mapped[str] = mapped_column(String(16), default="unknown", server_default="unknown")
    class_reason: Mapped[str | None] = mapped_column(String(120))
    class_confidence: Mapped[str | None] = mapped_column(String(8))
    exit_path: Mapped[str | None] = mapped_column(String(300))


class AnalyticsPageview(Base):
    __tablename__ = "analytics_pageviews"
    __table_args__ = (
        Index("ix_analytics_pageviews_session_id", "session_id"),
        Index("ix_analytics_pageviews_path_occurred_at", "path", "occurred_at"),
        Index("ix_analytics_pageviews_occurred_at", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analytics_sessions.id", ondelete="CASCADE")
    )
    path: Mapped[str] = mapped_column(String(300))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    engaged_ms: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_scroll: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")


class AnalyticsEvent(Base):
    __tablename__ = "analytics_events"
    __table_args__ = (
        Index("ix_analytics_events_session_id", "session_id"),
        Index("ix_analytics_events_name_occurred_at", "name", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analytics_sessions.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(32))
    path: Mapped[str] = mapped_column(String(300))
    target: Mapped[str | None] = mapped_column(String(255))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
