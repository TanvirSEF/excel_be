import logging

from app.utils.datacenter import refresh_ranges

logger = logging.getLogger(__name__)


async def refresh_datacenter_ranges() -> None:
    count = await refresh_ranges()
    if count:
        logger.info("Loaded %d datacenter IP ranges", count)
