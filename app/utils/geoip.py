import json
import logging
from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)

GEOIP_CACHE_TTL = 30 * 24 * 3600  # 30 days


async def resolve_geoip(ip: str | None) -> tuple[str | None, str | None, str | None]:
    """
    Resolves an IP address to (country_name, country_code, city).
    Uses Redis caching to ensure each IP is only looked up once.
    """
    if not ip or not ip.strip():
        return None, None, None

    ip_clean = ip.strip()

    # Private / Local IPs
    if ip_clean.startswith(("127.", "192.168.", "10.", "172.16.", "::1", "localhost")):
        return "Local Network", "LO", "Internal"

    cache_key = f"geoip:{ip_clean}"

    try:
        r = get_redis()
        cached = await r.get(cache_key)
        if cached:
            data = json.loads(cached)
            return data.get("country"), data.get("country_code"), data.get("city")
    except Exception:
        pass

    # Query external fast GeoIP
    country = None
    country_code = None
    city = None

    try:
        import httpx

        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(
                f"http://ip-api.com/json/{ip_clean}?fields=status,country,countryCode,city"
            )
            if resp.status_code == 200:
                res = resp.json()
                if res.get("status") == "success":
                    country = res.get("country")
                    country_code = res.get("countryCode")
                    city = res.get("city")
    except Exception as e:
        logger.debug("GeoIP lookup failed for IP %s: %s", ip_clean, e)

    # Cache result if resolved
    if country or country_code:
        try:
            r = get_redis()
            payload = json.dumps({"country": country, "country_code": country_code, "city": city})
            await r.set(cache_key, payload, ex=GEOIP_CACHE_TTL)
        except Exception:
            pass

    return country, country_code, city
