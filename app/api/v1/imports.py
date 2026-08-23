from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import ValidationException
from app.deps.auth_deps import require_role
from app.models import User, UserRole
from app.services.import_service import ImportResult, run_import

router = APIRouter(prefix="/imports", tags=["imports"])


@router.post("/wordpress", response_model=dict)
async def wordpress_import(
    file: UploadFile = File(...),
    dry_run: bool = Query(False),
    include_images: bool = Query(True),
    user: User = Depends(require_role(UserRole.super_admin)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not file.filename or not file.filename.endswith(".xml"):
        raise ValidationException("Only .xml WXR export files are accepted")

    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        raise ValidationException("File too large — maximum 50 MB")

    result: ImportResult = await run_import(
        db=db,
        content=content,
        author=user,
        include_images=include_images,
        dry_run=dry_run,
    )

    return {
        "dry_run": result.dry_run,
        "site_title": result.site_title,
        "total_posts": result.total_posts,
        "posts_created": result.posts_created,
        "posts_updated": result.posts_updated,
        "categories": result.categories,
        "tags": result.tags,
        "redirects": result.redirects,
        "images_uploaded": result.images_uploaded,
        "images_failed": result.images_failed,
    }
