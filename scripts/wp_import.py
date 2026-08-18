import argparse
import asyncio
import html as html_lib
import logging
import re
import sys
from hashlib import sha256
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import httpx
from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal, engine
from app.models import (
    Category,
    Comment,
    CommentStatus,
    Media,
    Post,
    PostStatus,
    Redirect,
    User,
)
from app.services import tag_service
from app.services.media_service import process_image, upload_to_r2
from app.utils.reading_time import reading_time_minutes
from app.utils.slugify import slugify

logging.getLogger("httpx").setLevel(logging.WARNING)

CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}"
EXCERPT_NS = "{http://purl.org/rss/1.0/modules/excerpt/}"

STATUS_MAP = {
    "publish": PostStatus.published,
    "draft": PostStatus.draft,
    "pending": PostStatus.pending_review,
    "private": PostStatus.draft,
    "future": PostStatus.scheduled,
}

SEO_META_KEYS = {
    "_yoast_wpseo_title": "meta_title",
    "rank_math_title": "meta_title",
    "_yoast_wpseo_metadesc": "meta_description",
    "rank_math_description": "meta_description",
}

MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024


@dataclass
class WxrCategory:
    wp_id: str
    slug: str
    name: str
    description: str
    parent_slug: str


@dataclass
class WxrPost:
    wp_id: str
    title: str
    slug: str
    link: str
    content: str
    excerpt: str
    date_gmt: datetime | None
    modified_gmt: datetime | None
    status: str
    author_login: str
    category_slug: str | None
    tag_names: list[str]
    metas: dict[str, str]
    thumbnail_id: str | None
    comments: list[dict]


@dataclass
class WxrAttachment:
    wp_id: str
    url: str
    title: str
    alt: str


@dataclass
class WxrFile:
    site_title: str
    categories: list[WxrCategory]
    posts: list[WxrPost]
    attachments: list[WxrAttachment]
    skipped: dict[str, int] = field(default_factory=dict)
    distinct_authors: set[str] = field(default_factory=set)


