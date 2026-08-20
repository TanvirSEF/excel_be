from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, select

import app.services.asset_service as asset_service
from app.core.database import AsyncSessionLocal
from app.models import DownloadableAsset, Post

XLSX_BYTES = b"PK\x03\x04" + b"0" * 256


@pytest.fixture(autouse=True)
async def assets_test_cleanup():
    await _cleanup()
    yield
    await _cleanup()


async def _cleanup():
    async with AsyncSessionLocal() as db:
        await db.execute(delete(DownloadableAsset).where(DownloadableAsset.file_name.like("assets-test-%")))
        await db.execute(delete(Post).where(Post.slug.like("assets-test-%")))
        await db.commit()


@pytest.fixture(autouse=True)
def fake_r2(monkeypatch):
    monkeypatch.setattr(
        asset_service, "upload_to_r2", lambda key, data, content_type: f"https://cdn.test/{key}"
    )
    monkeypatch.setattr(asset_service, "presign_get_url", lambda key, expires_in: f"https://signed.test/{key}")
    monkeypatch.setattr(asset_service, "delete_from_r2", lambda key: None)


async def create_draft_post(client: httpx.AsyncClient, token: str) -> dict:
    response = await client.post(
        "/api/v1/posts",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "title": f"Assets test post {uuid4().hex[:8]}",
            "slug": f"assets-test-{uuid4().hex[:8]}",
            "content_json": {"blocks": [{"type": "paragraph", "text": "body"}]},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def publish(client: httpx.AsyncClient, token: str, post_id: str) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    await client.post(f"/api/v1/posts/{post_id}/submit-review", headers=headers)
    response = await client.post(f"/api/v1/posts/{post_id}/publish", headers=headers)
    assert response.status_code == 200, response.text


async def attach(client: httpx.AsyncClient, token: str, post_id: str, content=b"", filename="assets-test.xlsx"):
    return await client.post(
        f"/api/v1/posts/{post_id}/assets",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": (filename, content, "application/octet-stream")},
    )


async def test_attach_and_public_list(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    post = await create_draft_post(client, admin_token)

    response = await attach(client, admin_token, post["id"], XLSX_BYTES)
    assert response.status_code == 201, response.text
    asset = response.json()
    assert asset["file_type"] == "xlsx"
    assert asset["download_count"] == 0

    draft_list = await client.get(f"/api/v1/posts/{post['id']}/assets")
    assert draft_list.status_code == 404

    await publish(client, admin_token, post["id"])

    published_list = await client.get(f"/api/v1/posts/{post['id']}/assets")
    assert published_list.status_code == 200
    assert [item["id"] for item in published_list.json()] == [asset["id"]]


async def test_attach_rejects_content_not_matching_extension(client, admin_token):
    post = await create_draft_post(client, admin_token)

    response = await attach(client, admin_token, post["id"], b"plain text, not a zip")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_FILE_TYPE"


async def test_attach_rejects_unsupported_extension(client, admin_token):
    post = await create_draft_post(client, admin_token)

    response = await attach(client, admin_token, post["id"], b"MZ9999", "assets-test.exe")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_FILE_TYPE"


async def test_download_issues_signed_url_and_increments_count(client, admin_token):
    post = await create_draft_post(client, admin_token)
    asset = (await attach(client, admin_token, post["id"], XLSX_BYTES)).json()
    await publish(client, admin_token, post["id"])

    first = await client.get(f"/api/v1/assets/{asset['id']}/download")
    assert first.status_code == 200, first.text
    assert first.json()["url"].startswith("https://signed.test/")
    assert first.json()["expires_in"] == 300

    second = await client.get(f"/api/v1/assets/{asset['id']}/download")
    assert second.status_code == 200

    listed = await client.get(f"/api/v1/posts/{post['id']}/assets")
    assert listed.json()[0]["download_count"] == 2


async def test_download_unknown_asset_404(client):
    response = await client.get(f"/api/v1/assets/{uuid4()}/download")
    assert response.status_code == 404


async def test_delete_requires_editor(client, admin_token):
    post = await create_draft_post(client, admin_token)
    asset = (await attach(client, admin_token, post["id"], XLSX_BYTES)).json()

    writer_login = await client.post(
        "/api/v1/auth/login", data={"username": "writer@test.com", "password": "WriterPass123!"}
    )
    writer_token = writer_login.json()["access_token"]

    denied = await client.delete(
        f"/api/v1/assets/{asset['id']}", headers={"Authorization": f"Bearer {writer_token}"}
    )
    assert denied.status_code == 403

    allowed = await client.delete(
        f"/api/v1/assets/{asset['id']}", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert allowed.status_code == 200

    async with AsyncSessionLocal() as db:
        remaining = await db.scalar(
            select(DownloadableAsset).where(DownloadableAsset.id == asset["id"])
        )
    assert remaining is None
