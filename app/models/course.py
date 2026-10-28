import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.category import Category
from app.models.user import User


class CourseLevel(str, enum.Enum):
    all_levels = "all_levels"
    beginner = "beginner"
    intermediate = "intermediate"
    advanced = "advanced"


class CourseStatus(str, enum.Enum):
    draft = "draft"
    published = "published"
    archived = "archived"


class LessonType(str, enum.Enum):
    video = "video"
    article = "article"
    exercise = "exercise"
    quiz = "quiz"


class Course(Base):
    __tablename__ = "courses"
    __table_args__ = (
        Index("ix_courses_status_published_at", "status", "published_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    headline: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    thumbnail_url: Mapped[str | None] = mapped_column(String(500))
    promo_video_url: Mapped[str | None] = mapped_column(String(500))
    level: Mapped[CourseLevel] = mapped_column(
        Enum(CourseLevel, name="course_level"), default=CourseLevel.all_levels, server_default=text("'all_levels'")
    )
    status: Mapped[CourseStatus] = mapped_column(
        Enum(CourseStatus, name="course_status"), default=CourseStatus.draft, server_default=text("'draft'")
    )
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id", ondelete="SET NULL"), nullable=True, index=True
    )
    category: Mapped[Category | None] = relationship(lazy="selectin")
    instructor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    instructor: Mapped[User] = relationship(lazy="selectin")
    is_free: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    price: Mapped[float] = mapped_column(Numeric(10, 2), default=0.00, server_default=text("0.00"))
    learning_outcomes: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    requirements: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    total_duration_seconds: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    total_lessons_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    sections: Mapped[list["CourseSection"]] = relationship(
        "CourseSection",
        back_populates="course",
        order_by="CourseSection.order_index",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class CourseSection(Base):
    __tablename__ = "course_sections"
    __table_args__ = (
        Index("ix_course_sections_course_order", "course_id", "order_index"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    course: Mapped["Course"] = relationship("Course", back_populates="sections")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    order_index: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    lessons: Mapped[list["CourseLesson"]] = relationship(
        "CourseLesson",
        back_populates="section",
        order_by="CourseLesson.order_index",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class CourseLesson(Base):
    __tablename__ = "course_lessons"
    __table_args__ = (
        Index("ix_course_lessons_section_order", "section_id", "order_index"),
        Index("ix_course_lessons_course_id", "course_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    section_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("course_sections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    section: Mapped["CourseSection"] = relationship("CourseSection", back_populates="lessons")
    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    lesson_type: Mapped[LessonType] = mapped_column(
        Enum(LessonType, name="course_lesson_type"), default=LessonType.video, server_default=text("'video'")
    )
    video_url: Mapped[str | None] = mapped_column(String(500))
    video_duration_seconds: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    content_json: Mapped[dict | None] = mapped_column(JSONB)
    content_html: Mapped[str | None] = mapped_column(Text)
    is_preview: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    order_index: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    attachments: Mapped[list["LessonAttachment"]] = relationship(
        "LessonAttachment",
        back_populates="lesson",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class LessonAttachment(Base):
    __tablename__ = "lesson_attachments"
    __table_args__ = (Index("ix_lesson_attachments_lesson_id", "lesson_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lesson_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("course_lessons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lesson: Mapped["CourseLesson"] = relationship("CourseLesson", back_populates="attachments")
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_url: Mapped[str] = mapped_column(String(500), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    file_type: Mapped[str] = mapped_column(String(100), default="application/octet-stream")
    download_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CourseEnrollment(Base):
    __tablename__ = "course_enrollments"
    __table_args__ = (
        UniqueConstraint("user_id", "course_id", name="uq_course_enrollments_user_course"),
        Index("ix_course_enrollments_user_id", "user_id"),
        Index("ix_course_enrollments_course_id", "course_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    progress_percent: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LessonProgress(Base):
    __tablename__ = "lesson_progress"
    __table_args__ = (
        UniqueConstraint("user_id", "lesson_id", name="uq_lesson_progress_user_lesson"),
        Index("ix_lesson_progress_user_course", "user_id", "course_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    lesson_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("course_lessons.id", ondelete="CASCADE"), nullable=False
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    is_completed: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    last_watched_second: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
