import asyncio
import io
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

import boto3
from botocore.config import Config
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ConflictException, NotFoundException, ValidationException
from app.deps.pagination import PaginationParams
from app.models import Media, Post, PostStatus, User

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_WIDTH = 1920
WEBP_QUALITY = 82
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF"}

_s3 = None


@dataclass
class ProcessedImage:
    data: bytes
    width: int
    height: int


def get_s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client(
            "s3",
            endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            region_name="auto",
            config=Config(connect_timeout=10, read_timeout=30, retries={"max_attempts": 1}),
        )
    return _s3


def process_image(file_bytes: bytes) -> ProcessedImage:
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise ValidationException("File exceeds the 10MB limit", code="FILE_TOO_LARGE")

    try:
        img = Image.open(io.BytesIO(file_bytes))
        img.load()
    except Exception:
        raise ValidationException("File is not a valid image", code="INVALID_IMAGE")

    if img.format not in ALLOWED_FORMATS:
        raise ValidationException(
            f"Unsupported image format '{img.format}'", code="INVALID_IMAGE"
        )

    if img.mode not in ("RGB", "RGBA", "L"):
        img = img.convert("RGB")

    if img.width > MAX_WIDTH:
        ratio = MAX_WIDTH / img.width
        img = img.resize((MAX_WIDTH, round(img.height * ratio)))

    out = io.BytesIO()
    img.save(out, format="WEBP", quality=WEBP_QUALITY)
    return ProcessedImage(data=out.getvalue(), width=img.width, height=img.height)


def upload_to_r2(key: str, data: bytes) -> str:
    get_s3().put_object(
        Bucket=settings.r2_bucket_name,
        Key=key,
        Body=data,
        ContentType="image/webp",
    )
    return f"{settings.r2_public_url.rstrip('/')}/{key}"


def delete_from_r2(key: str) -> None:
    get_s3().delete_object(Bucket=settings.r2_bucket_name, Key=key)


def object_key(file_url: str) -> str:
    return file_url.replace(f"{settings.r2_public_url.rstrip('/')}/", "", 1)


def alt_text_from(filename: str) -> str:
    stem = filename.rsplit(".", 1)[0]
    words = [w for w in stem.replace("_", "-").replace("-", " ").split() if w]
    return " ".join(words).title() or "Uploaded image"


async def upload(
    db: AsyncSession, user: User, file_bytes: bytes, filename: str, folder: str
) -> Media:
    processed = await asyncio.to_thread(process_image, file_bytes)
    key = f"images/{datetime.now(timezone.utc):%Y/%m}/{uuid4().hex}.webp"
    file_url = await asyncio.to_thread(upload_to_r2, key, processed.data)

    media = Media(
        uploader_id=user.id,
        file_url=file_url,
        file_type="image",
        alt_text=alt_text_from(filename),
        width=processed.width,
        height=processed.height,
        size_kb=len(processed.data) // 1024,
        folder=folder,
    )
    db.add(media)
    await db.commit()
    await db.refresh(media)
    return media


async def list_media(
    db: AsyncSession, pagination: PaginationParams, folder: str | None
) -> dict:
    conditions = []
    if folder is not None:
        conditions.append(Media.folder == folder)

    total = await db.scalar(select(func.count()).select_from(Media).where(*conditions))
    items = (
        await db.scalars(
            select(Media)
            .where(*conditions)
            .order_by(Media.created_at.desc())
            .offset(pagination.offset)
            .limit(pagination.page_size)
        )
    ).all()

    return {
        "items": items,
        "total": total,
        "page": pagination.page,
        "page_size": pagination.page_size,
        "total_pages": math.ceil(total / pagination.page_size) if total else 0,
    }


async def delete_media(db: AsyncSession, media_id: UUID) -> None:
    media = await db.scalar(select(Media).where(Media.id == media_id))
    if media is None:
        raise NotFoundException("Media not found", code="MEDIA_NOT_FOUND")

    in_use = await db.scalar(
        select(func.count())
        .select_from(Post)
        .where(
            Post.status == PostStatus.published,
            Post.deleted_at.is_(None),
            (Post.featured_image_url == media.file_url)
            | (Post.og_image_url == media.file_url),
        )
    )
    if in_use:
        raise ConflictException(
            "Media is referenced by a published post", code="MEDIA_IN_USE"
        )

    await db.delete(media)
    await db.commit()

    try:
        delete_from_r2(object_key(media.file_url))
    except Exception:
        logger.exception("Failed to delete R2 object for media %s", media.id)
