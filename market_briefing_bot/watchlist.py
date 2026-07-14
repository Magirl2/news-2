from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re

from .investment_plan import SECTOR_STOCKS
from .market_data import MarketSnapshot, Quote, SECTOR_KO, fetch_yahoo_daily, format_change
from .news import NewsItem, korean_news_label, korean_news_sentiment


SYMBOL_TO_NAME = {
    symbol: name
    for _sector, stocks in SECTOR_STOCKS.items()
    for symbol, name in stocks
}


SYMBOL_ALIASES = {
    "NVDA": ["nvidia", "엔비디아"],
    "MSFT": ["microsoft", "마이크로소프트"],
    "AAPL": ["apple", "애플"],
    "AVGO": ["broadcom", "브로드컴"],
    "AMD": ["amd"],
    "META": ["meta", "메타"],
    "GOOGL": ["alphabet", "google", "알파벳", "구글"],
    "NFLX": ["netflix", "넷플릭스"],
    "DIS": ["disney", "디즈니"],
    "AMZN": ["amazon", "아마존"],
    "TSLA": ["tesla", "테슬라"],
    "MU": ["micron", "마이크론"],
    "SNDK": ["sandisk", "샌디스크"],
    "ASTS": ["ast spacemobile", "asts"],
    "NVO": ["novo nordisk", "노보 노디스크"],
    "PLTR": ["palantir", "팔란티어"],
    "TSM": ["tsmc", "taiwan semiconductor"],
    "ASML": ["asml"],
    "ARM": ["arm holdings", "arm"],
    "SMCI": ["super micro", "supermicro", "슈퍼마이크로"],
    "RTX": ["rtx", "raytheon"],
    "LMT": ["lockheed", "록히드"],
    "JPM": ["jpmorgan", "jp morgan", "jp모건"],
    "GS": ["goldman", "골드만"],
    "LLY": ["eli lilly", "일라이릴리"],
}


SYMBOL_TO_SECTOR = {
    symbol: sector
    for sector, stocks in SECTOR_STOCKS.items()
    for symbol, _name in stocks
}
SYMBOL_TO_SECTOR.update(
    {
        "MU": "Technology",
        "SNDK": "Technology",
        "ASTS": "Communication Services",
        "NVO": "Health Care",
        "PLTR": "Technology",
        "TSM": "Technology",
        "ASML": "Technology",
        "ARM": "Technology",
        "SMCI": "Technology",
    }
)


def _text_mentions_token(text: str, token: str) -> bool:
    token = token.strip().lower()
    if not token:
        return False
    if " " in token:
        return token in text
    return re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", text) is not None


def text_mentions_symbol_or_alias(text: str, symbol: str) -> bool:
    lower_text = text.lower()
    symbol = symbol.upper()
    tokens = [symbol.lower(), *[alias.lower() for alias in SYMBOL_ALIASES.get(symbol, [])]]
    return any(_text_mentions_token(lower_text, token) for token in tokens)


@dataclass(frozen=True)
class WatchlistAction:
    symbol: str
    sector: str | None
    close: float
    change_percent: float
    stance: str
    check_price: str
    caution: str
    sector_text: str
    relative_strength: str
    news_impact: str


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


