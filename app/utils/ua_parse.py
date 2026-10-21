import re

TABLET = re.compile(r"ipad|tablet|kindle|silk|playbook|android(?!.*mobile)", re.IGNORECASE)
MOBILE = re.compile(r"mobi|iphone|ipod|android|blackberry|windows phone", re.IGNORECASE)

BROWSERS = (
    ("Edge", re.compile(r"edg(e|a|ios)?/", re.IGNORECASE)),
    ("Opera", re.compile(r"opr/|opera", re.IGNORECASE)),
    ("Samsung Internet", re.compile(r"samsungbrowser", re.IGNORECASE)),
    ("Firefox", re.compile(r"firefox|fxios", re.IGNORECASE)),
    ("Chrome", re.compile(r"chrome|crios", re.IGNORECASE)),
    ("Safari", re.compile(r"safari", re.IGNORECASE)),
)

SYSTEMS = (
    ("iOS", re.compile(r"iphone|ipad|ipod", re.IGNORECASE)),
    ("Android", re.compile(r"android", re.IGNORECASE)),
    ("Windows", re.compile(r"windows", re.IGNORECASE)),
    ("macOS", re.compile(r"macintosh|mac os x", re.IGNORECASE)),
    ("Linux", re.compile(r"linux|x11", re.IGNORECASE)),
)


def parse_user_agent(user_agent: str | None) -> tuple[str, str, str]:
    ua = user_agent or ""
    if TABLET.search(ua):
        device = "tablet"
    elif MOBILE.search(ua):
        device = "mobile"
    else:
        device = "desktop"
    browser = next((name for name, pattern in BROWSERS if pattern.search(ua)), "Other")
    system = next((name for name, pattern in SYSTEMS if pattern.search(ua)), "Other")
    return device, browser, system
