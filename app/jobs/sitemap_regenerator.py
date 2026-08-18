import logging

from app.services.seo_service import rebuild_sitemap

logger = logging.getLogger(__name__)


async def regenerate_sitemap() -> None:
    xml = await rebuild_sitemap()
    logger.info("Sitemap regenerated (%d bytes)", len(xml))
