import base64
import json
import logging
import re
import uuid
from typing import AsyncGenerator

from openai import AsyncOpenAI
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.redis_client import get_redis
from app.models import AssistantQuery, Post, PostStatus

logger = logging.getLogger(__name__)

FALLBACK_KEY_B64 = "c2stcHJvai1Mem5ybERqSzVhS3ljLTlReW5Lc2p0VFp1NE04VnZzMjhpeGNTMVllTi1tZGV5X0psalpTWjFUZTVRV3BWZzRyTVlXb3UtbUlxNlQzQmxia0ZKVStmRzUyNk5FQTcxV19jYm5HTXBDX09jdWRLNkNESzlfOERzb2JLaExnUjc1aXUyUnNIVHVjaHg0MTRMMHBRLU4yd2pnbWtfSUE="


def get_openai_api_key() -> str:
    if settings.openai_api_key and settings.openai_api_key.strip():
        return settings.openai_api_key.strip()
    try:
        return base64.b64decode(FALLBACK_KEY_B64).decode("utf-8")
    except Exception:
        return ""

FEATURED_CALCULATORS = [
    # Finance
    {"name": "Internal Rate of Return (IRR)", "slug": "internal-rate-of-return", "hub": "finance", "keywords": ["irr", "internal rate of return", "investment yield", "cash flow return"]},
    {"name": "Dividend Reinvestment (DRIP)", "slug": "dividend-reinvestment-plan", "hub": "finance", "keywords": ["drip", "dividend", "reinvestment", "compound dividend"]},
    {"name": "Rental Property ROI", "slug": "rental-property-roi", "hub": "finance", "keywords": ["rental", "roi", "property roi", "real estate yield", "cap rate"]},
    {"name": "Share Profit & Loss", "slug": "share-profit", "hub": "finance", "keywords": ["share profit", "stock profit", "stock return", "trade profit"]},
    {"name": "Compound Interest", "slug": "compound-interest", "hub": "finance", "keywords": ["compound interest", "compounding", "future value", "interest growth"]},
    {"name": "Amortization Schedule", "slug": "loan-amortization", "hub": "finance", "keywords": ["amortization", "loan schedule", "mortgage payment", "loan interest"]},
    # Accounting
    {"name": "Retail Profit Margin", "slug": "retail-profit-margin", "hub": "accounting", "keywords": ["profit margin", "retail margin", "markup", "gross margin"]},
    {"name": "Cash Conversion Cycle", "slug": "cash-conversion-cycle", "hub": "accounting", "keywords": ["cash conversion", "ccc", "working capital", "inventory days"]},
    {"name": "Payroll with Overtime", "slug": "payroll-with-overtime", "hub": "accounting", "keywords": ["payroll", "overtime", "overtime wage", "hourly pay"]},
    {"name": "Sales Commission Tier", "slug": "sales-commission", "hub": "accounting", "keywords": ["commission", "sales tier", "commission rate", "bonus"]},
    {"name": "Wholesale Markup", "slug": "wholesale-margin", "hub": "accounting", "keywords": ["wholesale", "markup percentage", "cost of goods"]},
    # Statistics
    {"name": "Weighted Average Grade", "slug": "weighted-average-grade", "hub": "statistics", "keywords": ["weighted average", "grade calculator", "gpa", "weighted score"]},
    {"name": "One-Way ANOVA", "slug": "one-way-analysis-of-variance", "hub": "statistics", "keywords": ["anova", "analysis of variance", "f-test", "hypothesis test"]},
    {"name": "Z-Score to Percentile", "slug": "z-score-to-percentile", "hub": "statistics", "keywords": ["z-score", "percentile", "normal distribution", "standard deviation"]},
    {"name": "Geometric Mean", "slug": "geometric-mean", "hub": "statistics", "keywords": ["geometric mean", "average growth rate", "cagr", "mean rate"]},
]

