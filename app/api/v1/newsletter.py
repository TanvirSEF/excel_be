from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.newsletter import SubscribeRequest, UnsubscribeRequest
from app.services import newsletter_service

router = APIRouter(prefix="/newsletter", tags=["newsletter"])


@router.post("/subscribe", status_code=201)
async def subscribe(
    data: SubscribeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    ip = request.client.host if request.client else None
    await newsletter_service.subscribe(db, data.email, data.source, ip)
    return {"message": "Subscribed"}


@router.post("/unsubscribe")
async def unsubscribe(
    data: UnsubscribeRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    await newsletter_service.unsubscribe(db, data.token)
    return {"message": "Unsubscribed"}