def parse_wp_date(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value or value.startswith("0000"):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_wxr(path: str) -> WxrFile:
    tree = ET.parse(path)
    channel = tree.getroot().find("channel")
    if channel is None:
        sys.exit("Not a valid WXR file: no channel element")

    wxr = WxrFile(
        site_title=(channel.findtext("title") or "").strip(),
        categories=[],
        posts=[],
        attachments=[],
        skipped={},
    )
    wxr.skipped.setdefault("page", 0)
    wxr.skipped.setdefault("trash", 0)
    wxr.skipped.setdefault("other_post_type", 0)
    wxr.skipped.setdefault("unknown_status", 0)

    for term in channel.findall("{*}term"):
        taxonomy = term.findtext("{*}term_taxonomy") or ""
        if taxonomy != "category":
            continue
        wxr.categories.append(_parse_term(term))
    for cat in channel.findall("{*}category"):
        slug = cat.findtext("{*}category_nicename") or ""
        if slug and slug not in {c.slug for c in wxr.categories}:
            wxr.categories.append(
                WxrCategory(
                    wp_id=cat.findtext("{*}term_id") or "",
                    slug=slug,
                    name=(cat.findtext("{*}cat_name") or slug).strip(),
                    description=cat.findtext("{*}category_description") or "",
                    parent_slug=cat.findtext("{*}category_parent") or "",
                )
            )

    for item in channel.findall("item"):
        post_type = item.findtext("{*}post_type") or ""
        if post_type == "attachment":
            wxr.attachments.append(_parse_attachment(item))
            continue

        status = (item.findtext("{*}status") or "").strip()
        if post_type != "post":
            wxr.skipped["page" if post_type == "page" else "other_post_type"] += 1
            continue
        if status == "trash":
            wxr.skipped["trash"] += 1
            continue
        if status not in STATUS_MAP:
            wxr.skipped["unknown_status"] += 1
            continue

        wxr.posts.append(_parse_post(item, status, wxr))

    return wxr


def _parse_term(term) -> WxrCategory:
    return WxrCategory(
        wp_id=term.findtext("{*}term_id") or "",
        slug=term.findtext("{*}term_slug") or "",
        name=(term.findtext("{*}term_name") or "").strip(),
        description=term.findtext("{*}term_description") or "",
        parent_slug=term.findtext("{*}term_parent") or "",
    )


def _parse_attachment(item) -> WxrAttachment:
    alt = ""
    title = (item.findtext("title") or "").strip()
    for meta in item.findall("{*}postmeta"):
        if meta.findtext("{*}meta_key") == "_wp_attachment_image_alt":
            alt = meta.findtext("{*}meta_value") or ""
    return WxrAttachment(
        wp_id=item.findtext("{*}post_id") or "",
        url=item.findtext("{*}attachment_url") or "",
        title=title,
        alt=alt or title,
    )


def _parse_post(item, status: str, wxr: WxrFile) -> WxrPost:
    metas: dict[str, str] = {}
    for meta in item.findall("{*}postmeta"):
        key = meta.findtext("{*}meta_key") or ""
        if key:
            metas[key] = meta.findtext("{*}meta_value") or ""

    category_slug = None
    tag_names: list[str] = []
    for cat in item.findall("category"):
        domain = cat.get("domain", "")
        text = (cat.text or "").strip()
        if domain == "category" and text and text.lower() != "uncategorized":
            category_slug = cat.get("nicename") or slugify(text)
        elif domain == "post_tag" and text:
            tag_names.append(text)

    comments = []
    for comment in item.findall("{*}comment"):
        approved = (comment.findtext("{*}comment_approved") or "").strip()
        ctype = (comment.findtext("{*}comment_type") or "").strip()
        if approved in ("spam", "trash") or ctype in ("trackback", "pingback"):
            wxr.skipped["comments_spam"] = wxr.skipped.get("comments_spam", 0) + 1
            continue
        comments.append(
            {
                "wp_id": comment.findtext("{*}comment_id") or "",
                "author": (comment.findtext("{*}comment_author") or "Anonymous").strip(),
                "email": (comment.findtext("{*}comment_author_email") or "").strip(),
                "content": comment.findtext("{*}comment_content") or "",
                "date_gmt": parse_wp_date(comment.findtext("{*}comment_date_gmt") or ""),
                "approved": approved == "1",
                "parent_wp_id": (comment.findtext("{*}comment_parent") or "0").strip(),
            }
        )

    author_login = (item.findtext("{*}creator") or "").strip()
    wxr.distinct_authors.add(author_login)

    title = (item.findtext("title") or "").strip() or "Untitled"
    slug = (item.findtext("{*}post_name") or "").strip() or slugify(title)

    return WxrPost(
        wp_id=item.findtext("{*}post_id") or "",
        title=title,
        slug=slug,
        link=(item.findtext("link") or "").strip(),
        content=item.findtext(f"{CONTENT_NS}encoded") or "",
        excerpt=(item.findtext(f"{EXCERPT_NS}encoded") or "").strip(),
        date_gmt=parse_wp_date(item.findtext("{*}post_date_gmt") or ""),
        modified_gmt=parse_wp_date(item.findtext("{*}post_modified_gmt") or ""),
        status=status,
        author_login=author_login,
        category_slug=category_slug,
        tag_names=tag_names,
        metas=metas,
        thumbnail_id=metas.get("_thumbnail_id", "").strip(),
        comments=comments,
    )


VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
DROP_TAGS = {"script", "style"}


class _TopLevelSplitter(HTMLParser):
    def __init__(self, src: str):
        super().__init__(convert_charrefs=False)
        self.src = src
        self.line_starts = [0]
        for i, ch in enumerate(src):
            if ch == "\n":
                self.line_starts.append(i + 1)
        self.depth = 0
        self.drop_depth = 0
        self.start: int | None = None
        self.spans: list[tuple[int, int]] = []

    def _offset(self) -> int:
        line, col = self.getpos()
        return self.line_starts[line - 1] + col

    def _tag_end(self, start: int) -> int:
        return self.src.index(">", start) + 1

    def handle_starttag(self, tag, attrs):
        if self.depth == 0 and tag in DROP_TAGS:
            self.drop_depth += 1
            return
        if self.drop_depth:
            return
        if tag in VOID_TAGS:
            if self.depth == 0:
                start = self._offset()
                self.spans.append((start, self._tag_end(start)))
            return
        if self.depth == 0:
            self.start = self._offset()
        self.depth += 1

    def handle_startendtag(self, tag, attrs):
        if self.depth == 0 and not self.drop_depth:
            start = self._offset()
            self.spans.append((start, self._tag_end(start)))

    def handle_endtag(self, tag):
        if tag in DROP_TAGS and self.drop_depth:
            self.drop_depth -= 1
            return
        if self.drop_depth or tag in VOID_TAGS:
            return
        if self.depth > 0:
            self.depth -= 1
            if self.depth == 0 and self.start is not None:
                end = self._offset() + len(f"</{tag}>")
                self.spans.append((self.start, end))
                self.start = None

    def handle_data(self, data):
        if self.depth == 0 and not self.drop_depth and data.strip():
            start = self._offset()
            self.spans.append((start, start + len(data)))

    def close(self):
        super().close()
        if self.start is not None and self.depth > 0:
            self.spans.append((self.start, len(self.src)))
            self.start = None


OPEN_TAG_RE = re.compile(r"<([a-zA-Z0-9]+)")
MARKUP_RE = re.compile(r"<[a-zA-Z!/]")
LI_RE = re.compile(r"<li[^>]*>(.*?)</li>", re.DOTALL | re.IGNORECASE)


class _TextCollector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data):
        self.parts.append(data)


