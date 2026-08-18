import re

BOT_PATTERN = re.compile(
    r"bot|crawler|spider|scraping|preview|headless|lighthouse|pingdom|uptime"
    r"|slack|discord|telegram|whatsapp"
    r"|facebookexternalhit|twitterbot|linkedinbot|pinterest"
    r"|gptbot|claudebot|perplexity|ccbot|amazonbot|bytespider"
    r"|curl|wget|python|urllib|axios|node-fetch|go-http-client|java/",
    re.IGNORECASE,
)


def is_bot(user_agent: str | None) -> bool:
    if not user_agent:
        return True
    return bool(BOT_PATTERN.search(user_agent))
