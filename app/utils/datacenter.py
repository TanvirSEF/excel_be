import bisect
import ipaddress
import json
import logging
from time import monotonic

import httpx

from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)

AWS_RANGES_URL = "https://ip-ranges.amazonaws.com/ip-ranges.json"
GCP_RANGES_URL = "https://www.gstatic.com/ipranges/cloud.json"
CACHE_KEY = "analytics:datacenter:v4"
CACHE_TTL = 8 * 24 * 3600
MEMORY_REFRESH_SECONDS = 3600
FETCH_TIMEOUT_SECONDS = 20

_starts: list[int] = []
_ends: list[int] = []
_loaded_at: float | None = None


def parse_aws(payload: dict) -> list[str]:
    return [
        item["ip_prefix"]
        for item in payload.get("prefixes", [])
        if item.get("service") == "EC2" and "ip_prefix" in item
    ]


def parse_gcp(payload: dict) -> list[str]:
    return [item["ipv4Prefix"] for item in payload.get("prefixes", []) if "ipv4Prefix" in item]


def merge_ranges(prefixes: list[str]) -> list[list[int]]:
    spans = []
    for prefix in prefixes:
        try:
            network = ipaddress.ip_network(prefix, strict=False)
        except ValueError:
            continue
        if network.version == 4:
            spans.append((int(network.network_address), int(network.broadcast_address)))
    spans.sort()
    merged: list[list[int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def load_ranges(ranges: list[list[int]]) -> None:
    global _starts, _ends, _loaded_at
    _starts = [item[0] for item in ranges]
    _ends = [item[1] for item in ranges]
    _loaded_at = monotonic()


def contains(ip: str | None) -> bool:
    if not ip or not _starts:
        return False
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if address.version != 4:
        return False
    value = int(address)
    index = bisect.bisect_right(_starts, value) - 1
    return index >= 0 and value <= _ends[index]


async def is_datacenter_ip(ip: str | None) -> bool:
    if _loaded_at is None or monotonic() - _loaded_at > MEMORY_REFRESH_SECONDS:
        try:
            cached = await get_redis().get(CACHE_KEY)
            load_ranges(json.loads(cached) if cached else [])
        except Exception:
            load_ranges([])
    return contains(ip)


async def refresh_ranges() -> int:
    prefixes: list[str] = []
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_SECONDS) as client:
        for url, parse in ((AWS_RANGES_URL, parse_aws), (GCP_RANGES_URL, parse_gcp)):
            try:
                response = await client.get(url)
                response.raise_for_status()
                prefixes.extend(parse(response.json()))
            except Exception:
                logger.warning("Could not fetch datacenter ranges from %s", url)

    ranges = merge_ranges(prefixes)
    if not ranges:
        return 0
    await get_redis().set(CACHE_KEY, json.dumps(ranges), ex=CACHE_TTL)
    load_ranges(ranges)
    return len(ranges)
