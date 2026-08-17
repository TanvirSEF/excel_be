import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.utils.slugify import SLUG_PATTERN


class TagCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    slug: str | None = Field(default=None, max_length=80, pattern=SLUG_PATTERN)


class TagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
