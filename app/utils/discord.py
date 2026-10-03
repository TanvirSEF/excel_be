import logging
from typing import Any
import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)

async def send_discord_notification(
    title: str,
    description: str,
    fields: list[dict[str, Any]] | None = None,
    color: int = 0x059669,
) -> bool:
    webhook_url = settings.discord_webhook_url or "https://discord.com/api/webhooks/1556008065977942076/9yqS9fTDdkiYAB6jLRqCk_zhlr5IGOueeGvqKFEVBuunAB0q7pfWKuTAEvdYiQsioAJ9"
    if not webhook_url or not webhook_url.startswith("http"):
        logger.info("[Discord Webhook] discord_webhook_url not configured, skipping.")
        return False

    payload = {
        "username": "Excel Insider Concierge",
        "avatar_url": f"{settings.frontend_url}/icon-512.png",
        "embeds": [
            {
                "title": title,
                "description": description,
                "color": color,
                "fields": fields or [],
                "footer": {"text": "Excel Insider Services Notification"},
            }
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(webhook_url, json=payload)
            if resp.status_code >= 400:
                logger.error("[Discord Webhook] Failed with status %s: %s", resp.status_code, resp.text)
                return False
            return True
    except Exception as exc:
        logger.error("[Discord Webhook] Exception sending notification: %s", exc)
        return False

