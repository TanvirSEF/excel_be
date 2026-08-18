from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import NotFoundException
from app.services import seo_service

router = APIRouter(tags=["seo"])


@router.get("/sitemap.xml")
async def sitemap() -> Response:
    xml = await seo_service.get_sitemap()
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/api/v1/redirects/{old_path:path}")
async def redirect_lookup(old_path: str, db: AsyncSession = Depends(get_db)) -> dict:
    redirect = await seo_service.find_redirect(db, old_path)
    if redirect is None:
        raise NotFoundException("No redirect for this path", code="REDIRECT_NOT_FOUND")
    return {
        "old_path": redirect.old_path,
        "new_path": redirect.new_path,
        "redirect_type": redirect.redirect_type,
    }
