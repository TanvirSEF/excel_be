from dataclasses import dataclass

from app.utils.bot_detect import BOT_THRESHOLD

LIKELY_HUMAN = "likely_human"
KNOWN_BOT = "known_bot"
SUSPICIOUS = "suspicious"
INTERNAL = "internal"
UNKNOWN = "unknown"

EXCLUDED_CLASSES = (KNOWN_BOT, SUSPICIOUS, INTERNAL)
CLEAN_CLASSES = (LIKELY_HUMAN, UNKNOWN)
ENGAGED_EVIDENCE_MS = 10_000
HUMAN_SCORE_CEILING = 30


@dataclass(frozen=True)
class Classification:
    traffic_class: str
    reason: str
    confidence: str


def classify_session(
    *,
    ua_bot: bool,
    internal: bool,
    bot_score: int,
    interacted: bool,
    engaged_ms: int,
    page_count: int,
) -> Classification:
    if internal:
        return Classification(INTERNAL, "Matched the internal traffic rules", "high")
    if ua_bot:
        return Classification(KNOWN_BOT, "User agent matches a known bot or automation tool", "high")
    if bot_score >= BOT_THRESHOLD:
        return Classification(
            SUSPICIOUS,
            "Automation signals: headless browser, request burst or missing browser features",
            "medium",
        )
    if bot_score < HUMAN_SCORE_CEILING and interacted:
        return Classification(LIKELY_HUMAN, "Real browser with direct user interaction", "high")
    if bot_score < HUMAN_SCORE_CEILING and (engaged_ms >= ENGAGED_EVIDENCE_MS or page_count >= 2):
        return Classification(LIKELY_HUMAN, "Real browser with sustained engagement or multiple pages", "medium")
    if bot_score >= HUMAN_SCORE_CEILING:
        return Classification(
            UNKNOWN,
            "Some automation signals, for example a datacenter IP, but not enough to call it suspicious",
            "low",
        )
    return Classification(UNKNOWN, "Passed browser checks but no interaction or engagement evidence yet", "low")
