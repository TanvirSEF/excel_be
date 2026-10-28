import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException
from app.models.course import Course, CourseEnrollment, CourseLesson, LessonProgress

logger = logging.getLogger(__name__)


async def get_user_enrollment(
    db: AsyncSession, user_id: uuid.UUID, course_id: uuid.UUID
) -> CourseEnrollment | None:
    stmt = select(CourseEnrollment).where(
        CourseEnrollment.user_id == user_id, CourseEnrollment.course_id == course_id
    )
    return await db.scalar(stmt)


async def enroll_user(
    db: AsyncSession, user_id: uuid.UUID, course_id: uuid.UUID
) -> CourseEnrollment:
    course = await db.get(Course, course_id)
    if not course:
        raise NotFoundException("Course not found", code="COURSE_NOT_FOUND")

    existing = await get_user_enrollment(db, user_id, course_id)
    if existing:
        return existing

    enrollment = CourseEnrollment(
        user_id=user_id,
        course_id=course_id,
        progress_percent=0,
    )
    db.add(enrollment)
    await db.commit()
    await db.refresh(enrollment)
    return enrollment


async def get_user_course_progress(
    db: AsyncSession, user_id: uuid.UUID, course_id: uuid.UUID
) -> tuple[dict[uuid.UUID, bool], dict[uuid.UUID, int], int]:
    # Returns (completed_map, last_watched_map, progress_percent)
    enrollment = await get_user_enrollment(db, user_id, course_id)
    progress_percent = enrollment.progress_percent if enrollment else 0

    progress_stmt = select(LessonProgress).where(
        LessonProgress.user_id == user_id, LessonProgress.course_id == course_id
    )
    res = await db.execute(progress_stmt)
    records = res.scalars().all()

    completed_map = {r.lesson_id: r.is_completed for r in records}
    last_watched_map = {r.lesson_id: r.last_watched_second for r in records}

    return completed_map, last_watched_map, progress_percent


async def record_lesson_progress(
    db: AsyncSession,
    user_id: uuid.UUID,
    course_id: uuid.UUID,
    lesson_id: uuid.UUID,
    is_completed: bool,
    last_watched_second: int,
) -> LessonProgress:
    # Ensure enrollment exists
    enrollment = await enroll_user(db, user_id, course_id)

    stmt = select(LessonProgress).where(
        LessonProgress.user_id == user_id, LessonProgress.lesson_id == lesson_id
    )
    progress = await db.scalar(stmt)
    if not progress:
        progress = LessonProgress(
            user_id=user_id,
            course_id=course_id,
            lesson_id=lesson_id,
            is_completed=is_completed,
            last_watched_second=last_watched_second,
        )
        db.add(progress)
    else:
        progress.is_completed = is_completed
        progress.last_watched_second = max(progress.last_watched_second, last_watched_second)
        if is_completed:
            progress.completed_at = datetime.now(timezone.utc)

    await db.commit()

    # Recalculate enrollment progress
    total_lessons = (
        await db.scalar(
            select(func.count(CourseLesson.id)).where(CourseLesson.course_id == course_id)
        )
        or 0
    )
    completed_lessons = (
        await db.scalar(
            select(func.count(LessonProgress.id)).where(
                LessonProgress.user_id == user_id,
                LessonProgress.course_id == course_id,
                LessonProgress.is_completed.is_(True),
            )
        )
        or 0
    )

    new_pct = int((completed_lessons / total_lessons) * 100) if total_lessons > 0 else 0
    enrollment.progress_percent = min(100, max(0, new_pct))
    if enrollment.progress_percent == 100 and not enrollment.completed_at:
        enrollment.completed_at = datetime.now(timezone.utc)
    elif enrollment.progress_percent < 100:
        enrollment.completed_at = None

    await db.commit()
    await db.refresh(progress)
    return progress
