from fastapi import APIRouter

from app.api.v1 import analytics, assets, audit, auth, categories, comments, media, newsletter, posts, search, tags, users

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(categories.router)
api_router.include_router(posts.router)
api_router.include_router(assets.router)
api_router.include_router(tags.router)
api_router.include_router(comments.router)
api_router.include_router(media.router)
api_router.include_router(search.router)
api_router.include_router(analytics.router)
api_router.include_router(newsletter.router)
api_router.include_router(audit.router)