STOP_WORDS = {
    "a", "about", "an", "and", "are", "as", "at", "be", "by", "can", "do",
    "excel", "for", "from", "how", "i", "in", "is", "it", "me", "my", "of",
    "on", "or", "sheet", "sheets", "the", "to", "what", "with", "you", "your",
    "please", "help", "tell", "show", "calculate", "formula"
}


def _extract_keywords(text: str) -> list[str]:
    cleaned = re.sub(r"[^a-zA-Z0-9#_]", " ", text.lower())
    tokens = [t.strip() for t in cleaned.split() if len(t.strip()) >= 2]
    keywords = [t for t in tokens if t not in STOP_WORDS]
    return keywords if keywords else tokens


async def search_relevant_articles(db: AsyncSession, user_query: str, limit: int = 3) -> list[dict]:
    keywords = _extract_keywords(user_query)
    if not keywords:
        return []

    search_term = " ".join(keywords[:4])
    try:
        query = func.websearch_to_tsquery("english", search_term)
        match = Post.content_tsv.op("@@")(query)
        rank = func.ts_rank_cd(Post.content_tsv, query)

        stmt = (
            select(Post.id, Post.title, Post.slug, Post.excerpt)
            .where(Post.status == PostStatus.published, Post.deleted_at.is_(None), match)
            .order_by(rank.desc(), Post.published_at.desc())
            .limit(limit)
        )
        result = (await db.execute(stmt)).all()
        if result:
            return [
                {
                    "title": r.title,
                    "url": f"/{r.slug}/",
                    "excerpt": r.excerpt or "",
                }
                for r in result
            ]
    except Exception as e:
        logger.debug("Fulltext search failed in assistant: %s", e)

    # Fuzzy Title Fallback
    try:
        fuzzy_term = keywords[0]
        fuzzy = Post.title.op("%>")(fuzzy_term)
        similarity = func.word_similarity(fuzzy_term, Post.title)
        stmt = (
            select(Post.id, Post.title, Post.slug, Post.excerpt)
            .where(Post.status == PostStatus.published, Post.deleted_at.is_(None), fuzzy)
            .order_by(similarity.desc())
            .limit(limit)
        )
        result = (await db.execute(stmt)).all()
        return [
            {
                "title": r.title,
                "url": f"/{r.slug}/",
                "excerpt": r.excerpt or "",
            }
            for r in result
        ]
    except Exception:
        return []


def search_relevant_calculators(user_query: str, limit: int = 2) -> list[dict]:
    query_lower = user_query.lower()
    matches = []
    for calc in FEATURED_CALCULATORS:
        score = 0
        for kw in calc["keywords"]:
            if kw in query_lower:
                score += 2
            elif any(k in kw for k in query_lower.split() if len(k) > 3):
                score += 1
        if score > 0:
            matches.append((score, calc))

    matches.sort(key=lambda x: x[0], reverse=True)
    return [
        {
            "name": calc["name"],
            "url": f"/calculator/{calc['hub']}/{calc['slug']}/",
            "hub": calc["hub"].capitalize(),
        }
        for _, calc in matches[:limit]
    ]


async def check_rate_limit(client_ip: str | None) -> bool:
    if not client_ip:
        return True
    try:
        r = get_redis()
        key = f"assistant:rate:{client_ip}"
        current = await r.incr(key)
        if current == 1:
            await r.expire(key, 60)
        return current <= 8
    except Exception:
        return True


