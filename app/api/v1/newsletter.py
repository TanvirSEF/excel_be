from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.deps.rate_limit import newsletter_rate_limit
from app.schemas.newsletter import SubscribeRequest, UnsubscribeRequest
from app.services import newsletter_service

router = APIRouter(prefix="/newsletter", tags=["newsletter"])


@router.post("/subscribe", status_code=201, dependencies=[Depends(newsletter_rate_limit())])
async def subscribe(
    data: SubscribeRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    await newsletter_service.subscribe(db, data.email, data.source)
    return {"message": "Subscribed"}


@router.post("/unsubscribe")
async def unsubscribe(
    data: UnsubscribeRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    await newsletter_service.unsubscribe(db, data.token)
    return {"message": "Unsubscribed"}
