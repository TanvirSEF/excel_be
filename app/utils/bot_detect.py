import re

BOT_PATTERN = re.compile(
    r"bot|crawler|spider|scraping|preview|headless|lighthouse|pingdom|uptime"
    r"|slack|discord|telegram|whatsapp"
    r"|facebookexternalhit|twitterbot|linkedinbot|pinterest"
    r"|gptbot|claudebot|perplexity|ccbot|amazonbot|bytespider"
    r"|curl|wget|python|urllib|axios|node-fetch|go-http-client|java/"
    r"|excelinsider-web",
    re.IGNORECASE,
)


def is_bot(user_agent: str | None) -> bool:
    if not user_agent:
        return True
    return bool(BOT_PATTERN.search(user_agent))


BOT_THRESHOLD = 50


def bot_score(
    user_agent: str | None,
    *,
    webdriver: bool = False,
    has_languages: bool = True,
    screen_width: int | None = None,
    screen_height: int | None = None,
    burst: bool = False,
    datacenter: bool = False,
) -> int:
    if is_bot(user_agent):
        return 100
    score = 0
    if webdriver:
        score += 70
    if not has_languages:
        score += 30
    if screen_width == 0 or screen_height == 0:
        score += 30
    if burst:
        score += 50
    if datacenter:
        score += 40
    return min(score, 100)
