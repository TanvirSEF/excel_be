import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
from app.utils.html_to_blocks import convert
from app.utils.reading_time import reading_time_minutes
from app.utils.sanitize import sanitize_html
from app.utils.shortcodes import expand_shortcodes
from app.utils.slugify import slugify

CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}"
EXCERPT_NS = "{http://purl.org/rss/1.0/modules/excerpt/}"
IMG_SRC_RE = re.compile(r'src=["\x27]([^"\x27]+)["\x27]')
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024

STATUS_MAP: dict[str, PostStatus] = {
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


@dataclass
class WxrCategory:
    wp_id: str
    slug: str
    name: str
    description: str
    parent_slug: str


@dataclass
class WxrAttachment:
    wp_id: str
    url: str
    alt: str


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
    thumbnail_id: str | None
    metas: dict[str, str]
    comments: list[dict]


@dataclass
class WxrFile:
    site_title: str
    posts: list[WxrPost] = field(default_factory=list)
    categories: list[WxrCategory] = field(default_factory=list)
    attachments: list[WxrAttachment] = field(default_factory=list)
    distinct_authors: set[str] = field(default_factory=set)
    skipped: dict[str, int] = field(default_factory=dict)


@dataclass
class ImportResult:
    dry_run: bool
    posts_created: int
    posts_updated: int
    categories: int
    tags: int
    redirects: int
    images_uploaded: int
    images_failed: int
    site_title: str
    total_posts: int


def _parse_wp_date(value: str) -> datetime | None:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _parse_term(term) -> WxrCategory:
    ns = "{http://wordpress.org/export/1.2/}"
    return WxrCategory(
        wp_id=term.findtext(f"{ns}term_id", ""),
        slug=term.findtext(f"{ns}term_slug", ""),
        name=term.findtext(f"{ns}term_name", ""),
        description=term.findtext(f"{ns}term_description", ""),
        parent_slug=term.findtext(f"{ns}term_parent", ""),
    )


def _parse_attachment(item) -> WxrAttachment:
    ns = "{http://wordpress.org/export/1.2/}"
    wp_id = item.findtext(f"{ns}post_id", "")
    url = item.findtext(f"{ns}attachment_url", "") or item.findtext("link", "")
    alt = ""
    for meta in item.findall(f"{ns}postmeta"):
        if meta.findtext(f"{ns}meta_key") == "_wp_attachment_image_alt":
            alt = meta.findtext(f"{ns}meta_value", "")
    return WxrAttachment(wp_id=wp_id, url=url, alt=alt)


def _parse_post(item, status: str, wxr: WxrFile) -> WxrPost:
    ns = "{http://wordpress.org/export/1.2/}"
    title = item.findtext("title", "")
    slug = item.findtext(f"{ns}post_name", "") or slugify(title)
    category_slug: str | None = None
    tag_names: list[str] = []
    for cat in item.findall("category"):
        domain = cat.get("domain", "")
        nicename = cat.get("nicename", "")
        if domain == "category" and nicename:
            category_slug = nicename
        elif domain == "post_tag" and nicename:
            tag_names.append(nicename)

    thumbnail_id: str | None = None
    metas: dict[str, str] = {}
    for meta in item.findall(f"{ns}postmeta"):
        key = meta.findtext(f"{ns}meta_key", "")
        value = meta.findtext(f"{ns}meta_value", "")
        metas[key] = value
        if key == "_thumbnail_id":
            thumbnail_id = value

    comments: list[dict] = []
    for comment in item.findall(f"{ns}comment"):
        c_status = comment.findtext(f"{ns}comment_approved", "0")
        c_type = comment.findtext(f"{ns}comment_type", "")
        if c_type in ("trackback", "pingback") or c_status == "spam":
            wxr.skipped["comments_spam"] = wxr.skipped.get("comments_spam", 0) + 1
            continue
        comments.append({
            "wp_id": comment.findtext(f"{ns}comment_id", ""),
            "parent_wp_id": comment.findtext(f"{ns}comment_parent", "0"),
            "author": comment.findtext(f"{ns}comment_author", "Anonymous"),
            "email": comment.findtext(f"{ns}comment_author_email", ""),
            "content": comment.findtext(f"{ns}comment_content", ""),
            "approved": c_status == "1",
            "date_gmt": _parse_wp_date(comment.findtext(f"{ns}comment_date_gmt", "")),
        })

    author_login = item.findtext(f"{ns}post_author", "")
    wxr.distinct_authors.add(author_login)
    return WxrPost(
        wp_id=item.findtext(f"{ns}post_id", ""),
        title=title,
        slug=slug,
        link=item.findtext("link", ""),
        content=item.findtext(f"{CONTENT_NS}encoded", "") or "",
        excerpt=item.findtext(f"{EXCERPT_NS}encoded", "") or "",
        date_gmt=_parse_wp_date(item.findtext(f"{ns}post_date_gmt", "")),
        modified_gmt=_parse_wp_date(item.findtext(f"{ns}post_modified_gmt", "")),
        status=status,
        author_login=author_login,
        category_slug=category_slug,
        tag_names=tag_names,
        thumbnail_id=thumbnail_id,
        metas=metas,
        comments=comments,
    )


def parse_wxr_bytes(content: bytes) -> WxrFile:
    root = ET.fromstring(content)
    ns = "{http://wordpress.org/export/1.2/}"
    channel = root.find("channel")
    if channel is None:
        raise ValueError("Invalid WXR: missing <channel>")

    wxr = WxrFile(site_title=channel.findtext("title", ""))
    for term in channel.findall(f"{ns}term"):
        if term.findtext(f"{ns}term_taxonomy", "") == "category":
            wxr.categories.append(_parse_term(term))
    for item in channel.findall("item"):
        post_type = item.findtext(f"{ns}post_type", "")
        status = item.findtext(f"{ns}status", "draft")
        if post_type == "attachment":
            wxr.attachments.append(_parse_attachment(item))
        elif post_type == "post" and status in STATUS_MAP:
            wxr.posts.append(_parse_post(item, status, wxr))
        else:
            wxr.skipped["other"] = wxr.skipped.get("other", 0) + 1
    return wxr


class MediaPipeline:
    def __init__(self, db: AsyncSession, author: User, enabled: bool):
        self.db = db
        self.author = author
        self.enabled = enabled
        self.uploaded = 0
        self.failed: list[str] = []
        self._cache: dict[str, str] = {}

    async def remap(self, url: str, alt: str = "") -> str:
        if not self.enabled or not url:
            return url
        if url in self._cache:
            return self._cache[url]
        new_url = await self._upload(url, alt)
        self._cache[url] = new_url
        return new_url

    async def _upload(self, url: str, alt: str) -> str:
        url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
        existing = await self.db.scalar(
            select(Media).where(Media.alt_text == f"wp-import:{url_hash}")
        )
        if existing:
            return existing.file_url
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
                raw = response.content
                if len(raw) > MAX_DOWNLOAD_BYTES:
                    raise ValueError("file too large")
                content_type = response.headers.get("content-type", "image/jpeg")
        except Exception:
            self.failed.append(url)
            return url
        try:
            parsed = urlparse(url)
            filename = parsed.path.split("/")[-1] or "image.jpg"
            image_data, width, height, size_kb = await process_image(raw)
            key = f"wp-import/{filename}"
            r2_url = await upload_to_r2(image_data, key, content_type)
            media = Media(
                file_url=r2_url,
                file_type=content_type,
                alt_text=f"wp-import:{url_hash}",
                width=width,
                height=height,
                size_kb=size_kb,
                folder="wp-import",
            )
            self.db.add(media)
            await self.db.flush()
            self.uploaded += 1
            return r2_url
        except Exception:
            self.failed.append(url)
            return url


async def _import_categories(db: AsyncSession, wxr: WxrFile) -> int:
    by_slug: dict[str, Category] = {}
    pending = {c.slug: c for c in wxr.categories}
    created = 0

    async def ensure(slug: str) -> Category | None:
        nonlocal created
        wp_cat = pending.pop(slug, None)
        if wp_cat is None:
            return by_slug.get(slug)
        if wp_cat.parent_slug and wp_cat.parent_slug != slug and wp_cat.parent_slug in pending:
            await ensure(wp_cat.parent_slug)
        category = by_slug.get(slug) or await db.scalar(select(Category).where(Category.slug == slug))
        if category is None:
            category = Category(name=wp_cat.name, slug=wp_cat.slug)
            db.add(category)
            created += 1
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
    return created


async def _import_posts(db: AsyncSession, wxr: WxrFile, media: MediaPipeline) -> dict[str, int]:
    stats: dict[str, int] = {"created": 0, "updated": 0, "comments": 0, "redirects": 0, "tags": 0}

    for wp_post in wxr.posts:
        content_html = wp_post.content
        if media.enabled:
            for img_url in {m for m in IMG_SRC_RE.findall(content_html)}:
                new_url = await media.remap(img_url)
                if new_url != img_url:
                    content_html = content_html.replace(img_url, new_url)
        content_html = sanitize_html(expand_shortcodes(content_html))

        featured_url: str | None = None
        if wp_post.thumbnail_id:
            attachment = next((a for a in wxr.attachments if a.wp_id == wp_post.thumbnail_id), None)
            if attachment and attachment.url:
                featured_url = await media.remap(attachment.url, attachment.alt)

        content_json = convert(content_html)
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
                db.add(comment)
                comment_map[wp_comment["wp_id"]] = comment
            if wp_post.comments:
                await db.flush()
                for wp_comment in wp_post.comments:
                    parent = comment_map.get(wp_comment["parent_wp_id"])
                    if parent:
                        comment_map[wp_comment["wp_id"]].parent_id = parent.id
                await db.commit()
                stats["comments"] += len(wp_post.comments)

        if wp_post.tag_names:
            await tag_service.sync_post_tags(db, post, wp_post.tag_names)
            await db.commit()
            stats["tags"] += len(wp_post.tag_names)

        old_path = urlparse(wp_post.link).path.rstrip("/") if wp_post.link else ""
        if old_path and old_path.lstrip("/") and old_path != f"/{wp_post.slug}":
            exists = await db.scalar(select(Redirect).where(Redirect.old_path == old_path.lstrip("/")))
            if exists is None:
                db.add(Redirect(old_path=old_path.lstrip("/"), new_path=f"/blog/{wp_post.slug}"))
                await db.commit()
                stats["redirects"] += 1

    return stats


async def run_import(
    db: AsyncSession,
    content: bytes,
    author: User,
    include_images: bool,
    dry_run: bool,
) -> ImportResult:
    wxr = parse_wxr_bytes(content)

    if dry_run:
        return ImportResult(
            dry_run=True,
            posts_created=0,
            posts_updated=0,
            categories=len(wxr.categories),
            tags=sum(len(p.tag_names) for p in wxr.posts),
            redirects=0,
            images_uploaded=0,
            images_failed=0,
            site_title=wxr.site_title,
            total_posts=len(wxr.posts),
        )

    categories_created = await _import_categories(db, wxr)
    pipeline = MediaPipeline(db, author, enabled=include_images)
    stats = await _import_posts(db, wxr, pipeline)

    return ImportResult(
        dry_run=False,
        posts_created=stats["created"],
        posts_updated=stats["updated"],
        categories=categories_created,
        tags=stats["tags"],
        redirects=stats["redirects"],
        images_uploaded=pipeline.uploaded,
        images_failed=len(pipeline.failed),
        site_title=wxr.site_title,
        total_posts=len(wxr.posts),
    )
