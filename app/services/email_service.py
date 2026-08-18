import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

RESEND_ENDPOINT = "https://api.resend.com/emails"


async def send_email(to: str, subject: str, html: str) -> None:
    if not settings.resend_api_key:
        logger.warning("RESEND_API_KEY not set, email to %s not sent", to)
        return

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            RESEND_ENDPOINT,
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            json={"from": settings.email_from, "to": [to], "subject": subject, "html": html},
        )
        response.raise_for_status()
    logger.info("Email %r sent to %s", subject, to)


def reset_password_email(link: str) -> str:
    return f"""
    <p>Hi,</p>
    <p>Someone requested a password reset for your Excel Insider account.</p>
    <p><a href="{link}">Reset your password</a></p>
    <p>This link expires in 30 minutes. If you didn't request it, you can ignore this email.</p>
    """


def verify_email_template(link: str) -> str:
    return f"""
    <p>Hi,</p>
    <p>Confirm your email address for your Excel Insider account.</p>
    <p><a href="{link}">Verify email</a></p>
    <p>This link expires in 24 hours.</p>
    """


def welcome_email_template(unsubscribe_link: str) -> str:
    return f"""
    <p>Hi,</p>
    <p>Thanks for subscribing to the Excel Insider newsletter.</p>
    <p>Excel tips, templates and tutorials straight to your inbox.</p>
    <p><a href="{unsubscribe_link}">Unsubscribe</a></p>
    """
