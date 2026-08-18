from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.deps.auth_deps import get_current_user, require_role
from app.models import User
from app.schemas.analytics import OverviewAnalytics, PostAnalytics
from app.services import analytics_service
from app.services.analytics_service import ANALYTICS_VIEWERS

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/posts/{post_id}", response_model=PostAnalytics)
async def post_analytics(
    post_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PostAnalytics:
    return await analytics_service.post_analytics(db, user, post_id)


@router.get("/overview", response_model=OverviewAnalytics)
async def overview(
    user: User = Depends(require_role(*ANALYTICS_VIEWERS)),
    db: AsyncSession = Depends(get_db),
) -> OverviewAnalytics:
    return await analytics_service.overview(db)
