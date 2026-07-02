from __future__ import annotations

import csv
import io
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Dict, Iterable, List, Sequence

from .timezones import get_timezone


STOOQ_DAILY_URL = "https://stooq.com/q/d/l/?s={symbol}&i=d"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1mo&interval=1d"


INDEX_SYMBOLS = {
    "S&P 500": ["^GSPC", "SPY"],
    "Nasdaq": ["^IXIC", "QQQ"],
    "Dow": ["^DJI", "DIA"],
}


SECTOR_SYMBOLS = {
    "Technology": "XLK",
    "Financials": "XLF",
    "Health Care": "XLV",
    "Consumer Discretionary": "XLY",
    "Communication Services": "XLC",
    "Industrials": "XLI",
    "Consumer Staples": "XLP",
    "Energy": "XLE",
    "Utilities": "XLU",
    "Materials": "XLB",
    "Real Estate": "XLRE",
}


RISK_SYMBOLS = {
    "VIX": ["^VIX"],
    "10Y Yield": ["^TNX"],
    "Dollar": ["DX-Y.NYB"],
    "Oil": ["CL=F"],
}


SECTOR_KO = {
    "Technology": "기술",
    "Financials": "금융",
    "Health Care": "헬스케어",
    "Consumer Discretionary": "임의소비재",
    "Communication Services": "커뮤니케이션",
    "Industrials": "산업재",
    "Consumer Staples": "필수소비재",
    "Energy": "에너지",
    "Utilities": "유틸리티",
    "Materials": "소재",
    "Real Estate": "부동산",
}


RISK_KO = {
    "VIX": "VIX",
    "10Y Yield": "미10년",
    "Dollar": "달러",
    "Oil": "유가",
}


@dataclass(frozen=True)
class Quote:
    name: str
    symbol: str
    trading_date: date
    close: float
    previous_close: float
    change_percent: float
    source: str


@dataclass(frozen=True)
class MarketSnapshot:
    target_date: date
    index_quotes: Dict[str, Quote]
    sector_quotes: Dict[str, Quote]
    risk_quotes: Dict[str, Quote]
    warnings: List[str]
    source: str = "Stooq daily CSV"


def _download_text(url: str, timeout: int = 20) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; market-briefing-bot/0.1)"
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def fetch_stooq_daily(symbol: str) -> List[dict]:
    encoded_symbol = urllib.parse.quote(symbol.lower(), safe="")
    url = STOOQ_DAILY_URL.format(symbol=encoded_symbol)
    text = _download_text(url)
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        if not row.get("Date") or row.get("Close") in (None, "", "N/D"):
            continue
        try:
            rows.append(
                {
                    "date": date.fromisoformat(row["Date"]),
                    "close": float(row["Close"]),
                }
            )
        except ValueError:
            continue
    if len(rows) < 2:
        raise RuntimeError(f"{symbol} 데이터를 충분히 가져오지 못했습니다.")
    rows.sort(key=lambda item: item["date"])
    return rows


def fetch_yahoo_daily(symbol: str) -> List[dict]:
    encoded_symbol = urllib.parse.quote(symbol, safe="")
    url = YAHOO_CHART_URL.format(symbol=encoded_symbol)
    payload = json.loads(_download_text(url))
    result = (payload.get("chart", {}).get("result") or [None])[0]
    if not result:
        error = payload.get("chart", {}).get("error")
        raise RuntimeError(f"{symbol} Yahoo 데이터 오류: {error}")

    timestamps = result.get("timestamp") or []
    closes = (
        result.get("indicators", {})
        .get("quote", [{}])[0]
        .get("close", [])
    )
    exchange_tz_name = result.get("meta", {}).get("exchangeTimezoneName", "America/New_York")
    exchange_tz = get_timezone(exchange_tz_name)
    rows = []
    for timestamp, close in zip(timestamps, closes):
        if close is None:
            continue
        trading_date = datetime.fromtimestamp(timestamp, timezone.utc).astimezone(
            exchange_tz
        ).date()
        rows.append({"date": trading_date, "close": float(close)})
    if len(rows) < 2:
        raise RuntimeError(f"{symbol} Yahoo 데이터를 충분히 가져오지 못했습니다.")
    rows.sort(key=lambda item: item["date"])
    return rows


def _pick_latest_row(rows: Sequence[dict], target_date: date) -> tuple[dict, dict]:
    eligible = [row for row in rows if row["date"] <= target_date]
    if len(eligible) < 2:
        raise RuntimeError("기준일 이전 데이터가 충분하지 않습니다.")
    return eligible[-1], eligible[-2]


def _quote_from_candidates(
    name: str, symbols: Iterable[str], target_date: date, warnings: List[str]
) -> Quote:
    last_error: Exception | None = None
    for symbol in symbols:
        try:
            rows = fetch_yahoo_daily(symbol)
            current, previous = _pick_latest_row(rows, target_date)
            previous_close = previous["close"]
            close = current["close"]
            change_percent = ((close - previous_close) / previous_close) * 100
            if current["date"] < target_date:
                warnings.append(
                    f"{name} 최신 데이터가 기준일보다 늦게 반영될 수 있어 "
                    f"{current['date']} 데이터를 사용했습니다."
                )
            return Quote(
                name=name,
                symbol=symbol,
                trading_date=current["date"],
                close=close,
                previous_close=previous_close,
                change_percent=change_percent,
                source="Yahoo Finance",
            )
        except Exception as exc:  # noqa: BLE001 - keep trying fallback symbols.
            last_error = exc
            continue
    raise RuntimeError(f"{name} 데이터를 가져오지 못했습니다: {last_error}")


def fetch_market_snapshot(target_date: date) -> MarketSnapshot:
    warnings: List[str] = []
    index_quotes = {
        name: _quote_from_candidates(name, symbols, target_date, warnings)
        for name, symbols in INDEX_SYMBOLS.items()
    }
    sector_quotes = {
        name: _quote_from_candidates(name, [symbol], target_date, warnings)
        for name, symbol in SECTOR_SYMBOLS.items()
    }
    risk_quotes: Dict[str, Quote] = {}
    for name, symbols in RISK_SYMBOLS.items():
        try:
            risk_quotes[name] = _quote_from_candidates(name, symbols, target_date, warnings)
        except Exception as exc:  # noqa: BLE001 - keep the briefing usable.
            warnings.append(f"{name} 위험지표를 가져오지 못했습니다: {exc}")
    return MarketSnapshot(
        target_date=target_date,
        index_quotes=index_quotes,
        sector_quotes=sector_quotes,
        risk_quotes=risk_quotes,
        warnings=warnings,
        source="Yahoo Finance chart data",
    )


def format_change(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.2f}%"
