from fastapi import APIRouter, Depends, Request, Response

from app.core.config import settings
from app.deps.client_info import real_client_ip, require_allowed_origin
from app.deps.rate_limit import enforce_rate_limit
from app.schemas.track import TrackBatch
from app.services import tracking_service
from app.utils.geoip import resolve_geoip

router = APIRouter(prefix="/track", tags=["track"])


@router.post("", status_code=204, dependencies=[Depends(require_allowed_origin)])
async def track(batch: TrackBatch, request: Request) -> Response:
    ip = real_client_ip(request)
    await enforce_rate_limit("track", ip or "unknown", settings.rate_limit_track)

    country = request.headers.get("cf-ipcountry")
    city = request.headers.get("cf-ipcity")
    if not country and any(event.type == "pageview" for event in batch.events):
        _, country, city = await resolve_geoip(ip)

    await tracking_service.ingest_batch(
        batch.events,
        user_agent=request.headers.get("user-agent"),
        ip=ip,
        country=country,
        city=city,
    )
    return Response(status_code=204)
