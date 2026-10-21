import re

def parse_user_agent(ua: str | None) -> tuple[str | None, str | None, str | None]:
    """
    Fast, zero-dependency User-Agent parser.
    Returns: (device_type, browser, os)
    """
    if not ua or not ua.strip():
        return None, None, None

    ua_lower = ua.lower()

    # 1. Device Type
    if "ipad" in ua_lower or "tablet" in ua_lower or "playbook" in ua_lower or "silk" in ua_lower:
        device_type = "tablet"
    elif any(kw in ua_lower for kw in ("mobile", "iphone", "ipod", "android", "blackberry", "webos")):
        device_type = "mobile"
    else:
        device_type = "desktop"

    # 2. Browser
    browser = "Other"
    if "edg/" in ua_lower or "edge/" in ua_lower:
        browser = "Edge"
    elif "opr/" in ua_lower or "opera" in ua_lower:
        browser = "Opera"
    elif "chrome/" in ua_lower or "crios/" in ua_lower:
        browser = "Chrome"
    elif "firefox/" in ua_lower or "fxios/" in ua_lower:
        browser = "Firefox"
    elif "safari/" in ua_lower and ("version/" in ua_lower or "mobile/" in ua_lower):
        browser = "Safari"
    elif "msie " in ua_lower or "trident/" in ua_lower:
        browser = "IE"

    # 3. Operating System
    os = "Other"
    if "windows nt 10" in ua_lower or "windows nt 11" in ua_lower:
        os = "Windows 10/11"
    elif "windows" in ua_lower:
        os = "Windows"
    elif "iphone" in ua_lower or "ipad" in ua_lower or "ios" in ua_lower:
        os = "iOS"
    elif "mac os x" in ua_lower or "macintosh" in ua_lower:
        os = "macOS"
    elif "android" in ua_lower:
        os = "Android"
    elif "cros" in ua_lower:
        os = "ChromeOS"
    elif "linux" in ua_lower:
        os = "Linux"

    return device_type, browser, os
