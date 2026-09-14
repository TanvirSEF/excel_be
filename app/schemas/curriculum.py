from pydantic import BaseModel


class CurriculumLesson(BaseModel):
    slug: str
    title: str
    reading_time_minutes: int | None


class CurriculumTopic(BaseModel):
    slug: str
    name: str
    lesson_count: int
    lessons: list[CurriculumLesson]


class CurriculumModule(BaseModel):
    slug: str
    name: str
    description: str | None
    lesson_count: int
    topics: list[CurriculumTopic]
