from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal
from app.models import Media, User, UserRole
from app.services.media_service import MAX_UPLOAD_BYTES


async def test_upload_rejects_oversized_file_before_processing(client, admin_token):
    response = await client.post(
        "/api/v1/media/upload",
        headers={"Authorization": f"Bearer {admin_token}"},
        files={"file": ("big.png", b"x" * (MAX_UPLOAD_BYTES + 1), "image/png")},
        data={"folder": "test"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"


async def test_upload_processes_size_then_content(client, admin_token):
    response = await client.post(
        "/api/v1/media/upload",
        headers={"Authorization": f"Bearer {admin_token}"},
        files={"file": ("fake.png", b"x" * 1024, "image/png")},
        data={"folder": "test"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_IMAGE"


async def create_media_row() -> Media:
    async with AsyncSessionLocal() as db:
        admin = await db.scalar(select(User).where(User.role == UserRole.super_admin))
        media = Media(
            uploader_id=admin.id,
            file_url=f"https://media.example.com/images/media-test-{admin.id.hex[:8]}.webp",
            file_type="image",
            folder="uncategorized",
        )
        db.add(media)
        await db.commit()
        await db.refresh(media)
        return media


async def delete_media_row(media_id) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Media).where(Media.id == media_id))
        await db.commit()


async def test_update_media_alt_text_and_folder(client, admin_token):
    media = await create_media_row()
    try:
        response = await client.patch(
            f"/api/v1/media/{media.id}",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"alt_text": "Chart of quarterly sales", "folder": "articles"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["id"] == str(media.id)
        assert body["alt_text"] == "Chart of quarterly sales"
        assert body["folder"] == "articles"
    finally:
        await delete_media_row(media.id)


async def test_update_media_ignores_unset_fields(client, admin_token):
    media = await create_media_row()
    try:
        response = await client.patch(
            f"/api/v1/media/{media.id}",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"folder": "tutorials"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["folder"] == "tutorials"
        assert response.json()["alt_text"] is None
    finally:
        await delete_media_row(media.id)


async def test_update_media_unknown_id_returns_404(client, admin_token):
    response = await client.patch(
        f"/api/v1/media/{uuid4()}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"alt_text": "missing"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "MEDIA_NOT_FOUND"
