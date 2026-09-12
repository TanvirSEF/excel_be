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


def _collect_marked(node):
    marks = []
    if node.name in MARK_TAGS:
        marks.append({"type": MARK_TAGS[node.name]})
    elif node.name == "a":
        href = (node.get("href") or "").strip()
        if href:
            marks.append({"type": "link", "href": href})
    return collect_runs(node, marks)


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
                elif has_block_child(child):
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
