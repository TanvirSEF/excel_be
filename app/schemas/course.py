import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.course import CourseLevel, CourseStatus, LessonType
from app.schemas.common import RequestModel
from app.utils.slugify import SLUG_PATTERN


class InstructorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    avatar_url: str | None = None
    bio: str | None = None


class CategorySimpleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str


# --- Attachments ---
class LessonAttachmentCreate(RequestModel):
    file_name: str = Field(min_length=1, max_length=255)
    file_url: str = Field(min_length=1, max_length=500)
    file_size_bytes: int = Field(default=0, ge=0)
    file_type: str = Field(default="application/octet-stream", max_length=100)


class LessonAttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    lesson_id: uuid.UUID
    file_name: str
    file_url: str
    file_size_bytes: int
    file_type: str
    download_count: int
    created_at: datetime


# --- Lessons ---
class CourseLessonCreate(RequestModel):
    title: str = Field(min_length=1, max_length=255)
    slug: str | None = Field(default=None, max_length=255, pattern=SLUG_PATTERN)
    lesson_type: LessonType = LessonType.video
    video_url: str | None = None
    video_duration_seconds: int = Field(default=0, ge=0)
    content_json: dict | None = None
    content_html: str | None = None
    is_preview: bool = False
    order_index: int = 0


class CourseLessonUpdate(RequestModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(default=None, max_length=255, pattern=SLUG_PATTERN)
    lesson_type: LessonType | None = None
    video_url: str | None = None
    video_duration_seconds: int | None = Field(default=None, ge=0)
    content_json: dict | None = None
    content_html: str | None = None
    is_preview: bool | None = None
    order_index: int | None = None
    section_id: uuid.UUID | None = None


class CourseLessonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    section_id: uuid.UUID
    course_id: uuid.UUID
    title: str
    slug: str
    lesson_type: LessonType
    video_url: str | None = None
    video_duration_seconds: int = 0
    content_json: dict | None = None
    content_html: str | None = None
    is_preview: bool = False
    order_index: int = 0
    created_at: datetime
    updated_at: datetime
    attachments: list[LessonAttachmentOut] = []


class LessonPublicListItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    section_id: uuid.UUID
    course_id: uuid.UUID
    title: str
    slug: str
    lesson_type: LessonType
    video_duration_seconds: int = 0
    is_preview: bool = False
    order_index: int = 0
    is_completed: bool = False


class ClassroomLessonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    section_id: uuid.UUID
    course_id: uuid.UUID
    title: str
    slug: str
    lesson_type: LessonType
    video_url: str | None = None
    video_duration_seconds: int = 0
    content_json: dict | None = None
    content_html: str | None = None
    is_preview: bool = False
    order_index: int = 0
    is_completed: bool = False
    last_watched_second: int = 0
    attachments: list[LessonAttachmentOut] = []
    prev_lesson_id: uuid.UUID | None = None
    next_lesson_id: uuid.UUID | None = None


# --- Sections ---
class CourseSectionCreate(RequestModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    order_index: int = 0


class CourseSectionUpdate(RequestModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    order_index: int | None = None


class CourseSectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    course_id: uuid.UUID
    title: str
    description: str | None = None
    order_index: int = 0
    created_at: datetime
    updated_at: datetime
    lessons: list[CourseLessonOut] = []


class CourseSectionPublicOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    course_id: uuid.UUID
    title: str
    description: str | None = None
    order_index: int = 0
    lessons: list[LessonPublicListItemOut] = []


# --- Course ---
class CourseCreate(RequestModel):
    title: str = Field(min_length=1, max_length=255)
    slug: str | None = Field(default=None, max_length=255, pattern=SLUG_PATTERN)
    headline: str | None = Field(default=None, max_length=500)
    description: str | None = None
    thumbnail_url: str | None = None
    promo_video_url: str | None = None
    level: CourseLevel = CourseLevel.all_levels
    category_id: uuid.UUID | None = None
    instructor_id: uuid.UUID | None = None
    is_free: bool = True
    price: float = Field(default=0.0, ge=0)
    learning_outcomes: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)


class CourseUpdate(RequestModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(default=None, max_length=255, pattern=SLUG_PATTERN)
    headline: str | None = None
    description: str | None = None
    thumbnail_url: str | None = None
    promo_video_url: str | None = None
    level: CourseLevel | None = None
    status: CourseStatus | None = None
    category_id: uuid.UUID | None = None
    instructor_id: uuid.UUID | None = None
    is_free: bool | None = None
    price: float | None = Field(default=None, ge=0)
    learning_outcomes: list[str] | None = None
    requirements: list[str] | None = None


class CourseListItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    slug: str
    headline: str | None = None
    thumbnail_url: str | None = None
    promo_video_url: str | None = None
    level: CourseLevel
    status: CourseStatus
    is_free: bool
    price: float
    total_duration_seconds: int = 0
    total_lessons_count: int = 0
    published_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    category: CategorySimpleOut | None = None
    instructor: InstructorOut | None = None


class CourseDetailOut(CourseListItemOut):
    description: str | None = None
    learning_outcomes: list[str] = []
    requirements: list[str] = []
    sections: list[CourseSectionOut] = []


class CoursePublicDetailOut(CourseListItemOut):
    description: str | None = None
    learning_outcomes: list[str] = []
    requirements: list[str] = []
    sections: list[CourseSectionPublicOut] = []
    is_enrolled: bool = False
    progress_percent: int = 0


# --- Reorder Payloads ---
class SectionOrderItem(RequestModel):
    id: uuid.UUID
    order_index: int


class ReorderSectionsInput(RequestModel):
    sections: list[SectionOrderItem]


class LessonOrderItem(RequestModel):
    id: uuid.UUID
    order_index: int
    section_id: uuid.UUID | None = None


class ReorderLessonsInput(RequestModel):
    lessons: list[LessonOrderItem]


# --- Progress & Enrollment ---
class ProgressUpdateInput(RequestModel):
    is_completed: bool = True
    last_watched_second: int = Field(default=0, ge=0)


class EnrollmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    course_id: uuid.UUID
    progress_percent: int
    enrolled_at: datetime
    completed_at: datetime | None = None


class DirectUploadIntentRequest(RequestModel):
    file_name: str = Field(min_length=1, max_length=255)
    file_size_bytes: int = Field(gt=0)
    content_type: str = Field(min_length=3, max_length=100)
    asset_type: str = Field(default="video", description="video, attachment, or thumbnail")
    course_id: uuid.UUID
    lesson_id: uuid.UUID | None = None


class DirectUploadIntentResponse(BaseModel):
    upload_url: str
    public_url: str
    file_key: str