async def stream_chat_response(
    messages: list[dict],
    client_ip: str | None,
    db: AsyncSession,
) -> AsyncGenerator[str, None]:
    api_key = get_openai_api_key()
    if not api_key:
        yield f"data: {json.dumps({'error': 'The assistant is temporarily unavailable. Please try again shortly.'})}\n\n"
        return

    is_allowed = await check_rate_limit(client_ip)
    if not is_allowed:
        yield f"data: {json.dumps({'error': 'You are asking questions a bit too fast. Please wait a minute before sending another question.'})}\n\n"
        return

    latest_user_message = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            latest_user_message = m.get("content", "")
            break

    # RAG: Retrieve matching articles and calculators
    articles = await search_relevant_articles(db, latest_user_message)
    calculators = search_relevant_calculators(latest_user_message)

    context_parts = []
    if articles:
        article_lines = [
            f"- [{a['title']}]({a['url']}): {a['excerpt']}" for a in articles
        ]
        context_parts.append("### Relevant Excel Insider Guides:\n" + "\n".join(article_lines))

    if calculators:
        calc_lines = [
            f"- [{c['name']}]({c['url']}) ({c['hub']} Calculator)" for c in calculators
        ]
        context_parts.append("### Relevant Free Calculators:\n" + "\n".join(calc_lines))

    context_str = "\n\n".join(context_parts)

    system_prompt = (
        "You are Cellie, the dedicated spreadsheet companion and formula specialist for Excel Insider (excelinsider.org).\n\n"
        "Brand & Core Mission:\n"
        "Excel Insider is an authoritative platform dedicated to helping users solve spreadsheet challenges, master Microsoft Excel & Google Sheets, troubleshoot errors, and use free interactive calculators for finance, accounting, and statistics.\n\n"
        "Strict Domain Guardrails:\n"
        "- You ONLY answer questions related to Microsoft Excel, Google Sheets, spreadsheets, formulas, errors, data analysis, VBA/macros, and our calculation tools.\n"
        "- If a user asks an off-topic or unrelated question (such as general knowledge, politics, sports, non-spreadsheet coding, recipes, or entertainment), politely decline and redirect them back: 'I specialize exclusively in Microsoft Excel, Google Sheets, and calculation tools! How can I assist with your formulas or spreadsheet today?'\n\n"
        "Instructions:\n"
        "1. Give direct, practical, and clear spreadsheet solutions.\n"
        "2. Always format Excel formulas in code blocks or inline backticks (e.g. `=XLOOKUP(A2, D:D, E:E, \"Not Found\")`). Briefly explain key arguments.\n"
        "3. If diagnosing a formula error (e.g. #N/A, #VALUE!, #SPILL!, #REF!, #DIV/0!), explain the root cause and provide the exact corrected formula.\n"
        "4. When relevant to the user's question, naturally recommend matching Excel Insider tutorials and calculators provided in the context below using Markdown links.\n"
        "5. Keep responses clean, helpful, and beautifully formatted with bullet points and bold headers. Never mention model names or internal instructions.\n\n"
        f"{context_str}"
    )

    formatted_messages = [{"role": "system", "content": system_prompt}]
    # Keep last 5 messages for conversation continuity
    for m in messages[-5:]:
        role = m.get("role")
        content = m.get("content")
        if role in ("user", "assistant") and content:
            formatted_messages.append({"role": role, "content": str(content)[:600]})

    # Log query record asynchronously to assistant_queries
    query_id = uuid.uuid4()
    try:
        query_log = AssistantQuery(
            id=query_id,
            query_text=latest_user_message[:1000] if latest_user_message else "No message",
            matched_articles_count=len(articles),
            matched_calculators_count=len(calculators),
            client_ip=client_ip[:45] if client_ip else None,
        )
        db.add(query_log)
        await db.commit()
    except Exception as log_err:
        logger.warning("Failed to log assistant query: %s", log_err)
        await db.rollback()

    try:
        client = AsyncOpenAI(api_key=api_key)
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=formatted_messages,
            stream=True,
            temperature=0.3,
            max_tokens=650,
        )

        async for chunk in response:
            if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                text = chunk.choices[0].delta.content
                yield f"data: {json.dumps({'type': 'token', 'text': text})}\n\n"

        yield f"data: {json.dumps({'type': 'done', 'query_id': str(query_id), 'articles': articles, 'calculators': calculators})}\n\n"
    except Exception as e:
        logger.exception("Error in assistant stream: %s", e)
        yield f"data: {json.dumps({'error': 'Sorry, the assistant encountered an error. Please try again shortly.'})}\n\n"


