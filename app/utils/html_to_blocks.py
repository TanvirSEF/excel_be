"""Convert sanitized article HTML into the structured blocks document.

Shared by the WP importers and the content rebuild script. Produces blocks
with inline runs and marks so formatting (bold, links, highlights) survives
the conversion, and maps div[data-callout] wrappers to callout blocks.
"""
import re
from bs4 import BeautifulSoup, NavigableString, Tag

from app.utils.sanitize import sanitize_html

MARK_TAGS = {
    "strong": "bold",
    "b": "bold",
    "em": "italic",
    "i": "italic",
    "u": "underline",
    "ins": "underline",
    "sup": "sup",
    "sub": "sub",
    "del": "strike",
    "s": "strike",
    "strike": "strike",
    "code": "code",
    "kbd": "kbd",
    "mark": "highlight",
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
CALLOUT_VARIANTS = {"info", "tip", "warning", "danger"}

VIDEO_URL_PATTERN = re.compile(
    r"^(?:https?://)?(?:www\.)?(?:youtube\.com/(?:watch\?v=|embed/|shorts/)|youtu\.be/|vimeo\.com/|player\.vimeo\.com/video/)[\w-]+",
    re.IGNORECASE,
)


def extract_video_url(text: str, runs: list) -> str | None:
    trimmed = (text or "").strip()
    if not trimmed:
        return None

    if " " not in trimmed and "\n" not in trimmed and VIDEO_URL_PATTERN.match(trimmed):
        if not trimmed.startswith("http"):
            trimmed = "https://" + trimmed
        elif trimmed.startswith("http://"):
            trimmed = "https://" + trimmed[7:]
        return trimmed

    if runs:
        meaningful_runs = [r for r in runs if (r.get("text") or "").strip()]
        if len(meaningful_runs) == 1:
            r = meaningful_runs[0]
            run_text = (r.get("text") or "").strip()
            for mark in r.get("marks") or []:
                if mark.get("type") == "link":
                    href = (mark.get("href") or "").strip()
                    if VIDEO_URL_PATTERN.match(href) and (href == run_text or VIDEO_URL_PATTERN.match(run_text)):
                        if not href.startswith("http"):
                            href = "https://" + href
                        elif href.startswith("http://"):
                            href = "https://" + href[7:]
                        return href

    return None


def collect_runs(node, marks):
    if isinstance(node, NavigableString):
        text = str(node)
        if not text:
            return []
        run = {"text": text}
        if marks:
            run["marks"] = list(marks)
        return [run]

    if not isinstance(node, Tag):
        return []

    if node.name == "br":
        return [{"text": "\n"}]

    node_marks = list(marks)
    if node.name in MARK_TAGS:
        mark_type = MARK_TAGS[node.name]
        if not any(m.get("type") == mark_type for m in node_marks):
            node_marks.append({"type": mark_type})
    elif node.name == "a":
        href = (node.get("href") or "").strip()
        if href and not any(m.get("type") == "link" for m in node_marks):
            node_marks.append({"type": "link", "href": href})

    runs = []
    for child in node.children:
        runs.extend(collect_runs(child, node_marks))
    return runs


def runs_text(runs):
    return "".join(r["text"] for r in runs).strip()


def has_block_child(el):
    return any(
        isinstance(c, Tag) and c.name in BLOCK_TAGS
        for c in el.children
    )


def _img_dimension(img, attr):
    try:
        value = int((img.get(attr) or "").strip())
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def image_block(img, fallback_alt=""):
    src = (img.get("src") or "").strip()
    if not src:
        return None
    block = {"type": "image", "url": src, "alt": img.get("alt") or fallback_alt}
    width = _img_dimension(img, "width")
    if width is not None:
        block["width"] = width
    height = _img_dimension(img, "height")
    if height is not None:
        block["height"] = height
    return block


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


def _collect_marked(node):
    return collect_runs(node, [])


def _callout_parts(div):
    runs = []
    images = []
    for child in div.children:
        if isinstance(child, NavigableString):
            text = str(child)
            if text:
                runs.append({"text": text})
        elif isinstance(child, Tag):
            if child.name == "img":
                images.append(child)
            elif child.name == "br":
                runs.append({"text": "\n"})
            elif child.name == "p" or child.name in HEADING_LEVELS:
                part = collect_runs(child, [])
                if part:
                    if runs and runs[-1]["text"].strip():
                        runs.append({"text": "\n"})
                    runs.extend(part)
            else:
                runs.extend(_collect_marked(child))
    return runs, images


def convert(html):
    soup = BeautifulSoup(html or "", "html.parser")
    blocks = []

    def emit_paragraph(runs):
        text = runs_text(runs)
        if not text:
            return
        video_url = extract_video_url(text, runs)
        if video_url:
            blocks.append({"type": "embed", "url": video_url})
            return
        blocks.append({"type": "paragraph", "text": text, "content": runs})

    def flush_pending(pending):
        if not pending:
            return
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
            if isinstance(node, Tag):
                for img in node.find_all("img"):
                    b = image_block(img)
                    if b:
                        blocks.append(b)

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
                isinstance(child, NavigableString) or (child.name in INLINE_TAGS and not child.find("img"))
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
                    num = (child.get("data-numhead") or "").strip()
                    if not num:
                        m = re.match(r"^(\d{1,3})[\.\)]\s*(.*)$", text)
                        if m:
                            num = m.group(1)
                            text = m.group(2)
                            if runs and runs[0].get("text"):
                                runs[0]["text"] = re.sub(r"^\d{1,3}[\.\)]\s*", "", runs[0]["text"])
                    block = {
                        "type": "heading",
                        "text": text,
                        "level": HEADING_LEVELS[name],
                        "content": runs,
                    }
                    if num:
                        block["num"] = num[:10]
                    blocks.append(block)

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

            elif name in INLINE_TAGS and child.find("img"):
                for img in child.find_all("img"):
                    block = image_block(img)
                    if block:
                        blocks.append(block)
                runs = collect_runs(child, [])
                emit_paragraph(runs)

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
                    iframe = child.find("iframe")
                    iframe_src = (iframe.get("src") or "").strip() if iframe else ""
                    video_url = extract_video_url(child.get_text(), []) or (iframe_src if VIDEO_URL_PATTERN.match(iframe_src) else None)
                    if video_url:
                        embed_block = {"type": "embed", "url": video_url}
                        if caption:
                            cap = caption.get_text().strip()
                            if cap:
                                embed_block["caption"] = cap
                        blocks.append(embed_block)
                    else:
                        blocks.append({"type": "html", "html": sanitize_html(str(child))})

            elif name == "iframe":
                src = (child.get("src") or "").strip()
                if VIDEO_URL_PATTERN.match(src):
                    blocks.append({"type": "embed", "url": src})
                else:
                    blocks.append({"type": "html", "html": sanitize_html(str(child))})

            elif name == "hr":
                blocks.append({"type": "hr"})

            elif name == "div":
                btn = child.find("a", attrs={"data-button": True}) if not has_block_child(child) else None
                if btn is not None:
                    href = (btn.get("href") or "#").strip()
                    label = btn.get_text().strip() or "Download"
                    blocks.append({
                        "type": "button",
                        "label": label,
                        "href": href,
                        "variant": btn.get("data-variant") or "primary",
                    })
                elif child.get("data-embed") is not None:
                    url = (child.get("data-url") or "").strip()
                    if url and VIDEO_URL_PATTERN.match(url):
                        caption = (child.get("data-caption") or "").strip()
                        embed_block = {"type": "embed", "url": url}
                        if caption:
                            embed_block["caption"] = caption
                        blocks.append(embed_block)
                        continue
                elif child.get("data-callout") is not None:
                    runs, images = _callout_parts(child)
                    text = runs_text(runs)
                    if not text:
                        continue
                    variant = child.get("data-variant")
                    if variant not in CALLOUT_VARIANTS:
                        variant = "info"
                    block = {
                        "type": "callout",
                        "variant": variant,
                        "text": text,
                        "content": runs,
                    }
                    title = (child.get("data-title") or "").strip()
                    if title:
                        block["title"] = title[:200]
                    blocks.append(block)
                    for img in images:
                        image = image_block(img)
                        if image:
                            blocks.append(image)
                elif has_block_child(child) or child.find("img"):
                    walk(child)
                else:
                    for img in child.find_all("img"):
                        block = image_block(img)
                        if block:
                            blocks.append(block)
                    emit_paragraph(collect_runs(child, []))

            elif name == "a" and child.get("data-button") is not None:
                href = (child.get("href") or "#").strip()
                label = child.get_text().strip() or "Download"
                blocks.append({
                    "type": "button",
                    "label": label,
                    "href": href,
                    "variant": child.get("data-variant") or "primary",
                })

            elif name in MARK_TAGS or name == "a":
                for img in child.find_all("img"):
                    block = image_block(img)
                    if block:
                        blocks.append(block)
                emit_paragraph(collect_runs(child, []))

            else:
                for img in child.find_all("img"):
                    block = image_block(img)
                    if block:
                        blocks.append(block)
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
            if extract_video_url(text, block.get("content") or []):
                return True
        if block.get("type") == "html" and len(block.get("html") or "") < 60:
            return True
    return False
