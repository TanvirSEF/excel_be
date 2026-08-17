import re
import unicodedata

SLUG_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
