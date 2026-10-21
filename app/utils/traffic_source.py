from urllib.parse import urlparse

SEARCH_ENGINES = {"google", "bing", "duckduckgo", "yahoo", "yandex", "baidu", "ecosia", "brave"}
SOCIAL_NETWORKS = {
    "facebook", "fb", "instagram", "t", "twitter", "x", "linkedin", "lnkd", "reddit", "youtube",
    "youtu", "pinterest", "tiktok", "quora", "whatsapp", "telegram",
}


def referrer_host(referrer: str | None) -> str | None:
    if not referrer:
        return None
    try:
        host = urlparse(referrer).hostname
    except ValueError:
        return None
    return host.removeprefix("www.") if host else None


def classify_source(
    referrer: str | None,
    utm_source: str | None,
    utm_medium: str | None,
    own_host: str,
) -> tuple[str, str, str | None]:
    host = referrer_host(referrer)
    if host and host.removeprefix("www.") == own_host.removeprefix("www."):
        host = None

    if utm_source:
        return utm_source.lower(), (utm_medium or "campaign").lower(), host
    if host is None:
        return "(direct)", "(none)", None
    labels = host.split(".")
    engine = next((label for label in labels if label in SEARCH_ENGINES), None)
    if engine:
        return engine, "organic", host
    network = next((label for label in labels if label in SOCIAL_NETWORKS), None)
    if network:
        return network, "social", host
    return host, "referral", host
