import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.exceptions import ConflictException, NotFoundException, ValidationException
from app.models.category import Category
from app.models.course import (
    Course,
    CourseLevel,
    CourseLesson,
    CourseSection,
    CourseStatus,
    LessonAttachment,
)
from app.models.user import User
from app.schemas.course import (
    CourseCreate,
    CourseLessonCreate,
    CourseLessonUpdate,
    CourseSectionCreate,
    CourseSectionUpdate,
    CourseUpdate,
    DirectUploadIntentRequest,
    DirectUploadIntentResponse,
    LessonAttachmentCreate,
    LessonOrderItem,
    SectionOrderItem,
)
from app.services.media_service import get_s3
from app.utils.slugify import slugify

logger = logging.getLogger(__name__)


async def _unique_course_slug(db: AsyncSession, base: str, exclude_id: uuid.UUID | None = None) -> str:
    if not base:
        base = "course"
    candidate = base
    counter = 2
    while True:
        stmt = select(Course.id).where(Course.slug == candidate)
        if exclude_id is not None:
            stmt = stmt.where(Course.id != exclude_id)
        existing = await db.scalar(stmt)
        if not existing:
            return candidate
        candidate = f"{base}-{counter}"
        counter += 1


async def _unique_lesson_slug(
    db: AsyncSession, course_id: uuid.UUID, base: str, exclude_id: uuid.UUID | None = None
) -> str:
    if not base:
        base = "lesson"
    candidate = base
    counter = 2
    while True:
        stmt = select(CourseLesson.id).where(
            CourseLesson.course_id == course_id, CourseLesson.slug == candidate
        )
        if exclude_id is not None:
            stmt = stmt.where(CourseLesson.id != exclude_id)
        existing = await db.scalar(stmt)
        if not existing:
            return candidate
        candidate = f"{base}-{counter}"
        counter += 1


async def recalculate_course_totals(db: AsyncSession, course_id: uuid.UUID) -> None:
    stats = await db.execute(
        select(
            func.count(CourseLesson.id),
            func.coalesce(func.sum(CourseLesson.video_duration_seconds), 0),
        ).where(CourseLesson.course_id == course_id)
    )
    count, total_seconds = stats.one()

    course = await db.get(Course, course_id)
    if course:
        course.total_lessons_count = count or 0
        course.total_duration_seconds = int(total_seconds or 0)
        await db.commit()


