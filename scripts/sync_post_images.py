import argparse
import asyncio
import hashlib
import io
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from PIL import Image
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models import Media, Post, User, UserRole
from app.services.media_service import upload_to_r2, process_image

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sync_images")

IMG_SRC_RE = re.compile(r'https?://[^\s"\'<>]+\.(?:png|jpe?g|webp|gif)', re.IGNORECASE)
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024


class ImageSyncPipeline:
    def __init__(self, admin_user_id, dry_run: bool = False, sem_count: int = 10):
        self.admin_id = admin_user_id
        self.dry_run = dry_run
        self.cache: dict[str, str] = {}
        self.uploaded_count = 0
        self.failed_urls: list[str] = []
        self.semaphore = asyncio.Semaphore(sem_count)

    async def get_or_upload_image(self, client: httpx.AsyncClient, url: str) -> str:
        clean_url = url.split("?")[0].strip()
        if not clean_url or not clean_url.startswith("http"):
            return url

        # Fast memory cache check
        if clean_url in self.cache:
            return self.cache[clean_url]

        url_hash = hashlib.sha256(clean_url.encode()).hexdigest()[:16]
        alt_tag = f"wp-sync:{url_hash}"

        if self.dry_run:
            fake_url = f"{settings.r2_public_url.rstrip('/')}/images/wp/{clean_url.split('/')[-1]}"
            self.cache[clean_url] = fake_url
            return fake_url

        async with self.semaphore:
            if clean_url in self.cache:
                return self.cache[clean_url]

            # Check DB
            try:
                async with AsyncSessionLocal() as session:
                    existing = await session.scalar(select(Media).where(Media.alt_text == alt_tag))
                    if existing:
                        self.cache[clean_url] = existing.file_url
                        return existing.file_url
            except Exception:
                pass

            # Download from WP host
            try:
                resp = await client.get(clean_url, timeout=25.0)
                if resp.status_code != 200:
                    self.failed_urls.append(clean_url)
                    return url
                raw_bytes = resp.content
                if len(raw_bytes) > MAX_DOWNLOAD_BYTES:
                    self.failed_urls.append(clean_url)
                    return url
            except Exception:
                self.failed_urls.append(clean_url)
                return url

            # Optimize & Upload to R2
            try:
                parsed = urlparse(clean_url)
                filename = parsed.path.split("/")[-1] or "image.jpg"
                stem = filename.rsplit(".", 1)[0]
                now = datetime.now(timezone.utc)
                r2_key = f"images/{now:%Y/%m}/{stem}-{url_hash[:8]}.webp"

                processed = await asyncio.to_thread(process_image, raw_bytes)
                r2_url = await asyncio.to_thread(upload_to_r2, r2_key, processed.data, "image/webp")

                async with AsyncSessionLocal() as session:
                    media = Media(
                        uploader_id=self.admin_id,
                        file_url=r2_url,
                        file_type="image/webp",
                        alt_text=alt_tag,
                        width=processed.width,
                        height=processed.height,
                        size_kb=len(processed.data) // 1024,
                        folder="imported",
                    )
                    session.add(media)
                    await session.commit()

                self.uploaded_count += 1
                self.cache[clean_url] = r2_url
                return r2_url
            except Exception as e:
                logger.warning(f"Processing failed for {clean_url}: {e}")
                self.failed_urls.append(clean_url)
                return url


