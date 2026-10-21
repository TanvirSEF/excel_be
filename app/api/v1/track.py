from fastapi import APIRouter, Request, Response

from app.core.config import settings
from app.core.exceptions import PermissionDeniedException
from app.deps.rate_limit import enforce_rate_limit
from app.schemas.track import TrackPayload
from app.services import tracking_service
from app.utils.geoip import resolve_geoip

router = APIRouter(prefix="/track", tags=["track"])


def _client_ip(request: Request) -> str | None:
    return (
        request.headers.get("cf-connecting-ip")
        or (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        or request.headers.get("x-real-ip")
        or (request.client.host if request.client else None)
    )


def _verify_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin not in settings.cors_origins:
        raise PermissionDeniedException("Origin not allowed")


@router.post("", status_code=204)
async def track(payload: TrackPayload, request: Request) -> Response:
    _verify_origin(request)
    ip = _client_ip(request)
    await enforce_rate_limit("track", ip or "unknown", settings.rate_limit_track)

    country = request.headers.get("cf-ipcountry")
    city = request.headers.get("cf-ipcity")
    if payload.type == "pageview" and not country:
        _, country, city = await resolve_geoip(ip)

    await tracking_service.ingest(
        payload,
        user_agent=request.headers.get("user-agent"),
        ip=ip,
        country=country,
        city=city,
    )
    return Response(status_code=204)