def _strip_tags(fragment: str) -> str:
    collector = _TextCollector()
    collector.feed(fragment)
    return html_lib.unescape("".join(collector.parts)).strip()


def _inner(raw: str) -> str:
    first = raw.find(">")
    last = raw.rfind("<")
    if first == -1 or last <= first:
        return ""
    return raw[first + 1 : last]


def html_to_blocks(content: str) -> list[dict]:
    splitter = _TopLevelSplitter(content)
    splitter.feed(content)
    splitter.close()

    blocks = []
    for start, end in sorted(splitter.spans):
        raw = content[start:end].strip()
        if not raw:
            continue
        blocks.append(_to_block(raw))
    return blocks


def _to_block(raw: str) -> dict:
    match = OPEN_TAG_RE.match(raw)
    tag = match.group(1).lower() if match else ""
    inner = _inner(raw)

    if tag == "p" and not MARKUP_RE.search(inner):
        text = html_lib.unescape(inner).strip()
        if text:
            return {"type": "paragraph", "text": text}
        return {"type": "html", "html": raw}

    if tag in ("h1", "h2", "h3", "h4", "h5", "h6") and not MARKUP_RE.search(inner):
        return {"type": "heading", "text": html_lib.unescape(inner).strip(), "level": int(tag[1])}

    if tag == "blockquote" and not MARKUP_RE.search(inner):
        return {"type": "quote", "text": html_lib.unescape(inner).strip()}

    if tag == "pre" and not MARKUP_RE.search(inner):
        return {"type": "code", "text": html_lib.unescape(inner).rstrip()}

    if tag in ("ul", "ol"):
        items = [html_lib.unescape(m).strip() for m in LI_RE.findall(raw)]
        if items and not any(MARKUP_RE.search(i) for i in items):
            return {"type": "list", "items": items, "ordered": tag == "ol"}

    if not tag and not MARKUP_RE.search(raw):
        text = raw.strip()
        if text:
            return {"type": "paragraph", "text": text}

    return {"type": "html", "html": raw}


class MediaPipeline:
    def __init__(self, db, author: User, enabled: bool):
        self.db = db
        self.author = author
        self.enabled = enabled
        self.cache: dict[str, str] = {}
        self.uploaded = 0
        self.failed: list[str] = []

    async def remap(self, url: str, alt: str = "") -> str:
        if not self.enabled or not url:
            return url
        if url in self.cache:
            return self.cache[url]
        if url.startswith("data:") or not url.startswith(("http://", "https://")):
            return url
        if settings.r2_public_url and url.startswith(settings.r2_public_url.rstrip("/")):
            self.cache[url] = url
            return url

        new_url = await self._upload(url, alt)
        self.cache[url] = new_url
        return new_url

    async def _upload(self, url: str, alt: str) -> str:
        try:
            basename = urlparse(url).path.split("/")[-1] or "wp"
            digest = sha256(url.encode()).hexdigest()[:12]
            key = f"wp-import/{digest}-{basename}.webp"
            new_url = f"{settings.r2_public_url.rstrip('/')}/{key}"

            existing = await self.db.scalar(select(Media).where(Media.file_url == new_url))
            if existing is not None:
                self.uploaded += 1
                return new_url

            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                head = await client.head(url)
                length = int(head.headers.get("content-length") or 0)
                if length > MAX_DOWNLOAD_BYTES:
                    raise ValueError("too large")
                response = await client.get(url)
                data = response.content
            if len(data) > MAX_DOWNLOAD_BYTES:
                raise ValueError("too large")
            processed = process_image(data)
            upload_to_r2(key, processed.data)
            self.db.add(
                Media(
                    uploader_id=self.author.id,
                    file_url=new_url,
                    file_type="image",
                    alt_text=alt or "Imported from WordPress",
                    width=processed.width,
                    height=processed.height,
                    size_kb=len(processed.data) // 1024,
                    folder="wp-import",
                )
            )
            await self.db.flush()
            self.uploaded += 1
            return new_url
        except Exception:
            self.failed.append(url)
            return url