# --- Courses ---
async def list_courses(
    db: AsyncSession,
    *,
    search: str | None = None,
    category_id: uuid.UUID | None = None,
    level: CourseLevel | None = None,
    status: CourseStatus | None = None,
    instructor_id: uuid.UUID | None = None,
    page: int = 1,
    page_size: int = 20,
    is_public: bool = False,
) -> tuple[list[Course], int]:
    conditions = []
    if is_public:
        conditions.append(Course.status == CourseStatus.published)
    elif status:
        conditions.append(Course.status == status)

    if category_id:
        conditions.append(Course.category_id == category_id)

    if level:
        conditions.append(Course.level == level)

    if instructor_id:
        conditions.append(Course.instructor_id == instructor_id)

    if search:
        search_fmt = f"%{search.strip().lower()}%"
        conditions.append(
            func.lower(Course.title).like(search_fmt)
            | func.lower(Course.headline).like(search_fmt)
        )

    count_stmt = select(func.count(Course.id)).where(*conditions)
    total = (await db.scalar(count_stmt)) or 0

    query = (
        select(Course)
        .where(*conditions)
        .options(
            selectinload(Course.category),
            selectinload(Course.instructor),
        )
        .order_by(
            Course.published_at.desc().nullslast() if is_public else Course.created_at.desc()
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    )

    result = await db.execute(query)
    courses = list(result.scalars().all())
    return courses, total


async def get_course_by_id(
    db: AsyncSession, course_id: uuid.UUID, load_curriculum: bool = True
) -> Course | None:
    query = select(Course).where(Course.id == course_id).options(
        selectinload(Course.category),
        selectinload(Course.instructor),
    )
    if load_curriculum:
        query = query.options(
            selectinload(Course.sections)
            .selectinload(CourseSection.lessons)
            .selectinload(CourseLesson.attachments)
        )
    return await db.scalar(query)


async def get_course_by_slug(
    db: AsyncSession, slug: str, is_public: bool = False
) -> Course | None:
    conditions = [Course.slug == slug]
    if is_public:
        conditions.append(Course.status == CourseStatus.published)

    query = (
        select(Course)
        .where(*conditions)
        .options(
            selectinload(Course.category),
            selectinload(Course.instructor),
            selectinload(Course.sections)
            .selectinload(CourseSection.lessons)
            .selectinload(CourseLesson.attachments),
        )
    )
    return await db.scalar(query)


async def create_course(db: AsyncSession, user: User, data: CourseCreate) -> Course:
    if data.category_id:
        category = await db.get(Category, data.category_id)
        if not category:
            raise NotFoundException("Category not found", code="CATEGORY_NOT_FOUND")

    slug_candidate = data.slug or slugify(data.title)
    slug = await _unique_course_slug(db, slug_candidate)

    instructor_id = data.instructor_id or user.id

    course = Course(
        title=data.title,
        slug=slug,
        headline=data.headline,
        description=data.description,
        thumbnail_url=data.thumbnail_url,
        promo_video_url=data.promo_video_url,
        level=data.level,
        status=CourseStatus.draft,
        category_id=data.category_id,
        instructor_id=instructor_id,
        is_free=data.is_free,
        price=data.price,
        learning_outcomes=data.learning_outcomes,
        requirements=data.requirements,
    )
    db.add(course)
    await db.commit()
    await db.refresh(course)
    return await get_course_by_id(db, course.id, load_curriculum=True)  # type: ignore


async def update_course(
    db: AsyncSession, user: User, course_id: uuid.UUID, data: CourseUpdate
) -> Course:
    course = await db.get(Course, course_id)
    if not course:
        raise NotFoundException("Course not found", code="COURSE_NOT_FOUND")

    update_dict = data.model_dump(exclude_unset=True)

    if "category_id" in update_dict and update_dict["category_id"]:
        category = await db.get(Category, update_dict["category_id"])
        if not category:
            raise NotFoundException("Category not found", code="CATEGORY_NOT_FOUND")

    if "title" in update_dict and "slug" not in update_dict and not course.slug:
        course.slug = await _unique_course_slug(db, slugify(update_dict["title"]), exclude_id=course.id)
    elif "slug" in update_dict and update_dict["slug"]:
        course.slug = await _unique_course_slug(db, update_dict["slug"], exclude_id=course.id)

    if "status" in update_dict:
        new_status = update_dict["status"]
        if new_status == CourseStatus.published and course.status != CourseStatus.published:
            course.published_at = datetime.now(timezone.utc)
        elif new_status != CourseStatus.published:
            course.published_at = None

    for field, val in update_dict.items():
        if field not in ("slug",):
            setattr(course, field, val)

    await db.commit()
    return await get_course_by_id(db, course.id, load_curriculum=True)  # type: ignore


async def delete_course(db: AsyncSession, course_id: uuid.UUID) -> None:
    course = await db.get(Course, course_id)
    if not course:
        raise NotFoundException("Course not found", code="COURSE_NOT_FOUND")
    await db.delete(course)
    await db.commit()


# --- Sections ---
async def create_section(
    db: AsyncSession, course_id: uuid.UUID, data: CourseSectionCreate
) -> CourseSection:
    course = await db.get(Course, course_id)
    if not course:
        raise NotFoundException("Course not found", code="COURSE_NOT_FOUND")

    max_order = (
        await db.scalar(
            select(func.coalesce(func.max(CourseSection.order_index), -1)).where(
                CourseSection.course_id == course_id
            )
        )
        or -1
    )

    section = CourseSection(
        course_id=course_id,
        title=data.title,
        description=data.description,
        order_index=max_order + 1 if data.order_index == 0 else data.order_index,
    )
    db.add(section)
    await db.commit()
    await db.refresh(section)
    return section


async def update_section(
    db: AsyncSession, section_id: uuid.UUID, data: CourseSectionUpdate
) -> CourseSection:
    section = await db.get(CourseSection, section_id)
    if not section:
        raise NotFoundException("Section not found", code="SECTION_NOT_FOUND")

    update_dict = data.model_dump(exclude_unset=True)
    for k, v in update_dict.items():
        setattr(section, k, v)

    await db.commit()
    await db.refresh(section)
    return section


async def delete_section(db: AsyncSession, section_id: uuid.UUID) -> None:
    section = await db.get(CourseSection, section_id)
    if not section:
        raise NotFoundException("Section not found", code="SECTION_NOT_FOUND")
    course_id = section.course_id
    await db.delete(section)
    await db.commit()
    await recalculate_course_totals(db, course_id)


async def reorder_sections(
    db: AsyncSession, course_id: uuid.UUID, order_data: list[SectionOrderItem]
) -> None:
    for item in order_data:
        section = await db.get(CourseSection, item.id)
        if section and section.course_id == course_id:
            section.order_index = item.order_index
    await db.commit()


# --- Lessons ---
async def create_lesson(
    db: AsyncSession, section_id: uuid.UUID, data: CourseLessonCreate
) -> CourseLesson:
    section = await db.get(CourseSection, section_id)
    if not section:
        raise NotFoundException("Section not found", code="SECTION_NOT_FOUND")

    course_id = section.course_id

    max_order = (
        await db.scalar(
            select(func.coalesce(func.max(CourseLesson.order_index), -1)).where(
                CourseLesson.section_id == section_id
            )
        )
        or -1
    )

    slug_candidate = data.slug or slugify(data.title)
    slug = await _unique_lesson_slug(db, course_id, slug_candidate)

    lesson = CourseLesson(
        section_id=section_id,
        course_id=course_id,
        title=data.title,
        slug=slug,
        lesson_type=data.lesson_type,
        video_url=data.video_url,
        video_duration_seconds=data.video_duration_seconds,
        content_json=data.content_json,
        content_html=data.content_html,
        is_preview=data.is_preview,
        order_index=max_order + 1 if data.order_index == 0 else data.order_index,
    )
    db.add(lesson)
    await db.commit()
    await db.refresh(lesson)
    await recalculate_course_totals(db, course_id)
    return await get_lesson_by_id(db, lesson.id)  # type: ignore


async def get_lesson_by_id(db: AsyncSession, lesson_id: uuid.UUID) -> CourseLesson | None:
    query = (
        select(CourseLesson)
        .where(CourseLesson.id == lesson_id)
        .options(
            selectinload(CourseLesson.attachments),
            selectinload(CourseLesson.section),
        )
    )
    return await db.scalar(query)


async def update_lesson(
    db: AsyncSession, lesson_id: uuid.UUID, data: CourseLessonUpdate
) -> CourseLesson:
    lesson = await db.get(CourseLesson, lesson_id)
    if not lesson:
        raise NotFoundException("Lesson not found", code="LESSON_NOT_FOUND")

    update_dict = data.model_dump(exclude_unset=True)

    if "section_id" in update_dict and update_dict["section_id"]:
        new_section = await db.get(CourseSection, update_dict["section_id"])
        if not new_section or new_section.course_id != lesson.course_id:
            raise ValidationException("Invalid target section", code="INVALID_SECTION")

    if "title" in update_dict and "slug" not in update_dict and not lesson.slug:
        lesson.slug = await _unique_lesson_slug(
            db, lesson.course_id, slugify(update_dict["title"]), exclude_id=lesson.id
        )
    elif "slug" in update_dict and update_dict["slug"]:
        lesson.slug = await _unique_lesson_slug(
            db, lesson.course_id, update_dict["slug"], exclude_id=lesson.id
        )

    for k, v in update_dict.items():
        if k != "slug":
            setattr(lesson, k, v)

    await db.commit()
    await recalculate_course_totals(db, lesson.course_id)
    return await get_lesson_by_id(db, lesson.id)  # type: ignore


async def delete_lesson(db: AsyncSession, lesson_id: uuid.UUID) -> None:
    lesson = await db.get(CourseLesson, lesson_id)
    if not lesson:
        raise NotFoundException("Lesson not found", code="LESSON_NOT_FOUND")
    course_id = lesson.course_id
    await db.delete(lesson)
    await db.commit()
    await recalculate_course_totals(db, course_id)


async def reorder_lessons(
    db: AsyncSession, course_id: uuid.UUID, order_data: list[LessonOrderItem]
) -> None:
    for item in order_data:
        lesson = await db.get(CourseLesson, item.id)
        if lesson and lesson.course_id == course_id:
            lesson.order_index = item.order_index
            if item.section_id:
                lesson.section_id = item.section_id
    await db.commit()


# --- Attachments ---
async def add_attachment(
    db: AsyncSession, lesson_id: uuid.UUID, data: LessonAttachmentCreate
) -> LessonAttachment:
    lesson = await db.get(CourseLesson, lesson_id)
    if not lesson:
        raise NotFoundException("Lesson not found", code="LESSON_NOT_FOUND")

    attachment = LessonAttachment(
        lesson_id=lesson_id,
        file_name=data.file_name,
        file_url=data.file_url,
        file_size_bytes=data.file_size_bytes,
        file_type=data.file_type,
    )
    db.add(attachment)
    await db.commit()
    await db.refresh(attachment)
    return attachment


async def delete_attachment(db: AsyncSession, attachment_id: uuid.UUID) -> None:
    att = await db.get(LessonAttachment, attachment_id)
    if not att:
        raise NotFoundException("Attachment not found", code="ATTACHMENT_NOT_FOUND")
    await db.delete(att)
    await db.commit()


# --- Cloudflare R2 Direct Upload Intent ---
def generate_direct_upload_intent(data: DirectUploadIntentRequest) -> DirectUploadIntentResponse:
    s3_client = get_s3()
    clean_name = slugify(data.file_name.rsplit(".", 1)[0])
    extension = data.file_name.rsplit(".", 1)[-1].lower() if "." in data.file_name else "bin"
    file_uid = uuid.uuid4().hex[:12]

    if data.asset_type == "video":
        folder = "videos"
    elif data.asset_type == "attachment":
        folder = "attachments"
    elif data.asset_type == "thumbnail":
        folder = "thumbnails"
    else:
        folder = "assets"

    key = f"courses/{data.course_id}/{folder}/{file_uid}_{clean_name}.{extension}"

    upload_url = s3_client.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": settings.r2_bucket_name,
            "Key": key,
            "ContentType": data.content_type,
        },
        ExpiresIn=3600,
    )

    public_url = f"{settings.r2_public_url.rstrip('/')}/{key}"

    return DirectUploadIntentResponse(
        upload_url=upload_url,
        public_url=public_url,
        file_key=key,
    )
