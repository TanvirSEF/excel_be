import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.course import CourseLevel, CourseStatus, LessonType
from app.schemas.course import (
    CourseCreate,
    CourseLessonCreate,
    CourseSectionCreate,
    CourseUpdate,
    DirectUploadIntentRequest,
)
from app.services import course_service, progress_service


def test_course_schemas():
    data = CourseCreate(
        title="Excel Financial Modeling Masterclass",
        slug="excel-financial-modeling-masterclass",
        headline="Learn DCF and LBO modeling",
        level=CourseLevel.advanced,
        is_free=False,
        price=49.99,
        learning_outcomes=["Build DCF model", "Master sensitivity analysis"],
    )
    assert data.title == "Excel Financial Modeling Masterclass"
    assert data.price == 49.99
    assert len(data.learning_outcomes) == 2

    lesson = CourseLessonCreate(
        title="Intro to WACC",
        lesson_type=LessonType.video,
        video_duration_seconds=640,
        is_preview=True,
    )
    assert lesson.video_duration_seconds == 640
    assert lesson.is_preview is True


def test_upload_intent_generator():
    course_id = uuid.uuid4()
    lesson_id = uuid.uuid4()

    mock_s3 = MagicMock()
    mock_s3.generate_presigned_url.return_value = "https://mock-r2.cloudflarestorage.com/signed-put"

    with patch("app.services.course_service.get_s3", return_value=mock_s3):
        req = DirectUploadIntentRequest(
            file_name="Lecture 1 - Advanced Formulas.mp4",
            file_size_bytes=104857600,
            content_type="video/mp4",
            asset_type="video",
            course_id=course_id,
            lesson_id=lesson_id,
        )
        res = course_service.generate_direct_upload_intent(req)

        assert res.upload_url == "https://mock-r2.cloudflarestorage.com/signed-put"
        assert f"courses/{course_id}/videos/" in res.file_key
        assert res.file_key.endswith(".mp4")
        assert res.file_key in res.public_url

        att_req = DirectUploadIntentRequest(
            file_name="Model_Template.xlsx",
            file_size_bytes=524288,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            asset_type="attachment",
            course_id=course_id,
        )
        att_res = course_service.generate_direct_upload_intent(att_req)
        assert f"courses/{course_id}/attachments/" in att_res.file_key
        assert att_res.file_key.endswith(".xlsx")


@pytest.mark.asyncio
async def test_course_slug_generation():
    db = MagicMock()
    db.scalar = AsyncMock(side_effect=[None])
    slug = await course_service._unique_course_slug(db, "advanced-excel-formulas")
    assert slug == "advanced-excel-formulas"

    db.scalar = AsyncMock(side_effect=[uuid.uuid4(), None])
    slug_conflict = await course_service._unique_course_slug(db, "advanced-excel-formulas")
    assert slug_conflict == "advanced-excel-formulas-2"


@pytest.mark.asyncio
async def test_lesson_slug_generation():
    db = MagicMock()
    course_id = uuid.uuid4()
    db.scalar = AsyncMock(side_effect=[uuid.uuid4(), None])
    slug = await course_service._unique_lesson_slug(db, course_id, "vlookup-vs-xlookup")
    assert slug == "vlookup-vs-xlookup-2"
