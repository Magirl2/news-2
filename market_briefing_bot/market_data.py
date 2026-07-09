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
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1y&interval=1d"
STOOQ_SYMBOL_OVERRIDES = {
    "^GSPC": "spy.us",
    "^IXIC": "qqq.us",
    "^DJI": "dia.us",
    "SPY": "spy.us",
    "QQQ": "qqq.us",
    "DIA": "dia.us",
    "^VIX": "^vix",
}


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
            item = {
                "date": date.fromisoformat(row["Date"]),
                "close": float(row["Close"]),
            }
            for csv_key, item_key in (
                ("Open", "open"),
                ("High", "high"),
                ("Low", "low"),
                ("Volume", "volume"),
            ):
                value = row.get(csv_key)
                if value not in (None, "", "N/D"):
                    item[item_key] = float(value)
            rows.append(item)
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
    quote = result.get("indicators", {}).get("quote", [{}])[0]
    opens = quote.get("open", [])
    highs = quote.get("high", [])
    lows = quote.get("low", [])
    closes = quote.get("close", [])
    volumes = quote.get("volume", [])
    exchange_tz_name = result.get("meta", {}).get("exchangeTimezoneName", "America/New_York")
    exchange_tz = get_timezone(exchange_tz_name)
    rows = []
    for index, (timestamp, close) in enumerate(zip(timestamps, closes)):
        if close is None:
            continue
        trading_date = datetime.fromtimestamp(timestamp, timezone.utc).astimezone(
            exchange_tz
        ).date()
        item = {"date": trading_date, "close": float(close)}
        for values, key in (
            (opens, "open"),
            (highs, "high"),
            (lows, "low"),
            (volumes, "volume"),
        ):
            if index < len(values) and values[index] is not None:
                item[key] = float(values[index])
        rows.append(item)
    if len(rows) < 2:
        raise RuntimeError(f"{symbol} Yahoo 데이터를 충분히 가져오지 못했습니다.")
    rows.sort(key=lambda item: item["date"])
    return rows


def _stooq_symbol(symbol: str) -> str | None:
    if symbol in STOOQ_SYMBOL_OVERRIDES:
        return STOOQ_SYMBOL_OVERRIDES[symbol]
    if symbol.isalpha():
        return f"{symbol.lower()}.us"
    return None


def _daily_rows_from_sources(
    symbol: str,
    name: str,
    warnings: List[str],
) -> tuple[List[dict], str, str]:
    errors: list[str] = []
    try:
        return fetch_yahoo_daily(symbol), symbol, "Yahoo Finance"
    except Exception as exc:  # noqa: BLE001 - Stooq fallback keeps the briefing usable.
        errors.append(f"Yahoo Finance {symbol}: {exc}")

    stooq_symbol = _stooq_symbol(symbol)
    if stooq_symbol:
        try:
            rows = fetch_stooq_daily(stooq_symbol)
            warnings.append(
                f"확인 필요: {name} Yahoo Finance 조회 실패로 Stooq 보조 데이터를 사용했습니다."
            )
            return rows, stooq_symbol, "Stooq daily CSV"
        except Exception as exc:  # noqa: BLE001 - report the combined failure below.
            errors.append(f"Stooq {stooq_symbol}: {exc}")

    raise RuntimeError("; ".join(errors))


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
            rows, used_symbol, source = _daily_rows_from_sources(symbol, name, warnings)
            current, previous = _pick_latest_row(rows, target_date)
            previous_close = previous["close"]
            close = current["close"]
            change_percent = ((close - previous_close) / previous_close) * 100
            if current["date"] < target_date:
                warnings.append(
                    f"확인 필요: {name} 최신 데이터가 기준일보다 늦게 반영되지 않아 "
                    f"{current['date']} 데이터를 사용했습니다."
                )
            return Quote(
                name=name,
                symbol=used_symbol,
                trading_date=current["date"],
                close=close,
                previous_close=previous_close,
                change_percent=change_percent,
                source=source,
            )
        except Exception as exc:  # noqa: BLE001 - keep trying fallback symbols.
            last_error = exc
            continue
    raise RuntimeError(f"확인 필요: {name} 데이터를 가져오지 못했습니다. {last_error}")


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
    sources = {
        quote.source
        for quote in [*index_quotes.values(), *sector_quotes.values(), *risk_quotes.values()]
    }
    return MarketSnapshot(
        target_date=target_date,
        index_quotes=index_quotes,
        sector_quotes=sector_quotes,
        risk_quotes=risk_quotes,
        warnings=warnings,
        source=" + ".join(sorted(sources)) if sources else "데이터 출처 확인 필요",
    )


def format_change(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.2f}%"
