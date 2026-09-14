from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.curriculum import CurriculumModule
from app.services import curriculum_service

router = APIRouter(prefix="/curriculum", tags=["curriculum"])


@router.get("/{track}", response_model=list[CurriculumModule])
async def get_curriculum(
    track: str,
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    return await curriculum_service.get_curriculum(db, track)
