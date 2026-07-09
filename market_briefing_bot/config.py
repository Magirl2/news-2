from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / ".env"
TOKEN_FILE = PROJECT_ROOT / ".secrets" / "kakao_tokens.json"
REPORTS_DIR = PROJECT_ROOT / "reports"
LOGS_DIR = PROJECT_ROOT / "logs"
SEND_STATE_FILE = LOGS_DIR / "send_state.json"


DEFAULT_RSS_URLS = [
    "https://finance.yahoo.com/news/rssindex",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "https://www.federalreserve.gov/feeds/press_all.xml",
]


@dataclass(frozen=True)
class Config:
    kakao_rest_api_key: str
    kakao_redirect_uri: str
    kakao_client_secret: str
    kakao_link_url: str
    kakao_send_mode: str
    report_public_base_url: str
    report_timezone: str
    market_timezone: str
    kakao_chunk_size: int
    news_rss_urls: List[str]
    watchlist_symbols: List[str]
    fred_api_key: str
    alpha_vantage_api_key: str
    openai_api_key: str
    openai_model: str
    sec_user_agent: str


def _read_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        values[key] = value
    return values


def _get_value(values: Dict[str, str], key: str, default: str = "") -> str:
    return os.environ.get(key) or values.get(key) or default


def _split_urls(raw_value: str) -> List[str]:
    urls = [url.strip() for url in raw_value.split(",") if url.strip()]
    return urls or DEFAULT_RSS_URLS


def _split_list(raw_value: str) -> List[str]:
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def load_config() -> Config:
    env_values = _read_env_file(ENV_FILE)
    chunk_size_raw = _get_value(env_values, "KAKAO_CHUNK_SIZE", "200")
    try:
        chunk_size = max(100, min(200, int(chunk_size_raw)))
    except ValueError:
        chunk_size = 180

    return Config(
        kakao_rest_api_key=_get_value(env_values, "KAKAO_REST_API_KEY"),
        kakao_redirect_uri=_get_value(
            env_values, "KAKAO_REDIRECT_URI", "http://localhost:8765/callback"
        ),
        kakao_client_secret=_get_value(env_values, "KAKAO_CLIENT_SECRET"),
        kakao_link_url=_get_value(
            env_values, "KAKAO_LINK_URL", "https://finance.yahoo.com/markets"
        ),
        kakao_send_mode=_get_value(env_values, "KAKAO_SEND_MODE", "full").lower(),
        report_public_base_url=_get_value(env_values, "REPORT_PUBLIC_BASE_URL"),
        report_timezone=_get_value(env_values, "REPORT_TIMEZONE", "Asia/Seoul"),
        market_timezone=_get_value(env_values, "MARKET_TIMEZONE", "America/New_York"),
        kakao_chunk_size=chunk_size,
        news_rss_urls=_split_urls(_get_value(env_values, "NEWS_RSS_URLS")),
        watchlist_symbols=[symbol.upper() for symbol in _split_list(_get_value(env_values, "WATCHLIST_SYMBOLS"))],
        fred_api_key=_get_value(env_values, "FRED_API_KEY"),
        alpha_vantage_api_key=_get_value(env_values, "ALPHA_VANTAGE_API_KEY"),
        openai_api_key=_get_value(env_values, "OPENAI_API_KEY"),
        openai_model=_get_value(env_values, "OPENAI_MODEL", "gpt-5.5"),
        sec_user_agent=_get_value(
            env_values,
            "SEC_USER_AGENT",
            "market-briefing-bot contact@example.com",
        ),
    )


def ensure_project_dirs() -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