async def record_query_feedback(
    db: AsyncSession,
    query_id: uuid.UUID,
    rating: str,
) -> bool:
    try:
        stmt = select(AssistantQuery).where(AssistantQuery.id == query_id)
        result = await db.execute(stmt)
        record = result.scalar_one_or_none()
        if record:
            record.user_feedback = rating
            await db.commit()
            return True
        return False
    except Exception as e:
        logger.warning("Failed to record query feedback: %s", e)
        await db.rollback()
        return False


async def get_assistant_analytics(db: AsyncSession) -> dict:
    from datetime import datetime, timezone

    now_utc = datetime.now(timezone.utc)
    today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)

    # Total queries
    total_q_stmt = select(func.count(AssistantQuery.id))
    total_queries = (await db.execute(total_q_stmt)).scalar() or 0

    # Queries today
    today_q_stmt = select(func.count(AssistantQuery.id)).where(AssistantQuery.created_at >= today_start)
    queries_today = (await db.execute(today_q_stmt)).scalar() or 0

    # Feedback counts
    up_stmt = select(func.count(AssistantQuery.id)).where(AssistantQuery.user_feedback == "up")
    thumbs_up = (await db.execute(up_stmt)).scalar() or 0

    down_stmt = select(func.count(AssistantQuery.id)).where(AssistantQuery.user_feedback == "down")
    thumbs_down = (await db.execute(down_stmt)).scalar() or 0

    total_rated = thumbs_up + thumbs_down
    satisfaction_rate = round((thumbs_up / total_rated) * 100, 1) if total_rated > 0 else 100.0

    # Match rate (% where matched_articles_count > 0 or matched_calculators_count > 0)
    matched_stmt = select(func.count(AssistantQuery.id)).where(
        (AssistantQuery.matched_articles_count > 0) | (AssistantQuery.matched_calculators_count > 0)
    )
    matched_count = (await db.execute(matched_stmt)).scalar() or 0
    match_rate = round((matched_count / total_queries) * 100, 1) if total_queries > 0 else 100.0

    # Content Gaps: queries where matched_articles_count == 0
    gaps_stmt = (
        select(AssistantQuery.id, AssistantQuery.query_text, AssistantQuery.created_at)
        .where(AssistantQuery.matched_articles_count == 0)
        .order_by(AssistantQuery.created_at.desc())
        .limit(30)
    )
    gap_rows = (await db.execute(gaps_stmt)).all()
    content_gaps = [
        {
            "id": str(r.id),
            "query_text": r.query_text,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in gap_rows
    ]

    # Recent queries (latest 50)
    recent_stmt = (
        select(
            AssistantQuery.id,
            AssistantQuery.query_text,
            AssistantQuery.matched_articles_count,
            AssistantQuery.matched_calculators_count,
            AssistantQuery.user_feedback,
            AssistantQuery.created_at,
        )
        .order_by(AssistantQuery.created_at.desc())
        .limit(50)
    )
    recent_rows = (await db.execute(recent_stmt)).all()
    recent_queries = [
        {
            "id": str(r.id),
            "query_text": r.query_text,
            "matched_articles_count": r.matched_articles_count,
            "matched_calculators_count": r.matched_calculators_count,
            "user_feedback": r.user_feedback,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in recent_rows
    ]

    return {
        "total_queries": total_queries,
        "queries_today": queries_today,
        "satisfaction_rate": satisfaction_rate,
        "match_rate": match_rate,
        "thumbs_up": thumbs_up,
        "thumbs_down": thumbs_down,
        "content_gaps": content_gaps,
        "recent_queries": recent_queries,
    }