async def sync_all_posts(limit: int | None = None, dry_run: bool = False):
    async with AsyncSessionLocal() as db:
        admin = await db.scalar(select(User).where(User.role == UserRole.super_admin))
        if not admin:
            logger.error("No super_admin user found.")
            return
        admin_id = admin.id

        pipeline = ImageSyncPipeline(admin_id, dry_run=dry_run, sem_count=10)

        # Preload DB media
        logger.info("Loading existing R2 media cache from database...")
        media_rows = (await db.scalars(select(Media).where(Media.alt_text.like("wp-sync:%")))).all()
        for m in media_rows:
            pipeline.cache[m.alt_text] = m.file_url
        logger.info(f"Loaded {len(media_rows)} existing images into fast cache.")

        # Find posts that still have WordPress image URLs
        query = select(Post.id, Post.title, Post.slug, Post.featured_image_url, Post.content_html, Post.content_json)
        query = query.order_by(Post.created_at.desc())
        if limit:
            query = query.limit(limit)

        posts_data = (await db.execute(query)).all()

    # Filter posts that actually contain WP images (.png/.jpg/.webp)
    candidates = []
    for row in posts_data:
        post_id, title, slug, featured, html, content_json = row
        featured = featured or ""
        html = html or ""
        imgs = [u for u in IMG_SRC_RE.findall(featured + " " + html) if "wp-content/uploads" in u]
        if imgs:
            candidates.append((row, imgs))

    total_candidates = len(candidates)
    logger.info(f"Found {total_candidates} posts needing image synchronization.")

    if total_candidates == 0:
        logger.info("All post images are already 100% migrated to Cloudflare R2!")
        return

    updated_posts = 0

    limits = httpx.Limits(max_keepalive_connections=30, max_connections=50)
    async with httpx.AsyncClient(limits=limits, follow_redirects=True) as http_client:
        for idx, (row, wp_images) in enumerate(candidates, start=1):
            post_id, title, slug, featured, html, content_json = row
            changed = False
            html = html or ""
            featured = featured or ""

            # Check featured image
            if "wp-content/uploads" in featured and any(featured.lower().endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif")):
                new_featured = await pipeline.get_or_upload_image(http_client, featured)
                if new_featured != featured:
                    featured = new_featured
                    changed = True

            # Concurrent fetch of all body images
            unique_imgs = list(set(wp_images))
            if unique_imgs:
                tasks = [pipeline.get_or_upload_image(http_client, img) for img in unique_imgs]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for old_img, res in zip(unique_imgs, results):
                    if isinstance(res, str) and res != old_img:
                        html = html.replace(old_img, res)
                        changed = True

            if changed:
                # Update content_json blocks
                if content_json and "blocks" in content_json:
                    blocks = content_json["blocks"]
                    for block in blocks:
                        if block.get("type") == "image" and block.get("url") in pipeline.cache:
                            block["url"] = pipeline.cache[block["url"]]
                        elif block.get("type") == "html":
                            b_html = block.get("html", "")
                            for old_img, new_img in pipeline.cache.items():
                                if old_img in b_html:
                                    b_html = b_html.replace(old_img, new_img)
                            block["html"] = b_html
                    content_json = {"blocks": blocks}

                if not dry_run:
                    for attempt in range(3):
                        try:
                            async with AsyncSessionLocal() as session:
                                await session.execute(
                                    update(Post)
                                    .where(Post.id == post_id)
                                    .values(
                                        featured_image_url=featured,
                                        content_html=html,
                                        content_json=content_json,
                                    )
                                )
                                await session.commit()
                            break
                        except Exception as e:
                            logger.warning(f"DB retry for {slug}: {e}")
                            await asyncio.sleep(1)

                updated_posts += 1

            if idx % 10 == 0 or idx == total_candidates:
                logger.info(
                    f"Progress: [{idx}/{total_candidates}] posts processed | "
                    f"{updated_posts} updated | "
                    f"{pipeline.uploaded_count} new images uploaded | "
                    f"{len(pipeline.failed_urls)} failed"
                )

    logger.info(
        f"\n=== SYNC COMPLETE ===\n"
        f"Total Posts Checked: {total_candidates}\n"
        f"Posts Updated: {updated_posts}\n"
        f"New Images Uploaded: {pipeline.uploaded_count}\n"
        f"Failed Downloads: {len(pipeline.failed_urls)}"
    )


def main():
    parser = argparse.ArgumentParser(description="Clean high-speed image sync to R2")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of posts to process")
    parser.add_argument("--dry-run", action="store_true", help="Scan and simulate only, no writes")
    args = parser.parse_args()

    asyncio.run(sync_all_posts(limit=args.limit, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
