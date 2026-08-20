import logging
import math
import re
from collections.abc import Awaitable, Callable
from time import time
from uuid import uuid4

from fastapi import Request

from app.core.config import settings
from app.core.exceptions import TooManyRequestsException
from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)

UNITS = {
    "second": 1,
    "seconds": 1,
    "minute": 60,
    "minutes": 60,
    "hour": 3600,
    "hours": 3600,
}

WINDOW_RE = re.compile(r"^(\d+)\s*([a-zA-Z]+)$")


def parse_rate(spec: str) -> tuple[int, int]:
    try:
        raw_limit, raw_window = spec.strip().split("/", 1)
        limit = int(raw_limit)
        match = WINDOW_RE.match(raw_window.strip())
        if match is None:
            raise ValueError
        amount, unit = match.groups()
        window = int(amount) * UNITS[unit.lower()]
    except (ValueError, KeyError):
        raise ValueError(f"Invalid rate limit spec: {spec!r}")
    if limit < 1 or window < 1:
        raise ValueError(f"Invalid rate limit spec: {spec!r}")
    return limit, window


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def enforce_rate_limit(name: str, identifier: str, spec: str) -> None:
    limit, window = parse_rate(spec)
    key = f"ratelimit:{name}:{identifier}"
    now = time()

    try:
        r = get_redis()
        await r.zremrangebyscore(key, 0, now - window)
        oldest = await r.zrange(key, 0, 0, withscores=True)
        member = f"{now}:{uuid4().hex[:6]}"
        await r.zadd(key, {member: now})
        await r.expire(key, window)
        count = await r.zcard(key)
    except Exception:
        logger.warning("Rate limit check skipped for %s, Redis unavailable", name)
        return

    if count > limit:
        retry_after = math.ceil(oldest[0][1] + window - now) if oldest else window
        raise TooManyRequestsException(
            "Too many requests, please try again later",
            retry_after=max(retry_after, 1),
        )


def rate_limit(name: str, spec: str) -> Callable[..., Awaitable[None]]:
    async def dependency(request: Request) -> None:
        await enforce_rate_limit(name, client_ip(request), spec)

    return dependency


def login_rate_limit() -> Callable[..., Awaitable[None]]:
    return rate_limit("login", settings.rate_limit_login)


def comment_rate_limit() -> Callable[..., Awaitable[None]]:
    return rate_limit("comment", settings.rate_limit_comment)


def newsletter_rate_limit() -> Callable[..., Awaitable[None]]:
    return rate_limit("newsletter", settings.rate_limit_newsletter)


def forgot_password_rate_limit() -> Callable[..., Awaitable[None]]:
    return rate_limit("forgot-password", settings.rate_limit_forgot_password)


def reset_password_rate_limit() -> Callable[..., Awaitable[None]]:
    return rate_limit("reset-password", settings.rate_limit_reset_password)
