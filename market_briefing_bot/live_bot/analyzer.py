from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ..config import REPORTS_DIR, load_config
from ..investment_plan import SECTOR_STOCKS, _interest_plan
from ..market_data import Quote, fetch_yahoo_daily
from ..news import NewsItem, fetch_top_news, korean_news_headline, korean_news_label
from .price_feed import data_timestamp_text


@dataclass(frozen=True)
class LiveAnalysis:
    symbol: str
    plan: object
    related_news: list[NewsItem]
    morning_signal: dict | None
    comparison: str
    data_time: str
    warnings: list[str]


def _sector_for_symbol(symbol: str) -> str:
    symbol = symbol.upper()
    for sector, stocks in SECTOR_STOCKS.items():
        if any(stock_symbol == symbol for stock_symbol, _name in stocks):
            return sector
    return "Technology"


def _name_for_symbol(symbol: str) -> str:
    symbol = symbol.upper()
    for stocks in SECTOR_STOCKS.values():
        for stock_symbol, name in stocks:
            if stock_symbol == symbol:
                return name
    return symbol


def _latest_target_date(symbol: str) -> date:
    rows = fetch_yahoo_daily(symbol)
    return rows[-1]["date"]


def _load_morning_signal(symbol: str, reports_dir: Path = REPORTS_DIR) -> dict | None:
    path = reports_dir / "signals" / "latest.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    symbol = symbol.upper()
    for section in ("interest", "avoid"):
        for item in data.get(section, []):
            if str(item.get("symbol", "")).upper() == symbol:
                return item
    return None


def _comparison_text(signal: dict | None, plan: object) -> str:
    if not signal:
        return "아침 보고서 판단은 아직 연결되지 않았습니다."
    morning_action = signal.get("entry_action") or signal.get("judgement") or signal.get("stance") or "확인 필요"
    morning_position = signal.get("position_mode") or "확인 필요"
    current_action = getattr(plan, "entry_action", "확인 필요")
    current_position = getattr(plan, "position_mode", "확인 필요")
    if morning_action == current_action and morning_position == current_position:
        return f"아침 판단과 큰 변화 없음: {current_action} + {current_position}"
    return (
        "아침 보고서와 달라진 점: "
        f"아침 {morning_action} + {morning_position} → 현재 {current_action} + {current_position}"
    )


def _fetch_related_news(symbol: str, name: str) -> tuple[list[NewsItem], list[str]]:
    try:
        config = load_config()
        items, warnings = fetch_top_news(config.news_rss_urls, max_items=10)
    except Exception as exc:  # noqa: BLE001 - live lookup should still answer with price data.
        return [], [f"뉴스 조회 실패: {exc}"]
    needles = {symbol.lower(), name.lower()}
    related = [
        item
        for item in items
        if any(needle and needle in f"{item.title} {item.description}".lower() for needle in needles)
    ]
    return (related or items[:3])[:3], warnings


def analyze_symbol(symbol: str, *, include_news: bool = True, reports_dir: Path = REPORTS_DIR) -> LiveAnalysis:
    symbol = symbol.upper()
    name = _name_for_symbol(symbol)
    target_date = _latest_target_date(symbol)
    sector_name = _sector_for_symbol(symbol)
    sector_quote = Quote(sector_name, sector_name, target_date, 100.0, 99.0, 1.0, "live-neutral")
    news_items: list[NewsItem] = []
    warnings: list[str] = []
    if include_news:
        news_items, warnings = _fetch_related_news(symbol, name)
    snapshot = _snapshot_stub(target_date)
    plan = _interest_plan(symbol, name, sector_quote, snapshot, news_items)
    morning_signal = _load_morning_signal(symbol, reports_dir)
    return LiveAnalysis(
        symbol=symbol,
        plan=plan,
        related_news=news_items,
        morning_signal=morning_signal,
        comparison=_comparison_text(morning_signal, plan),
        data_time=data_timestamp_text(),
        warnings=warnings,
    )


def _snapshot_stub(target_date: date):
    from ..market_data import MarketSnapshot

    return MarketSnapshot(
        target_date=target_date,
        index_quotes={},
        sector_quotes={},
        risk_quotes={},
        warnings=[],
    )


def news_lines(items: list[NewsItem]) -> list[str]:
    if not items:
        return ["관련 최신 뉴스는 확인되지 않았습니다."]
    return [
        f"{index}. [{korean_news_label(item)}] {korean_news_headline(item)}"
        for index, item in enumerate(items[:3], start=1)
    ]