SRCSET_RE = re.compile(r'\s(?:srcset|sizes)="[^"]*"')
IMG_SRC_RE = re.compile(r'<img[^>]+src="([^"]+)"')
SCRIPT_BLOCK_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.DOTALL | re.IGNORECASE)


async def import_posts(db, wxr: WxrFile, media: MediaPipeline, limit: int | None) -> dict:
    stats = {"created": 0, "updated": 0, "comments": 0, "redirects": 0}
    posts = wxr.posts[:limit] if limit else wxr.posts

    for index, wp_post in enumerate(posts, start=1):
        content_html = wp_post.content
        if media.enabled:
            for img_url in {m for m in IMG_SRC_RE.findall(content_html)}:
                new_url = await media.remap(img_url)
                if new_url != img_url:
                    content_html = content_html.replace(img_url, new_url)
            content_html = SRCSET_RE.sub("", content_html)
        content_html = SCRIPT_BLOCK_RE.sub("", content_html)

        featured_url = None
        if wp_post.thumbnail_id:
            attachment = next((a for a in wxr.attachments if a.wp_id == wp_post.thumbnail_id), None)
            if attachment and attachment.url:
                featured_url = await media.remap(attachment.url, attachment.alt)

        content_json = {"blocks": html_to_blocks(content_html)}
        seo: dict[str, str] = {}
        for key, value in wp_post.metas.items():
            if key in SEO_META_KEYS and value.strip():
                seo[SEO_META_KEYS[key]] = value.strip()

        post = await db.scalar(select(Post).where(Post.slug == wp_post.slug))
        created = post is None
        if post is None:
            post = Post(slug=wp_post.slug, author_id=media.author.id, content_json=content_json)
            db.add(post)
            stats["created"] += 1
        else:
            stats["updated"] += 1

        post.title = wp_post.title
        post.excerpt = wp_post.excerpt[:500] or None
        post.content_json = content_json
        post.content_html = content_html
        post.featured_image_url = featured_url
        post.status = STATUS_MAP[wp_post.status]
        post.reading_time_minutes = reading_time_minutes(content_json)
        post.meta_title = seo.get("meta_title", "")[:255] or None
        post.meta_description = seo.get("meta_description", "")[:500] or None
        post.published_at = wp_post.date_gmt
        post.scheduled_at = wp_post.date_gmt if wp_post.status == "future" else None
        if wp_post.category_slug:
            category = await db.scalar(select(Category).where(Category.slug == wp_post.category_slug))
            post.category_id = category.id if category else None
        else:
            post.category_id = None

        await db.commit()

        comment_map: dict[str, Comment] = {}
        if created:
            for wp_comment in wp_post.comments:
                comment = Comment(
                    post_id=post.id,
                    user_name=wp_comment["author"][:100] or "Anonymous",
                    user_email=wp_comment["email"][:255] or "unknown@import.local",
                    comment_text=wp_comment["content"],
                    status=CommentStatus.approved if wp_comment["approved"] else CommentStatus.pending,
                    created_at=wp_comment["date_gmt"] or datetime.now(timezone.utc),
                )
                parent = comment_map.get(wp_comment["parent_wp_id"])
                if parent:
                    comment.parent_id = parent.id
                db.add(comment)
                await db.flush()
                comment_map[wp_comment["wp_id"]] = comment
                stats["comments"] += 1
            if wp_post.comments:
                await db.commit()
        elif wp_post.comments:
            stats["comments_skipped_reimport"] = stats.get("comments_skipped_reimport", 0) + len(wp_post.comments)

        if wp_post.tag_names:
            await tag_service.sync_post_tags(db, post, wp_post.tag_names)

        old_path = urlparse(wp_post.link).path.rstrip("/") if wp_post.link else ""
        if old_path and old_path.lstrip("/") and old_path != f"/{wp_post.slug}":
            exists = await db.scalar(select(Redirect).where(Redirect.old_path == old_path.lstrip("/")))
            if exists is None:
                db.add(Redirect(old_path=old_path.lstrip("/"), new_path=f"/blog/{wp_post.slug}"))
                await db.commit()
                stats["redirects"] += 1

        if index % 10 == 0 or index == len(posts):
            print(f"  posts {index}/{len(posts)}", flush=True)

    return stats


