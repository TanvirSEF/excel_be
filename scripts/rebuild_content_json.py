"""Rebuild content_json blocks from stored content_html for WP-migrated posts.

The original migration split WP HTML into tiny fragments, losing inline text
runs. content_html still holds the full original HTML, so this script rebuilds
proper structured blocks (headings, paragraphs with marks, lists, tables,
images, code, quotes) from it.

Usage (from excel_be/):
    PYTHONPATH=. venv/bin/python scripts/rebuild_content_json.py --dry-run
    PYTHONPATH=. venv/bin/python scripts/rebuild_content_json.py --slug <slug>
    PYTHONPATH=. venv/bin/python scripts/rebuild_content_json.py --all
"""
import argparse
import asyncio

from bs4 import BeautifulSoup, NavigableString, Tag
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models import Post
from app.services import cache_service
from app.utils.reading_time import reading_time_minutes
from app.utils.sanitize import sanitize_html

MARK_TAGS = {
    "strong": "bold",
    "b": "bold",
    "em": "italic",
    "i": "italic",
    "del": "strike",
    "s": "strike",
    "strike": "strike",
    "code": "code",
    "kbd": "code",
}
HEADING_LEVELS = {"h1": 2, "h2": 2, "h3": 3, "h4": 4, "h5": 4, "h6": 4}
BLOCK_TAGS = {
    "p", "table", "ul", "ol", "pre", "blockquote", "figure", "div",
    "iframe", "hr", "img", *HEADING_LEVELS,
}
INLINE_TAGS = set(MARK_TAGS) | {
    "a", "br", "span", "sup", "sub", "small", "u", "ins", "mark",
    "abbr", "time", "cite", "q", "font", "bdi", "bdo", "data", "wbr",
}


def collect_runs(node, marks):
    runs = []
    for child in node.children:
        if isinstance(child, NavigableString):
            text = str(child)
            if text:
                run = {"text": text}
                if marks:
                    run["marks"] = list(marks)
                runs.append(run)
            continue
        if not isinstance(child, Tag):
            continue
        if child.name == "br":
            runs.append({"text": "\n"})
            continue
        child_marks = marks
        if child.name in MARK_TAGS:
            child_marks = marks + [{"type": MARK_TAGS[child.name]}]
        elif child.name == "a":
            href = (child.get("href") or "").strip()
            if href:
                child_marks = marks + [{"type": "link", "href": href}]
        runs.extend(collect_runs(child, child_marks))
    return runs


def runs_text(runs):
    return "".join(r["text"] for r in runs).strip()


def has_block_child(el):
    return any(
        isinstance(c, Tag) and c.name in BLOCK_TAGS
        for c in el.children
    )


def image_block(img, fallback_alt=""):
    src = (img.get("src") or "").strip()
    if not src:
        return None
    return {"type": "image", "url": src, "alt": img.get("alt") or fallback_alt}


def table_block(table):
    nested = table.find("table") is not None
    has_span = any(
        td.get("colspan") or td.get("rowspan")
        for td in table.find_all(["td", "th"])
    )
    if nested or has_span:
        return {"type": "html", "html": sanitize_html(str(table))}

    rows = []
    first_row_all_th = False
    trs = table.find_all("tr")
    for tr_index, tr in enumerate(trs):
        cells = tr.find_all(["td", "th"], recursive=False)
        rows.append([collect_runs(cell, []) for cell in cells])
        if tr_index == 0 and cells:
            first_row_all_th = all(c.name == "th" for c in cells)

    header = table.find("thead") is not None or first_row_all_th
    return {"type": "table", "rows": rows, "header": header}


