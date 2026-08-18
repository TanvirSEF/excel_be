from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from sqlalchemy import text

from app.api.v1.router import api_router
from app.api.v1.seo import router as seo_router
from app.core.config import settings
from app.core.database import engine
from app.core.exceptions import AppException
from app.core.redis_client import close_redis, get_redis
from app.jobs.scheduler import build_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    scheduler = build_scheduler()
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)
    await close_redis()
    await engine.dispose()


app = FastAPI(title="Excel Insider API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
app.include_router(seo_router)


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message, "status": exc.status_code}},
    )


@app.get("/health")
async def health_check():
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))

    try:
        await get_redis().ping()
    except RedisError:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "database": "ok", "redis": "unreachable"},
        )

    return {"status": "ok", "environment": settings.environment, "database": "ok", "redis": "ok"}


@app.get("/")
async def root():
    return {"message": "Excel Insider API is running"}
