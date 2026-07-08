from __future__ import annotations

from .investment_plan import SECTOR_STOCKS
from .market_data import MarketSnapshot, Quote, SECTOR_KO, fetch_yahoo_daily, format_change


SYMBOL_TO_SECTOR = {
    symbol: sector
    for sector, stocks in SECTOR_STOCKS.items()
    for symbol, _name in stocks
}


def _latest_change(symbol: str, snapshot: MarketSnapshot) -> tuple[float, float]:
    rows = [row for row in fetch_yahoo_daily(symbol) if row["date"] <= snapshot.target_date]
    if len(rows) < 2:
        raise RuntimeError(f"{symbol} 가격 데이터가 부족합니다.")
    current = float(rows[-1]["close"])
    previous = float(rows[-2]["close"])
    return current, ((current - previous) / previous) * 100


def _sector_quote_for(symbol: str, snapshot: MarketSnapshot) -> Quote | None:
    sector = SYMBOL_TO_SECTOR.get(symbol.upper())
    if not sector:
        return None
    return snapshot.sector_quotes.get(sector)


def _watch_action(symbol_change: float, sector_quote: Quote | None) -> str:
    sector_change = sector_quote.change_percent if sector_quote else 0.0
    if symbol_change <= -2 and sector_change <= -1:
        return "섹터와 종목이 같이 약해 비중 점검 우선"
    if symbol_change >= 2 and sector_change >= 1:
        return "섹터와 종목이 같이 강해 추격보다 지지 확인"
    if symbol_change > 0 and sector_change < 0:
        return "섹터 약세 속 상대강도 확인"
    if symbol_change < 0 and sector_change > 0:
        return "섹터 강세를 못 따라가므로 보수적 관찰"
    return "가격 반응 확인 후 유지/관찰"


def build_watchlist_review(symbols: list[str], snapshot: MarketSnapshot) -> tuple[str, list[str]]:
    warnings: list[str] = []
    clean_symbols = [symbol.strip().upper() for symbol in symbols if symbol.strip()]
    if not clean_symbols:
        return "", warnings

    lines = ["보유/관심종목 영향"]
    for symbol in dict.fromkeys(clean_symbols):
        try:
            close, change_percent = _latest_change(symbol, snapshot)
            sector_quote = _sector_quote_for(symbol, snapshot)
            if sector_quote:
                sector_name = SECTOR_KO.get(sector_quote.name, sector_quote.name)
                sector_text = f"{sector_name} {format_change(sector_quote.change_percent)}"
            else:
                sector_text = "섹터 매핑 없음"
            action = _watch_action(change_percent, sector_quote)
            lines.append(
                f"- {symbol}: 종가 ${close:.2f}({format_change(change_percent)}) / 섹터 {sector_text} / 판단: {action}"
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{symbol} 보유/관심종목 분석 실패: {exc}")
    return "\n".join(lines), warnings