def convert(html):
    soup = BeautifulSoup(html or "", "html.parser")
    blocks = []

    def emit_paragraph(runs):
        text = runs_text(runs)
        if not text:
            return
        blocks.append({"type": "paragraph", "text": text, "content": runs})

    def flush_pending(pending):
        paragraphs = [[]]

        def add_runs(runs):
            for run in runs:
                parts = run["text"].split("\n\n")
                for index, part in enumerate(parts):
                    if index > 0:
                        paragraphs.append([])
                    if part:
                        new_run = {"text": part}
                        if run.get("marks"):
                            new_run["marks"] = run["marks"]
                        paragraphs[-1].append(new_run)

        for node in pending:
            if isinstance(node, NavigableString):
                add_runs([{"text": str(node)}])
            else:
                add_runs(collect_runs(node, []))
        for runs in paragraphs:
            emit_paragraph(runs)

    def walk(node):
        pending = []
        for child in node.children:
            if isinstance(child, (NavigableString, Tag)) and (
                isinstance(child, NavigableString) or child.name in INLINE_TAGS
            ):
                pending.append(child)
                continue
            flush_pending(pending)
            pending = []
            if not isinstance(child, Tag):
                continue

            name = child.name
            if name in HEADING_LEVELS:
                runs = collect_runs(child, [])
                text = runs_text(runs)
                if text:
                    blocks.append({
                        "type": "heading",
                        "text": text,
                        "level": HEADING_LEVELS[name],
                        "content": runs,
                    })

            elif name == "p":
                non_inline = [
                    c for c in child.children
                    if isinstance(c, Tag) and c.name in BLOCK_TAGS - {"img", "br"}
                ]
                if non_inline:
                    blocks.append({"type": "html", "html": sanitize_html(str(child))})
                    continue
                for img in child.find_all("img"):
                    block = image_block(img)
                    if block:
                        blocks.append(block)
                emit_paragraph(collect_runs(child, []))

            elif name in ("ul", "ol"):
                items = []
                for li in child.find_all("li", recursive=False):
                    runs = collect_runs(li, [])
                    items.append(runs if runs else [{"text": li.get_text()}])
                if items:
                    blocks.append({
                        "type": "list",
                        "items": items,
                        "ordered": name == "ol",
                    })

            elif name == "table":
                blocks.append(table_block(child))

            elif name == "pre":
                code = child.find("code")
                language = None
                if code is not None:
                    for cls in code.get("class") or []:
                        if cls.startswith("language-"):
                            language = cls.split("-", 1)[1]
                text = (code or child).get_text()
                if text.strip():
                    blocks.append({"type": "code", "text": text, "language": language})

            elif name == "blockquote":
                runs = collect_runs(child, [])
                text = runs_text(runs)
                if text:
                    blocks.append({"type": "quote", "text": text, "content": runs})

            elif name == "img":
                block = image_block(child)
                if block:
                    blocks.append(block)

            elif name == "figure":
                img = child.find("img")
                caption = child.find("figcaption")
                if img is not None:
                    block = image_block(
                        img, fallback_alt=caption.get_text().strip() if caption else ""
                    )
                    if block:
                        blocks.append(block)
                else:
                    blocks.append({"type": "html", "html": sanitize_html(str(child))})

            elif name == "iframe":
                blocks.append({"type": "html", "html": sanitize_html(str(child))})

            elif name == "hr":
                blocks.append({"type": "hr"})

            elif name == "div":
                if has_block_child(child):
                    walk(child)
                else:
                    for img in child.find_all("img"):
                        block = image_block(img)
                        if block:
                            blocks.append(block)
                    emit_paragraph(collect_runs(child, []))

            elif name in MARK_TAGS or name == "a":
                emit_paragraph(collect_runs(child, []))

            else:
                blocks.append({"type": "html", "html": sanitize_html(str(child))})

        flush_pending(pending)

    walk(soup)
    return {"blocks": blocks}


def is_broken(content_json):
    if not isinstance(content_json, dict):
        return True
    blocks = content_json.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        return True
    for block in blocks:
        if not isinstance(block, dict):
            return True
        if block.get("type") == "paragraph":
            text = (block.get("text") or "").strip()
            if not text and not block.get("content"):
                return True
        if block.get("type") == "html" and len(block.get("html") or "") < 60:
            return True
    return False


async def rebuild_post(db, post):
    doc = convert(post.content_html)
    post.content_json = doc
    post.reading_time_minutes = reading_time_minutes(doc)
    return len(doc["blocks"])


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    async with AsyncSessionLocal() as db:
        if args.slug:
            post = await db.scalar(select(Post).where(Post.slug == args.slug))
            if post is None:
                print(f"post not found: {args.slug}")
                return
            count = await rebuild_post(db, post)
            await db.commit()
            await cache_service.delete_pattern("posts:*")
            print(f"rebuilt {post.slug}: {count} blocks")
            return

        posts = (await db.scalars(select(Post).order_by(Post.id))).all()
        broken = [p for p in posts if is_broken(p.content_json)]
        print(f"total posts: {len(posts)} | broken content_json: {len(broken)}")

        if args.dry_run:
            for p in broken[:10]:
                print(" -", p.slug)
            return

        if not args.all:
            print("use --all to rebuild, or --slug for a single post")
            return

        fixed = 0
        for index, post in enumerate(broken, 1):
            await rebuild_post(db, post)
            fixed += 1
            if index % 50 == 0:
                await db.commit()
                print(f"progress: {index}/{len(broken)}")
        await db.commit()
        await cache_service.delete_pattern("posts:*")
        print(f"rebuilt {fixed} posts")


if __name__ == "__main__":
    asyncio.run(main())