async def import_categories(db, wxr: WxrFile) -> dict:
    stats = {"created": 0, "updated": 0}
    by_slug: dict[str, Category] = {}
    pending = {c.slug: c for c in wxr.categories}

    async def ensure(slug: str):
        wp_cat = pending.pop(slug, None)
        if wp_cat is None:
            return by_slug.get(slug)

        if wp_cat.parent_slug and wp_cat.parent_slug != slug and wp_cat.parent_slug in pending:
            await ensure(wp_cat.parent_slug)

        category = by_slug.get(slug) or await db.scalar(select(Category).where(Category.slug == slug))
        if category is None:
            category = Category(name=wp_cat.name, slug=wp_cat.slug)
            db.add(category)
            stats["created"] += 1
        else:
            stats["updated"] += 1
        category.name = wp_cat.name
        category.description = wp_cat.description or None
        parent = by_slug.get(wp_cat.parent_slug) if wp_cat.parent_slug else None
        category.parent_id = parent.id if parent else None
        await db.flush()
        by_slug[slug] = category
        return category

    for slug in list(pending):
        await ensure(slug)
    await db.commit()
    return stats


def print_report(wxr: WxrFile, limit: int | None = None):
    published = sum(1 for p in wxr.posts if p.status == "publish")
    scheduled = sum(1 for p in wxr.posts if p.status == "future")
    drafts = sum(1 for p in wxr.posts if p.status in ("draft", "pending", "private"))

    print("\n=== WXR parse report ===")
    print(f"site: {wxr.site_title!r}")
    print(f"posts total: {len(wxr.posts)} (published {published}, scheduled {scheduled}, draft/pending/private {drafts})")
    print(f"categories: {len(wxr.categories)}")
    print(f"attachments: {len(wxr.attachments)}")
    print(f"comments: {sum(len(p.comments) for p in wxr.posts)} (skipped spam/trackback: {wxr.skipped.get('comments_spam', 0)})")
    print(f"skipped: {wxr.skipped}")
    print(f"distinct WP authors: {sorted(a for a in wxr.distinct_authors if a) or ['(none)']}")
    print(f"posts with content: {sum(1 for p in wxr.posts if p.content.strip())}/{len(wxr.posts)}")
    print(f"posts with category: {sum(1 for p in wxr.posts if p.category_slug)}/{len(wxr.posts)}")
    print(f"posts with featured image: {sum(1 for p in wxr.posts if p.thumbnail_id)}/{len(wxr.posts)}")
    print(f"posts with SEO meta: {sum(1 for p in wxr.posts if any(k in p.metas for k in SEO_META_KEYS))}/{len(wxr.posts)}")

    if wxr.posts:
        dates = [p.date_gmt for p in wxr.posts if p.date_gmt]
        if dates:
            print(f"date range: {min(dates):%Y-%m-%d} .. {max(dates):%Y-%m-%d}")
    if limit:
        print(f"NOTE: --limit {limit} active, only first {limit} posts will import")


async def main():
    parser = argparse.ArgumentParser(description="Import WordPress WXR export into the database")
    parser.add_argument("--file", required=True, help="path to WXR .xml export")
    parser.add_argument("--dry-run", action="store_true", help="parse and report only, no writes")
    parser.add_argument("--author-email", help="backend user email that owns imported posts")
    parser.add_argument("--no-images", action="store_true", help="skip image download/R2 upload")
    parser.add_argument("--limit", type=int, help="import only first N posts")
    args = parser.parse_args()

    if not Path(args.file).exists():
        sys.exit(f"file not found: {args.file}")
    if not args.dry_run and not args.author_email:
        sys.exit("--author-email is required unless --dry-run")

    wxr = parse_wxr(args.file)
    print_report(wxr, args.limit)
    if args.dry_run:
        print("\ndry run complete, nothing written")
        return

    async with AsyncSessionLocal() as db:
        author = await db.scalar(select(User).where(User.email == args.author_email))
        if author is None:
            sys.exit(f"author email not found in database: {args.author_email}")

        cat_stats = await import_categories(db, wxr)
        print(f"\ncategories: {cat_stats}")

        media = MediaPipeline(db, author, enabled=not args.no_images)
        stats = await import_posts(db, wxr, media, args.limit)

        print(f"\nposts: created {stats['created']}, updated {stats['updated']}")
        print(f"comments imported: {stats['comments']}, skipped on update: {stats.get('comments_skipped_reimport', 0)}")
        print(f"redirects created: {stats['redirects']}")
        print(f"images uploaded to R2: {media.uploaded}, failed (kept original URL): {len(media.failed)}")
        for url in media.failed[:10]:
            print(f"  failed image: {url}")

    await engine.dispose()
    print("\nimport complete")


if __name__ == "__main__":
    asyncio.run(main())
