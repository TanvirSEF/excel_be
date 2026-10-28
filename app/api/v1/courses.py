import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import NotFoundException, PermissionDeniedException, UnauthorizedException
from app.deps.auth_deps import get_current_user, get_optional_current_user, require_permission
from app.deps.pagination import PaginationParams
from app.models.course import CourseLevel, CourseStatus
from app.models.user import User
from app.schemas.common import Page
from app.schemas.course import (
    ClassroomLessonOut,
    CourseCreate,
    CourseDetailOut,
    CourseListItemOut,
    CourseLessonCreate,
    CourseLessonOut,
    CourseLessonUpdate,
    CoursePublicDetailOut,
    CourseSectionCreate,
    CourseSectionOut,
    CourseSectionPublicOut,
    CourseSectionUpdate,
    CourseUpdate,
    DirectUploadIntentRequest,
    DirectUploadIntentResponse,
    EnrollmentOut,
    LessonAttachmentCreate,
    LessonAttachmentOut,
    LessonOrderItem,
    LessonPublicListItemOut,
    ProgressUpdateInput,
    ReorderLessonsInput,
    ReorderSectionsInput,
    SectionOrderItem,
)
from app.services import course_service, progress_service, role_service

router = APIRouter(prefix="/courses", tags=["courses"])


# ==========================================
# PUBLIC LMS ENDPOINTS
# ==========================================


@router.get("", response_model=Page[CourseListItemOut])
async def list_public_courses(
    search: str | None = None,
    category_id: uuid.UUID | None = None,
    level: CourseLevel | None = None,
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(get_db),
) -> Page[CourseListItemOut]:
    courses, total = await course_service.list_courses(
        db,
        search=search,
        category_id=category_id,
        level=level,
        page=pagination.page,
        page_size=pagination.page_size,
        is_public=True,
    )
    total_pages = (total + pagination.page_size - 1) // pagination.page_size if total > 0 else 1
    return Page(
        items=courses,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        total_pages=total_pages,
    )


@router.get("/{slug}", response_model=CoursePublicDetailOut)
async def get_public_course(
    slug: str,
    user: User | None = Depends(get_optional_current_user),
    db: AsyncSession = Depends(get_db),
) -> CoursePublicDetailOut:
    course = await course_service.get_course_by_slug(db, slug, is_public=True)
    if not course:
        raise NotFoundException("Course not found", code="COURSE_NOT_FOUND")

    is_enrolled = False
    progress_percent = 0
    completed_map: dict[uuid.UUID, bool] = {}

    if user:
        completed_map, _, progress_percent = await progress_service.get_user_course_progress(
            db, user.id, course.id
        )
        enrollment = await progress_service.get_user_enrollment(db, user.id, course.id)
        is_enrolled = enrollment is not None

    # Construct public section syllabus
    public_sections = []
    for sec in sorted(course.sections, key=lambda s: s.order_index):
        public_lessons = []
        for les in sorted(sec.lessons, key=lambda l: l.order_index):
            public_lessons.append(
                LessonPublicListItemOut(
                    id=les.id,
                    section_id=les.section_id,
                    course_id=les.course_id,
                    title=les.title,
                    slug=les.slug,
                    lesson_type=les.lesson_type,
                    video_duration_seconds=les.video_duration_seconds,
                    is_preview=les.is_preview,
                    order_index=les.order_index,
                    is_completed=completed_map.get(les.id, False),
                )
            )
        public_sections.append(
            CourseSectionPublicOut(
                id=sec.id,
                course_id=sec.course_id,
                title=sec.title,
                description=sec.description,
                order_index=sec.order_index,
                lessons=public_lessons,
            )
        )

    return CoursePublicDetailOut(
        id=course.id,
        title=course.title,
        slug=course.slug,
        headline=course.headline,
        description=course.description,
        thumbnail_url=course.thumbnail_url,
        promo_video_url=course.promo_video_url,
        level=course.level,
        status=course.status,
        is_free=course.is_free,
        price=float(course.price),
        total_duration_seconds=course.total_duration_seconds,
        total_lessons_count=course.total_lessons_count,
        published_at=course.published_at,
        created_at=course.created_at,
        updated_at=course.updated_at,
        category=course.category,
        instructor=course.instructor,
        learning_outcomes=course.learning_outcomes or [],
        requirements=course.requirements or [],
        sections=public_sections,
        is_enrolled=is_enrolled,
        progress_percent=progress_percent,
    )


