from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal
from app.models import Post
from app.utils.sanitize import sanitize_html


@pytest.fixture(autouse=True)
async def sanitization_test_cleanup():
    await _cleanup()
    yield
    await _cleanup()


async def _cleanup():
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Post).where(Post.slug.like("sanitize-test-post-%")))
        await db.commit()


def test_sanitize_strips_script_and_event_handlers():
    dirty = '<p onclick="steal()">hi</p><script>alert(1)</script><a href="https://x.com">link</a>'
    clean = sanitize_html(dirty)
    assert "<script" not in clean
    assert "onclick" not in clean
    assert '<a href="https://x.com"' in clean
    assert "alert" not in clean


def test_sanitize_blocks_javascript_urls():
    clean = sanitize_html('<a href="javascript:alert(1)">x</a><img src="javascript:alert(2)">')
    assert "javascript:" not in clean


def test_sanitize_keeps_rich_content():
    html = (
        '<figure class="chart"><img src="https://cdn.example.com/a.png" alt="chart" width="800">'
        "<figcaption>Q3</figcaption></figure>"
        '<iframe src="https://www.youtube.com/embed/abc" width="560" height="315"></iframe>'
        "<table><tr><td colspan=\"2\">cell</td></tr></table>"
    )
    clean = sanitize_html(html)
    assert "<figure" in clean and "figcaption" in clean
    assert '<img src="https://cdn.example.com/a.png"' in clean
    assert "youtube.com/embed/abc" in clean
    assert 'colspan="2"' in clean


async def test_stored_content_html_is_sanitized(client, admin_token):
    marker = uuid4().hex[:8]
    response = await client.post(
        "/api/v1/posts",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "title": f"Sanitize test post {marker}",
            "content_json": {
                "blocks": [
                    {"type": "html", "html": '<p onclick="x()">keep me</p><script>alert(1)</script>'},
                    {"type": "paragraph", "text": "plain block"},
                ]
            },
        },
    )
    assert response.status_code == 201, response.text
    slug = response.json()["slug"]

    async with AsyncSessionLocal() as db:
        post = await db.scalar(select(Post).where(Post.slug == slug))
    assert post is not None
    assert "<script" not in post.content_html
    assert "onclick" not in post.content_html
    assert "keep me" in post.content_html
    assert "plain block" in post.content_html
