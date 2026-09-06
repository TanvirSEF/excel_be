from pathlib import Path

import pytest
from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal
from app.models import Category, Comment, Post, PostTag, Tag, User, UserRole
from app.services.import_service import run_import

FIXTURE = Path(__file__).resolve().parents[1] / "scripts" / "fixtures" / "wp_export_sample.xml"
POST_SLUGS = ("wp-published-post", "wp-draft-post", "wp-scheduled-post")


@pytest.fixture(autouse=True)
async def imports_test_cleanup():
    await _cleanup()
    yield
    await _cleanup()


async def _cleanup():
    async with AsyncSessionLocal() as db:
        post_ids = select(Post.id).where(Post.slug.in_(POST_SLUGS))
        await db.execute(delete(Comment).where(Comment.post_id.in_(post_ids)))
        await db.execute(delete(PostTag).where(PostTag.post_id.in_(post_ids)))
        await db.execute(delete(Post).where(Post.slug.in_(POST_SLUGS)))
        await db.execute(
            delete(Tag).where(
                Tag.name.in_(("VLOOKUP", "Excel")),
                ~Tag.id.in_(select(PostTag.tag_id)),
            )
        )
        await db.execute(
            delete(Category).where(
                Category.slug == "advanced-formulas",
                ~Category.id.in_(select(Post.category_id).where(Post.category_id.isnot(None))),
            )
        )
        await db.commit()


def _all_texts(blocks):
    for block in blocks:
        yield block.get("text") or ""
        for run in block.get("content") or []:
            if isinstance(run, dict):
                yield run.get("text", "")


async def test_run_import_expands_shortcodes_and_keeps_marks():
    async with AsyncSessionLocal() as db:
        author = await db.scalar(select(User).where(User.role == UserRole.super_admin))
        result = await run_import(
            db, FIXTURE.read_bytes(), author, include_images=False, dry_run=False
        )
        assert result.posts_created >= 1
        assert result.dry_run is False

        post = await db.scalar(select(Post).where(Post.slug == "wp-published-post"))
    assert post is not None

    texts = list(_all_texts(post.content_json["blocks"]))
    assert not any("[wpsm_" in t or "[su_highlight" in t or "[sc:" in t for t in texts)
    assert any("[Pages]" in t for t in texts)

    callouts = [b for b in post.content_json["blocks"] if b["type"] == "callout"]
    assert any(c.get("title") == "Key Takeaways" for c in callouts)
    assert any(c.get("title") == "Explanation" for c in callouts)
    assert any(
        r.get("marks") == [{"type": "bold"}]
        for c in callouts
        for r in c.get("content", [])
    )

    headings = [b for b in post.content_json["blocks"] if b["type"] == "heading"]
    assert any(h["text"] == "1. Using the VLOOKUP Function" for h in headings)

    assert "[wpsm_" not in post.content_html
    assert "[su_highlight" not in post.content_html
    assert "data-callout" in post.content_html
    assert "<mark>" in post.content_html
    assert "<strong>" in post.content_html
