from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services import assistant_service

router = APIRouter(prefix="/assistant", tags=["assistant"])


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(..., min_length=1, max_length=20)


@router.post("/chat")
async def chat(
    data: ChatRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    ip = (
        request.headers.get("cf-connecting-ip")
        or (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        or request.headers.get("x-real-ip")
        or (request.client.host if request.client else None)
    )

    generator = assistant_service.stream_chat_response(
        messages=[m.model_dump() for m in data.messages],
        client_ip=ip,
        db=db,
    )

    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
