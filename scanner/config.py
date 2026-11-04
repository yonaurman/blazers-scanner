"""Scanner settings. Each value can be overridden with an environment variable of the same name."""

import os


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name)
    if not raw:
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


SITE_URL = "https://www.blazersedge.com"

# Matching is case-insensitive and on word boundaries, so "Deni" also covers "deni" and "Deni's"
# but not "denied". "Avidja" is a common misspelling in comments.
KEYWORDS = _env_list("KEYWORDS", ["Deni", "Avdija", "Avidja"])

LOOKBACK_DAYS = _env_int("LOOKBACK_DAYS", 3)
MAX_ARTICLES = _env_int("MAX_ARTICLES", 80)
FETCH_WORKERS = _env_int("FETCH_WORKERS", 4)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT_SEC = 20

# SB Nation's "Cyclone" comment service (replaced Coral in 2026).
CYCLONE_API_URL = "https://beta.sbnation.com/api"
# Blazer's Edge community id; read from each article page when present, this is the fallback.
CYCLONE_COMMUNITY_ID = "86747a8d-95f3-4356-a4d5-1715ef31bbf8"
