import json
import logging
import re
from typing import AsyncGenerator

from openai import AsyncOpenAI
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.redis_client import get_redis
from app.models import Post, PostStatus

logger = logging.getLogger(__name__)

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
    if not settings.openai_api_key:
        yield f"data: {json.dumps({'error': 'AI Assistant is not configured on this server.'})}\n\n"
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
        "You are the official Excel Insider AI Copilot, an expert spreadsheet educator and formula specialist for Excel and Google Sheets.\n\n"
        "Instructions:\n"
        "1. Give direct, practical, and beginner-to-advanced spreadsheet solutions.\n"
        "2. Always format formulas in inline code backticks or code blocks (e.g. `=XLOOKUP(A2, D:D, E:E, \"Not Found\")`). Briefly explain what each parameter does.\n"
        "3. If fixing an error (e.g., #N/A, #VALUE!, #SPILL!, #REF!), diagnose the exact root cause and give the exact fixed formula.\n"
        "4. Note any syntax differences between Microsoft Excel (Excel 365 vs Legacy) and Google Sheets where relevant.\n"
        "5. If relevant articles or calculators are provided below, naturally mention and link to them using Markdown links so readers can explore deeper.\n"
        "6. Keep responses clear, helpful, and beautifully formatted with bullet points and bold headers.\n\n"
        f"{context_str}"
    )

    formatted_messages = [{"role": "system", "content": system_prompt}]
    # Keep last 5 messages for conversation continuity
    for m in messages[-5:]:
        role = m.get("role")
        content = m.get("content")
        if role in ("user", "assistant") and content:
            formatted_messages.append({"role": role, "content": str(content)[:600]})

    try:
        client = AsyncOpenAI(api_key=settings.openai_api_key)
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

        yield f"data: {json.dumps({'type': 'done', 'articles': articles, 'calculators': calculators})}\n\n"
    except Exception as e:
        logger.exception("Error in assistant stream: %s", e)
        yield f"data: {json.dumps({'error': 'Sorry, the assistant encountered an error. Please try again shortly.'})}\n\n"
