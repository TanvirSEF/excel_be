import pytest

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
