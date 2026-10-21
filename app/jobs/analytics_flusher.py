import json
import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy import bindparam, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.database import AsyncSessionLocal
from app.core.redis_client import get_redis
from app.models import AnalyticsEvent, AnalyticsPageview, AnalyticsSession
from app.services.tracking_service import ANALYTICS_QUEUE
from app.utils.traffic_quality import EXCLUDED_CLASSES, classify_session

logger = logging.getLogger(__name__)

BATCH_SIZE = 500
MAX_EVENTS_PER_RUN = 100_000
INT_MAX = 2_147_483_647

sessions = AnalyticsSession.__table__
pageviews = AnalyticsPageview.__table__
custom_events = AnalyticsEvent.__table__


async def flush_analytics() -> None:
    r = get_redis()
    total = 0
    while total < MAX_EVENTS_PER_RUN:
        batch = await r.rpop(ANALYTICS_QUEUE, count=BATCH_SIZE)
        if not batch:
            break
        try:
            total += await _flush_batch(batch)
        except Exception:
            logger.exception("Failed to flush a batch of %d analytics events", len(batch))
    if total:
        logger.info("Flushed %d analytics events", total)


def _parse_event(raw: str) -> dict | None:
    try:
        event = json.loads(raw)
        for key in ("event_id", "session_id", "visitor_id", "page_id"):
            event[key] = UUID(event[key])
        event["occurred_at"] = datetime.fromisoformat(event["occurred_at"])
        if event["type"] not in ("pageview", "ping", "event"):
            raise ValueError("unknown event type")
    except (ValueError, KeyError, TypeError):
        logger.warning("Dropping malformed analytics event")
        return None
    return event


async def _flush_batch(batch: list[str]) -> int:
    events = [event for event in map(_parse_event, batch) if event is not None]
    if not events:
        return 0

    first_pageview: dict[UUID, dict] = {}
    last_seen: dict[UUID, datetime] = {}
    page_progress: dict[UUID, dict] = {}
    interacted_sessions: set[UUID] = set()
    internal_sessions: set[UUID] = set()

    for event in events:
        session_id = event["session_id"]
        last_seen[session_id] = max(last_seen.get(session_id, event["occurred_at"]), event["occurred_at"])
        if event["type"] == "pageview":
            first_pageview.setdefault(session_id, event)
        if event["type"] != "event":
            progress = page_progress.setdefault(event["page_id"], {"engaged": 0, "scroll": 0})
            progress["engaged"] = max(progress["engaged"], event["engaged_ms"])
            progress["scroll"] = max(progress["scroll"], event["scroll"])
        if event["interacted"]:
            interacted_sessions.add(session_id)
        if event["internal"]:
            internal_sessions.add(session_id)

    async with AsyncSessionLocal() as db:
        if first_pageview:
            await db.execute(
                pg_insert(sessions)
                .values([_session_row(event) for event in first_pageview.values()])
                .on_conflict_do_nothing(index_elements=["id"])
            )
            await db.execute(
                pg_insert(pageviews)
                .values(
                    [
                        {
                            "id": event["page_id"],
                            "session_id": event["session_id"],
                            "path": event["path"],
                            "occurred_at": event["occurred_at"],
                        }
                        for event in events
                        if event["type"] == "pageview"
                    ]
                )
                .on_conflict_do_nothing(index_elements=["id"])
            )

        known_sessions = set(
            await db.scalars(select(sessions.c.id).where(sessions.c.id.in_(list(last_seen))))
        )

        custom_rows = [
            {
                "id": event["event_id"],
                "session_id": event["session_id"],
                "name": event["name"],
                "path": event["path"],
                "target": event["target"],
                "occurred_at": event["occurred_at"],
            }
            for event in events
            if event["type"] == "event" and event["session_id"] in known_sessions
        ]
        if custom_rows:
            await db.execute(pg_insert(custom_events).values(custom_rows).on_conflict_do_nothing(index_elements=["id"]))

        await db.execute(
            update(pageviews)
            .where(pageviews.c.id == bindparam("target_page"))
            .values(
                engaged_ms=func.greatest(pageviews.c.engaged_ms, bindparam("new_engaged")),
                max_scroll=func.greatest(pageviews.c.max_scroll, bindparam("new_scroll")),
            ),
            [
                {
                    "target_page": page_id,
                    "new_engaged": min(progress["engaged"], INT_MAX),
                    "new_scroll": progress["scroll"],
                }
                for page_id, progress in page_progress.items()
            ],
        )

        affected = list(known_sessions)
        await db.execute(
            update(sessions)
            .where(sessions.c.id == bindparam("target_session"))
            .values(last_seen_at=func.greatest(sessions.c.last_seen_at, bindparam("seen_at"))),
            [{"target_session": sid, "seen_at": ts} for sid, ts in last_seen.items() if sid in known_sessions],
        )
        if interacted_sessions:
            await db.execute(
                update(sessions).where(sessions.c.id.in_(interacted_sessions)).values(interacted=True)
            )
        if internal_sessions:
            await db.execute(
                update(sessions).where(sessions.c.id.in_(internal_sessions)).values(is_internal=True)
            )

        await _refresh_aggregates(db, affected)
        await db.commit()
    return len(events)