@router.post("/{slug}/enroll", response_model=EnrollmentOut, status_code=status.HTTP_201_CREATED)
async def enroll_course(
    slug: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EnrollmentOut:
    course = await course_service.get_course_by_slug(db, slug, is_public=True)
    if not course:
        raise NotFoundException("Course not found", code="COURSE_NOT_FOUND")

    enrollment = await progress_service.enroll_user(db, user.id, course.id)
    return enrollment


@router.get("/{slug}/lessons/{lesson_id}", response_model=ClassroomLessonOut)
async def get_classroom_lesson(
    slug: str,
    lesson_id: uuid.UUID,
    user: User | None = Depends(get_optional_current_user),
    db: AsyncSession = Depends(get_db),
) -> ClassroomLessonOut:
    course = await course_service.get_course_by_slug(db, slug, is_public=False)
    if not course:
        raise NotFoundException("Course not found", code="COURSE_NOT_FOUND")

    # Locate lesson
    target_lesson = None
    all_lessons: list = []
    for sec in sorted(course.sections, key=lambda s: s.order_index):
        for les in sorted(sec.lessons, key=lambda l: l.order_index):
            all_lessons.append(les)
            if les.id == lesson_id:
                target_lesson = les

    if not target_lesson:
        raise NotFoundException("Lesson not found", code="LESSON_NOT_FOUND")

    # Access check:
    is_editor = False
    if user:
        is_editor = await role_service.has_permission(db, user, "courses:manage")

    enrollment = None
    if user:
        enrollment = await progress_service.get_user_enrollment(db, user.id, course.id)
        if not enrollment and course.is_free:
            enrollment = await progress_service.enroll_user(db, user.id, course.id)

    if not is_editor and not target_lesson.is_preview:
        if not user:
            raise UnauthorizedException("Sign in required to access this lesson")
        if not enrollment:
            raise PermissionDeniedException("Enrollment required to access this lesson")

    # Determine prev and next lesson
    idx = all_lessons.index(target_lesson)
    prev_lesson_id = all_lessons[idx - 1].id if idx > 0 else None
    next_lesson_id = all_lessons[idx + 1].id if idx < len(all_lessons) - 1 else None

    is_completed = False
    last_watched_second = 0
    if user:
        completed_map, watched_map, _ = await progress_service.get_user_course_progress(
            db, user.id, course.id
        )
        is_completed = completed_map.get(target_lesson.id, False)
        last_watched_second = watched_map.get(target_lesson.id, 0)

    return ClassroomLessonOut(
        id=target_lesson.id,
        section_id=target_lesson.section_id,
        course_id=target_lesson.course_id,
        title=target_lesson.title,
        slug=target_lesson.slug,
        lesson_type=target_lesson.lesson_type,
        video_url=target_lesson.video_url,
        video_duration_seconds=target_lesson.video_duration_seconds,
        content_json=target_lesson.content_json,
        content_html=target_lesson.content_html,
        is_preview=target_lesson.is_preview,
        order_index=target_lesson.order_index,
        is_completed=is_completed,
        last_watched_second=last_watched_second,
        attachments=target_lesson.attachments,
        prev_lesson_id=prev_lesson_id,
        next_lesson_id=next_lesson_id,
    )


@router.post("/{slug}/lessons/{lesson_id}/progress", response_model=ClassroomLessonOut)
async def update_lesson_progress(
    slug: str,
    lesson_id: uuid.UUID,
    data: ProgressUpdateInput,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ClassroomLessonOut:
    course = await course_service.get_course_by_slug(db, slug, is_public=False)
    if not course:
        raise NotFoundException("Course not found", code="COURSE_NOT_FOUND")

    lesson = await course_service.get_lesson_by_id(db, lesson_id)
    if not lesson or lesson.course_id != course.id:
        raise NotFoundException("Lesson not found", code="LESSON_NOT_FOUND")

    await progress_service.record_lesson_progress(
        db,
        user.id,
        course.id,
        lesson_id,
        is_completed=data.is_completed,
        last_watched_second=data.last_watched_second,
    )

    return await get_classroom_lesson(slug, lesson_id, user=user, db=db)


# ==========================================
# ADMIN CMS / STUDIO ENDPOINTS
# ==========================================


@router.get("/admin", response_model=Page[CourseListItemOut])
async def list_admin_courses(
    search: str | None = None,
    category_id: uuid.UUID | None = None,
    level: CourseLevel | None = None,
    status: CourseStatus | None = None,
    pagination: PaginationParams = Depends(),
    user: User = Depends(require_permission("courses:view")),
    db: AsyncSession = Depends(get_db),
) -> Page[CourseListItemOut]:
    courses, total = await course_service.list_courses(
        db,
        search=search,
        category_id=category_id,
        level=level,
        status=status,
        page=pagination.page,
        page_size=pagination.page_size,
        is_public=False,
    )
    total_pages = (total + pagination.page_size - 1) // pagination.page_size if total > 0 else 1
    return Page(
        items=courses,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        total_pages=total_pages,
    )


@router.post("/admin", response_model=CourseDetailOut, status_code=status.HTTP_201_CREATED)
async def create_course(
    data: CourseCreate,
    user: User = Depends(require_permission("courses:manage")),
    db: AsyncSession = Depends(get_db),
) -> CourseDetailOut:
    course = await course_service.create_course(db, user, data)
    return course  # type: ignore


@router.get("/admin/{course_id}", response_model=CourseDetailOut)
async def get_admin_course(
    course_id: uuid.UUID,
    user: User = Depends(require_permission("courses:view")),
    db: AsyncSession = Depends(get_db),
) -> CourseDetailOut:
    course = await course_service.get_course_by_id(db, course_id, load_curriculum=True)
    if not course:
        raise NotFoundException("Course not found", code="COURSE_NOT_FOUND")
    return course  # type: ignore


@router.put("/admin/{course_id}", response_model=CourseDetailOut)
async def update_course(
    course_id: uuid.UUID,
    data: CourseUpdate,
    user: User = Depends(require_permission("courses:manage")),
    db: AsyncSession = Depends(get_db),
) -> CourseDetailOut:
    course = await course_service.update_course(db, user, course_id, data)
    return course  # type: ignore


@router.delete("/admin/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_course(
    course_id: uuid.UUID,
    user: User = Depends(require_permission("courses:delete")),
    db: AsyncSession = Depends(get_db),
) -> None:
    await course_service.delete_course(db, course_id)


# --- Sections Admin ---
@router.post("/admin/{course_id}/sections", response_model=CourseSectionOut, status_code=status.HTTP_201_CREATED)
async def create_section(
    course_id: uuid.UUID,
    data: CourseSectionCreate,
    user: User = Depends(require_permission("courses:manage")),
    db: AsyncSession = Depends(get_db),
) -> CourseSectionOut:
    section = await course_service.create_section(db, course_id, data)
    return section  # type: ignore


@router.put("/admin/sections/{section_id}", response_model=CourseSectionOut)
async def update_section(
    section_id: uuid.UUID,
    data: CourseSectionUpdate,
    user: User = Depends(require_permission("courses:manage")),
    db: AsyncSession = Depends(get_db),
) -> CourseSectionOut:
    section = await course_service.update_section(db, section_id, data)
    return section  # type: ignore


@router.delete("/admin/sections/{section_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_section(
    section_id: uuid.UUID,
    user: User = Depends(require_permission("courses:delete")),
    db: AsyncSession = Depends(get_db),
) -> None:
    await course_service.delete_section(db, section_id)


@router.post("/admin/{course_id}/sections/reorder")
async def reorder_sections(
    course_id: uuid.UUID,
    data: ReorderSectionsInput,
    user: User = Depends(require_permission("courses:manage")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await course_service.reorder_sections(db, course_id, data.sections)
    return {"message": "Sections reordered"}


# --- Lessons Admin ---
@router.post("/admin/sections/{section_id}/lessons", response_model=CourseLessonOut, status_code=status.HTTP_201_CREATED)
async def create_lesson(
    section_id: uuid.UUID,
    data: CourseLessonCreate,
    user: User = Depends(require_permission("courses:manage")),
    db: AsyncSession = Depends(get_db),
) -> CourseLessonOut:
    lesson = await course_service.create_lesson(db, section_id, data)
    return lesson  # type: ignore


@router.get("/admin/lessons/{lesson_id}", response_model=CourseLessonOut)
async def get_admin_lesson(
    lesson_id: uuid.UUID,
    user: User = Depends(require_permission("courses:view")),
    db: AsyncSession = Depends(get_db),
) -> CourseLessonOut:
    lesson = await course_service.get_lesson_by_id(db, lesson_id)
    if not lesson:
        raise NotFoundException("Lesson not found", code="LESSON_NOT_FOUND")
    return lesson  # type: ignore


@router.put("/admin/lessons/{lesson_id}", response_model=CourseLessonOut)
async def update_lesson(
    lesson_id: uuid.UUID,
    data: CourseLessonUpdate,
    user: User = Depends(require_permission("courses:manage")),
    db: AsyncSession = Depends(get_db),
) -> CourseLessonOut:
    lesson = await course_service.update_lesson(db, lesson_id, data)
    return lesson  # type: ignore


@router.delete("/admin/lessons/{lesson_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_lesson(
    lesson_id: uuid.UUID,
    user: User = Depends(require_permission("courses:delete")),
    db: AsyncSession = Depends(get_db),
) -> None:
    await course_service.delete_lesson(db, lesson_id)


@router.post("/admin/{course_id}/lessons/reorder")
async def reorder_lessons(
    course_id: uuid.UUID,
    data: ReorderLessonsInput,
    user: User = Depends(require_permission("courses:manage")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await course_service.reorder_lessons(db, course_id, data.lessons)
    return {"message": "Lessons reordered"}


# --- Attachments Admin ---
@router.post("/admin/lessons/{lesson_id}/attachments", response_model=LessonAttachmentOut, status_code=status.HTTP_201_CREATED)
async def add_attachment(
    lesson_id: uuid.UUID,
    data: LessonAttachmentCreate,
    user: User = Depends(require_permission("courses:manage")),
    db: AsyncSession = Depends(get_db),
) -> LessonAttachmentOut:
    att = await course_service.add_attachment(db, lesson_id, data)
    return att  # type: ignore


@router.delete("/admin/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attachment(
    attachment_id: uuid.UUID,
    user: User = Depends(require_permission("courses:manage")),
    db: AsyncSession = Depends(get_db),
) -> None:
    await course_service.delete_attachment(db, attachment_id)


# --- Cloudflare R2 Direct Upload Intent ---
@router.post("/admin/upload-intent", response_model=DirectUploadIntentResponse)
async def create_upload_intent(
    data: DirectUploadIntentRequest,
    user: User = Depends(require_permission("courses:manage")),
) -> DirectUploadIntentResponse:
    return course_service.generate_direct_upload_intent(data)
