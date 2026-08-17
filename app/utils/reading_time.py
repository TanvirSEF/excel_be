import math


def extract_text(content_json: dict) -> str:
    parts = []
    for block in content_json.get("blocks", []):
        if isinstance(block, dict) and block.get("text"):
            parts.append(block["text"])
    return " ".join(parts)


def reading_time_minutes(content_json: dict, wpm: int = 200) -> int:
    words = len(extract_text(content_json).split())
    return max(1, math.ceil(words / wpm))