async def _refresh_aggregates(db, session_ids: list[UUID]) -> None:
    if not session_ids:
        return

    await db.execute(
        update(sessions)
        .where(sessions.c.id.in_(session_ids))
        .values(
            engaged_ms=select(func.coalesce(func.sum(pageviews.c.engaged_ms), 0))
            .where(pageviews.c.session_id == sessions.c.id)
            .scalar_subquery(),
            page_count=select(func.count()).where(pageviews.c.session_id == sessions.c.id).scalar_subquery(),
            exit_path=select(pageviews.c.path)
            .where(pageviews.c.session_id == sessions.c.id)
            .order_by(pageviews.c.occurred_at.desc())
            .limit(1)
            .scalar_subquery(),
        )
    )

    rows = (
        await db.execute(
            select(
                sessions.c.id,
                sessions.c.ua_bot,
                sessions.c.is_internal,
                sessions.c.bot_score,
                sessions.c.interacted,
                sessions.c.engaged_ms,
                sessions.c.page_count,
            ).where(sessions.c.id.in_(session_ids))
        )
    ).all()
    updates = []
    for row in rows:
        verdict = classify_session(
            ua_bot=row.ua_bot,
            internal=row.is_internal,
            bot_score=row.bot_score,
            interacted=row.interacted,
            engaged_ms=row.engaged_ms,
            page_count=row.page_count,
        )
        updates.append(
            {
                "target_session": row.id,
                "cls": verdict.traffic_class,
                "reason": verdict.reason,
                "confidence": verdict.confidence,
                "excluded": verdict.traffic_class in EXCLUDED_CLASSES,
            }
        )
    if updates:
        await db.execute(
            update(sessions)
            .where(sessions.c.id == bindparam("target_session"))
            .values(
                traffic_class=bindparam("cls"),
                class_reason=bindparam("reason"),
                class_confidence=bindparam("confidence"),
                is_bot=bindparam("excluded"),
            ),
            updates,
        )


def _session_row(event: dict) -> dict:
    return {
        "id": event["session_id"],
        "visitor_id": event["visitor_id"],
        "started_at": event["occurred_at"],
        "last_seen_at": event["occurred_at"],
        "landing_path": event["path"],
        "source": event["source"],
        "medium": event["medium"],
        "referrer_host": event["referrer_host"],
        "utm_campaign": event["utm_campaign"],
        "device": event["device"],
        "browser": event["browser"],
        "os": event["os"],
        "country": event["country"],
        "city": event["city"],
        "ip_hash": event["ip_hash"],
        "is_returning": event["returning"],
        "bot_score": event["bot_score"],
        "ua_bot": event["ua_bot"],
        "is_internal": event["internal"],
    }
