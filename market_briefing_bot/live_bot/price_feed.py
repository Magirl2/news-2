from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..market_data import fetch_yahoo_daily


@dataclass(frozen=True)
class LatestPrice:
    symbol: str
    price: float
    previous_close: float
    change_percent: float
    data_date: object
    source: str


def get_latest_price(symbol: str) -> LatestPrice:
    rows = fetch_yahoo_daily(symbol.upper())
    if len(rows) < 2:
        raise RuntimeError(f"{symbol.upper()} 가격 데이터가 부족합니다.")
    current = rows[-1]
    previous = rows[-2]
    price = float(current["close"])
    previous_close = float(previous["close"])
    change_percent = ((price - previous_close) / previous_close) * 100
    return LatestPrice(
        symbol=symbol.upper(),
        price=price,
        previous_close=previous_close,
        change_percent=change_percent,
        data_date=current["date"],
        source="Yahoo Finance",
    )


def data_timestamp_text() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")