def _news_score_for_symbol(symbol: str, sector: str | None, news_items: list[NewsItem]) -> tuple[int, str]:
    if not news_items:
        return 0, "관련 뉴스 확인 없음"

    name_text = SYMBOL_TO_NAME.get(symbol.upper(), "").lower()
    sector_to_labels = {
        "Technology": {"AI/반도체", "AI/클라우드", "소프트웨어", "실적", "시장"},
        "Communication Services": {"AI/클라우드", "시장"},
        "Health Care": {"실적", "시장"},
        "Industrials": {"방산", "시장"},
        "Energy": {"에너지"},
        "Financials": {"금리/물가", "채권", "시장"},
        "Consumer Discretionary": {"시장", "실적"},
        "Consumer Staples": {"시장", "실적"},
        "Utilities": {"채권", "금리/물가"},
        "Materials": {"시장", "실적"},
        "Real Estate": {"채권", "금리/물가"},
    }
    related_labels = sector_to_labels.get(sector or "", set())
    direct_score = 0
    sector_score = 0
    direct_count = 0
    sector_count = 0
    for item in news_items:
        text = f"{item.title} {item.description}".lower()
        sentiment, _reason = korean_news_sentiment(item)
        points = 0
        if sentiment == "긍정":
            points = 2
        elif sentiment == "중립+":
            points = 1
        elif sentiment == "중립-":
            points = -1
        elif sentiment == "부정":
            points = -2

        direct = text_mentions_symbol_or_alias(text, symbol) or bool(
            name_text and _text_mentions_token(text, name_text)
        )
        if direct:
            direct_score += points
            direct_count += 1
            continue
        if korean_news_label(item) in related_labels:
            sector_score += points
            sector_count += 1

    if direct_count:
        if direct_score > 0:
            return direct_score, "직접 긍정 뉴스 영향"
        if direct_score < 0:
            return direct_score, "직접 부정 뉴스 영향"
        return direct_score, "직접 뉴스 영향은 중립"
    if sector_count:
        if sector_score > 0:
            return sector_score, "섹터 관련 긍정 뉴스"
        if sector_score < 0:
            return sector_score, "섹터 관련 부정 뉴스"
        return sector_score, "섹터 관련 뉴스는 중립"
    return 0, "뉴스 영향 제한적"


def _watch_stance(symbol_change: float, sector_quote: Quote | None, news_score: int) -> str:
    sector_change = sector_quote.change_percent if sector_quote else 0.0
    relative = symbol_change - sector_change
    if symbol_change <= -1.5 or relative <= -2 or news_score <= -2:
        return "부정"
    if symbol_change >= 1.5 and relative >= -0.5 and news_score >= 0:
        return "긍정"
    if sector_change >= 1 and symbol_change >= 0 and news_score >= 1:
        return "긍정"
    return "중립"


def _check_price_text(close: float, stance: str) -> str:
    if stance == "긍정":
        return f"${close:.2f} 위에서 지지 확인"
    if stance == "부정":
        return f"${close:.2f} 회복 전 신규매수 보류"
    return f"${close:.2f} 기준 방향 확인"


def build_watchlist_actions(
    symbols: list[str],
    snapshot: MarketSnapshot,
    news_items: list[NewsItem] | None = None,
) -> tuple[list[WatchlistAction], list[str]]:
    warnings: list[str] = []
    clean_symbols = list(dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip()))
    actions: list[WatchlistAction] = []
    for symbol in clean_symbols:
        try:
            close, change_percent = _latest_change(symbol, snapshot)
            sector = SYMBOL_TO_SECTOR.get(symbol)
            sector_quote = _sector_quote_for(symbol, snapshot)
            if sector_quote:
                sector_name = SECTOR_KO.get(sector_quote.name, sector_quote.name)
                sector_text = f"{sector_name} {format_change(sector_quote.change_percent)}"
            else:
                sector_text = "섹터 매핑 없음"
            news_score, news_impact = _news_score_for_symbol(symbol, sector, news_items or [])
            stance = _watch_stance(change_percent, sector_quote, news_score)
            actions.append(
                WatchlistAction(
                    symbol=symbol,
                    sector=sector,
                    close=close,
                    change_percent=change_percent,
                    stance=stance,
                    check_price=_check_price_text(close, stance),
                    caution=_watch_action(change_percent, sector_quote),
                    sector_text=sector_text,
                    relative_strength=_relative_strength(change_percent, sector_quote),
                    news_impact=news_impact,
                )
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{symbol} 보유/관심종목 분석 실패: {exc}")
    return actions, warnings


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
    clean_symbols = list(dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip()))
    if not clean_symbols:
        return "", []

    actions, warnings = build_watchlist_actions(clean_symbols, snapshot)

    lines = [
        "보유/관심종목 영향",
        "포트폴리오 리스크 요약",
        _portfolio_summary(clean_symbols, snapshot),
        "종목별 판단",
    ]
    for action in actions:
        lines.append(
            f"- {action.symbol}: 종가 ${action.close:.2f}({format_change(action.change_percent)}) / "
            f"섹터 {action.sector_text} / 상대강도 {action.relative_strength} / 판단: {action.caution}"
        )
    return "\n".join(lines), warnings
