from __future__ import annotations

from collections import Counter

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
    relative = symbol_change - sector_change
    if symbol_change <= -2 and sector_change <= -1:
        return "섹터와 종목이 같이 약해 보유 비중 점검 우선"
    if symbol_change >= 2 and sector_change >= 1:
        return "섹터와 종목이 같이 강함. 추격보다 지지 확인"
    if relative >= 2:
        return "섹터보다 강한 상대강도. 관심 유지"
    if relative <= -2:
        return "섹터보다 약한 상대약세. 원인 확인 전 신규매수 보류"
    if symbol_change < 0 and sector_change > 0:
        return "섹터 강세를 못 따라가므로 보수적 관찰"
    return "가격 반응 확인 후 유지/관찰"


def _relative_strength(symbol_change: float, sector_quote: Quote | None) -> str:
    if not sector_quote:
        return "섹터 비교 불가"
    relative = symbol_change - sector_quote.change_percent
    if relative >= 2:
        return f"섹터 대비 강함({format_change(relative)})"
    if relative <= -2:
        return f"섹터 대비 약함({format_change(relative)})"
    return f"섹터와 유사({format_change(relative)})"


def _portfolio_summary(symbols: list[str], snapshot: MarketSnapshot) -> str:
    sectors = []
    unmapped = 0
    for symbol in symbols:
        sector = SYMBOL_TO_SECTOR.get(symbol)
        if sector and sector in snapshot.sector_quotes:
            sectors.append(sector)
        else:
            unmapped += 1

    if not sectors:
        return "- 섹터 매핑이 부족해 포트폴리오 쏠림을 계산하지 못했습니다."

    counts = Counter(sectors)
    total = len(sectors)
    top_sector, top_count = counts.most_common(1)[0]
    top_name = SECTOR_KO.get(top_sector, top_sector)
    concentration = round((top_count / total) * 100)
    lines = [
        f"- 가장 큰 노출: {top_name} {top_count}/{total}개({concentration}%)",
    ]
    if concentration >= 50:
        lines.append(f"- 경고: {top_name} 쏠림이 커서 해당 섹터 뉴스에 포트폴리오가 민감합니다.")
    else:
        lines.append("- 쏠림: 특정 섹터 집중도는 과도하지 않습니다.")
    if unmapped:
        lines.append(f"- 참고: {unmapped}개 종목은 섹터 매핑이 없어 가격 반응만 확인합니다.")
    return "\n".join(lines)


def build_watchlist_review(symbols: list[str], snapshot: MarketSnapshot) -> tuple[str, list[str]]:
    warnings: list[str] = []
    clean_symbols = list(dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip()))
    if not clean_symbols:
        return "", warnings

    lines = [
        "보유/관심종목 영향",
        "포트폴리오 리스크 요약",
        _portfolio_summary(clean_symbols, snapshot),
        "종목별 판단",
    ]
    for symbol in clean_symbols:
        try:
            close, change_percent = _latest_change(symbol, snapshot)
            sector_quote = _sector_quote_for(symbol, snapshot)
            if sector_quote:
                sector_name = SECTOR_KO.get(sector_quote.name, sector_quote.name)
                sector_text = f"{sector_name} {format_change(sector_quote.change_percent)}"
            else:
                sector_text = "섹터 매핑 없음"
            action = _watch_action(change_percent, sector_quote)
            relative = _relative_strength(change_percent, sector_quote)
            lines.append(
                f"- {symbol}: 종가 ${close:.2f}({format_change(change_percent)}) / "
                f"섹터 {sector_text} / 상대강도 {relative} / 판단: {action}"
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{symbol} 보유/관심종목 분석 실패: {exc}")
    return "\n".join(lines), warnings
