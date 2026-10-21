from fastapi import Request

from app.core.config import settings
from app.core.exceptions import PermissionDeniedException


def real_client_ip(request: Request) -> str | None:
    return (
        request.headers.get("cf-connecting-ip")
        or (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        or request.headers.get("x-real-ip")
        or (request.client.host if request.client else None)
        or None
    )


def require_allowed_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin not in settings.cors_origins:
        raise PermissionDeniedException("Origin not allowed")
