from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.deps.rate_limit import contact_rate_limit
from app.schemas.contact import ContactMessageCreate
from app.services import contact_service

router = APIRouter(prefix="/contact", tags=["contact"])


@router.post("", status_code=201, dependencies=[Depends(contact_rate_limit())])
async def send_message(
    data: ContactMessageCreate,
    db: AsyncSession = Depends(get_db),
) -> dict:
    await contact_service.create_message(db, data)
    return {"message": "Message sent"}
