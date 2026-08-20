import asyncio
import logging
from uuid import UUID, uuid4

from fastapi import UploadFile
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException, ValidationException
from app.models import DownloadableAsset, Post, PostStatus
from app.services.media_service import (
    delete_from_r2,
    object_key,
    presign_get_url,
    read_upload,
    upload_to_r2,
)

logger = logging.getLogger(__name__)

MAX_ASSET_BYTES = 25 * 1024 * 1024
DOWNLOAD_URL_TTL = 300

CONTENT_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls": "application/vnd.ms-excel",
    "csv": "text/csv",
    "pdf": "application/pdf",
    "zip": "application/zip",
}

MAGIC_PREFIXES = {
    "xlsx": b"PK\x03\x04",
    "zip": b"PK\x03\x04",
    "pdf": b"%PDF-",
    "xls": b"\xd0\xcf\x11\xe0",
}


async def attach(db: AsyncSession, post_id: UUID, file: UploadFile) -> DownloadableAsset:
    post = await db.scalar(select(Post).where(Post.id == post_id, Post.deleted_at.is_(None)))
    if post is None:
        raise NotFoundException("Post not found", code="POST_NOT_FOUND")

    filename = file.filename or ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in CONTENT_TYPES:
        raise ValidationException(
            "Allowed file types: xlsx, xls, csv, pdf, zip", code="INVALID_FILE_TYPE"
        )

    data = await read_upload(file, MAX_ASSET_BYTES)

    prefix = MAGIC_PREFIXES.get(ext)
    if prefix is not None and not data.startswith(prefix):
        raise ValidationException("File content does not match its extension", code="INVALID_FILE_TYPE")

    key = f"downloads/{post_id}/{uuid4().hex}.{ext}"
    file_url = await asyncio.to_thread(upload_to_r2, key, data, CONTENT_TYPES[ext])

    asset = DownloadableAsset(
        post_id=post_id,
        file_name=filename[:255],
        file_url=file_url,
        file_type=ext,
        file_size_kb=len(data) // 1024,
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)
    return asset


async def list_for_post(db: AsyncSession, post_id: UUID) -> list[DownloadableAsset]:
    post = await db.scalar(
        select(Post).where(
            Post.id == post_id,
            Post.status == PostStatus.published,
            Post.deleted_at.is_(None),
        )
    )
    if post is None:
        raise NotFoundException("Post not found", code="POST_NOT_FOUND")

    return list(
        (
            await db.scalars(
                select(DownloadableAsset)
                .where(DownloadableAsset.post_id == post_id)
                .order_by(DownloadableAsset.created_at.desc())
            )
        ).all()
    )


async def issue_download_url(db: AsyncSession, asset_id: UUID) -> str:
    asset = await _published_asset_or_404(db, asset_id)

    await db.execute(
        update(DownloadableAsset)
        .where(DownloadableAsset.id == asset_id)
        .values(download_count=DownloadableAsset.download_count + 1)
    )
    await db.commit()
    return await asyncio.to_thread(presign_get_url, object_key(asset.file_url), DOWNLOAD_URL_TTL)


async def delete(db: AsyncSession, asset_id: UUID) -> None:
    asset = await db.scalar(select(DownloadableAsset).where(DownloadableAsset.id == asset_id))
    if asset is None:
        raise NotFoundException("Asset not found", code="ASSET_NOT_FOUND")

    await db.delete(asset)
    await db.commit()

    try:
        delete_from_r2(object_key(asset.file_url))
    except Exception:
        logger.exception("Failed to delete R2 object for asset %s", asset_id)


async def _published_asset_or_404(db: AsyncSession, asset_id: UUID) -> DownloadableAsset:
    asset = await db.scalar(
        select(DownloadableAsset)
        .join(Post, DownloadableAsset.post_id == Post.id)
        .where(
            DownloadableAsset.id == asset_id,
            Post.status == PostStatus.published,
            Post.deleted_at.is_(None),
        )
    )
    if asset is None:
        raise NotFoundException("Asset not found", code="ASSET_NOT_FOUND")
    return asset
