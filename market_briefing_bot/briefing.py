from __future__ import annotations

import html
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import REPORTS_DIR, Config
from .market_calendar import current_market_note, last_completed_trading_day
from .market_data import MarketSnapshot, RISK_KO, SECTOR_KO, fetch_market_snapshot, format_change
from .news import (
    NewsItem,
    fetch_top_news,
    korean_news_checkpoints,
    korean_news_headline,
    korean_news_next_signals,
    korean_news_importance,
    korean_news_label,
    korean_news_plain_explanation,
    korean_news_related,
    korean_news_scenario,
    korean_news_sentiment,
    korean_news_thinking_frame,
    korean_news_why_it_matters,
    korean_news_summary,
)
from .timezones import get_timezone
from .earnings_calendar import build_earnings_calendar
from .event_calendar import build_event_calendar
from .professional_review import build_professional_review
from .sec_filings import build_sec_filing_alert
from .watchlist import SYMBOL_ALIASES, WatchlistAction, build_watchlist_actions, build_watchlist_review
from .investment_plan import (
    build_investment_package,
    build_previous_signal_review,
    load_previous_investment_signals,
    write_investment_signals,
)


@dataclass(frozen=True)
class Briefing:
    text: str
    report_path: Path
    html_path: Path
    sources: list[str]
    warnings: list[str]


def _join_quotes(snapshot: MarketSnapshot) -> str:
    parts = []
    for name, quote in snapshot.index_quotes.items():
        parts.append(f"{name} {format_change(quote.change_percent)}")
    return ", ".join(parts)


def _sector_line(quotes: list, count: int = 3) -> str:
    return ", ".join(
        f"{SECTOR_KO.get(quote.name, quote.name)} {format_change(quote.change_percent)}"
        for quote in quotes[:count]
    )


def _quote_line(quotes: dict, names: list[str]) -> str:
    parts = []
    for name in names:
        quote = quotes.get(name)
        if not quote:
            continue
        label = RISK_KO.get(name, name)
        if name == "10Y Yield":
            value = f"{quote.close:.2f}%"
        elif name == "Oil":
            value = f"${quote.close:.2f}"
        else:
            value = f"{quote.close:.2f}"
        parts.append(f"{label} {value}({format_change(quote.change_percent)})")
    return ", ".join(parts) if parts else "위험지표 일부를 가져오지 못했습니다."


def _sector_marker(change_percent: float) -> str:
    if change_percent >= 1.5:
        return "++"
    if change_percent >= 0.3:
        return "+"
    if change_percent <= -1.5:
        return "--"
    if change_percent <= -0.3:
        return "-"
    return "0"


def _sector_overview(quotes: list) -> str:
    return ", ".join(
        f"{_sector_marker(quote.change_percent)} {SECTOR_KO.get(quote.name, quote.name)} {format_change(quote.change_percent)}"
        for quote in quotes
    )


def _sector_reason(strongest: str, weakest: str) -> str:
    growth = {"Technology", "Communication Services", "Consumer Discretionary"}
    defensive = {"Utilities", "Consumer Staples", "Health Care"}
    cyclical = {"Industrials", "Materials", "Financials", "Energy"}

    if strongest == "Communication Services" and weakest == "Technology":
        return "대형 플랫폼주는 강했지만 기술 섹터는 밀려 성장주 안에서도 종목별 차별화가 컸습니다."
    if strongest == "Financials" and weakest in growth:
        return "기술주 부담이 커진 대신 금융 등 경기민감 업종으로 일부 자금이 이동했습니다."
    if strongest in growth:
        return "성장주와 기술주 쪽으로 매수세가 들어온 흐름입니다."
    if strongest in defensive:
        return "방어주가 앞선 만큼 시장이 조심스럽게 움직인 모습입니다."
    if strongest in cyclical:
        return "경기민감 업종에 관심이 몰린 하루로 볼 수 있습니다."
    if weakest in growth:
        return "성장주 부담이 상대적으로 컸던 흐름입니다."
    return "섹터별 온도 차가 뚜렷했던 하루입니다."


def _news_text(news_items: list[NewsItem]) -> str:
    return " ".join(f"{item.title} {item.description}".lower() for item in news_items)


def _sector_driver(sector: str, change_percent: float, snapshot: MarketSnapshot, news_items: list[NewsItem]) -> str:
    news_text = _news_text(news_items)
    vix = snapshot.risk_quotes.get("VIX")
    ten_year = snapshot.risk_quotes.get("10Y Yield")
    dollar = snapshot.risk_quotes.get("Dollar")
    oil = snapshot.risk_quotes.get("Oil")
    direction = "강세" if change_percent >= 0 else "약세"
    is_strong = change_percent >= 0
    magnitude = abs(change_percent)

    if sector == "Technology":
        if not is_strong:
            if magnitude >= 2:
                return f"{direction} 이유: AI 뉴스가 있어도 실제 가격은 크게 밀려 반도체·대형 기술주 차익실현과 밸류에이션 부담이 더 컸습니다."
            if ten_year and ten_year.change_percent >= 0:
                return f"{direction} 이유: 금리 부담이 성장주 밸류에이션을 누르며 기술주 매수세를 제한했습니다."
            return f"{direction} 이유: 호재성 AI 뉴스보다 단기 과열 해소와 대형주 매도 압력이 더 강했습니다."
        if any(word in news_text for word in ("ai", "chip", "semiconductor", "cloud", "compute", "micron", "nvidia")):
            return f"{direction} 이유: AI/반도체·클라우드 뉴스가 성장주 심리를 지지했습니다."
        if ten_year and ten_year.change_percent < 0:
            return f"{direction} 이유: 금리 하락이 성장주 밸류에이션 부담을 낮췄습니다."
        return f"{direction} 이유: 성장주 선호가 이어졌지만 뉴스 확인은 필요합니다."
    if sector == "Communication Services":
        if not is_strong:
            return f"{direction} 이유: 메타·알파벳 등 대형 플랫폼주에 대한 매수세가 약해져 성장주 안에서도 방어가 안 된 흐름입니다."
        if any(word in news_text for word in ("meta", "alphabet", "google", "advertising", "cloud")):
            return f"{direction} 이유: 메타/알파벳 등 대형 플랫폼 뉴스가 섹터 심리에 영향을 줬습니다."
        return f"{direction} 이유: 대형 플랫폼주 수급 변화의 영향으로 볼 수 있습니다."
    if sector == "Consumer Discretionary":
        if not is_strong:
            return f"{direction} 이유: 소비·자동차·전자상거래 같은 경기민감 성장주에 대한 부담이 커진 흐름입니다."
        if ten_year and ten_year.change_percent > 1:
            return f"{direction} 이유: 금리 부담이 소비·자동차·성장 소비주에 압박을 줄 수 있습니다."
        return f"{direction} 이유: 소비심리와 대형 소비주 수급을 같이 봐야 합니다."
    if sector == "Industrials":
        if not is_strong:
            return f"{direction} 이유: 경기민감주 안에서 산업재 수급이 약했고, 방산·인프라 기대가 섹터 전체를 끌어올리지는 못했습니다."
        if any(word in news_text for word in ("defense", "budget", "hypersonic", "infrastructure")):
            return f"{direction} 이유: 방산·인프라 관련 정책 기대가 산업재 수요 기대를 키웠습니다."
        return f"{direction} 이유: 경기민감주로 자금이 일부 이동한 흐름입니다."
    if sector == "Materials":
        if not is_strong:
            return f"{direction} 이유: 달러와 경기 전망 부담이 원자재·소재 수요 기대를 눌렀을 가능성이 큽니다."
        if dollar and dollar.change_percent < 0:
            return f"{direction} 이유: 달러 약세가 원자재·소재주에 우호적으로 작용했을 수 있습니다."
        return f"{direction} 이유: 경기민감 업종 반등과 원자재 가격 기대를 반영한 움직임입니다."
    if sector == "Financials":
        if not is_strong:
            return f"{direction} 이유: 금리 하락이나 경기 둔화 우려가 은행 마진·대출 성장 기대를 약하게 만든 흐름입니다."
        if ten_year and ten_year.change_percent > 0:
            return f"{direction} 이유: 금리 상승은 은행 순이자마진 기대를 높일 수 있습니다."
        return f"{direction} 이유: 금리가 크게 오르지 않아도 금융주로 저가 매수와 경기민감 수급이 들어온 흐름입니다."
    if sector == "Energy":
        if oil:
            oil_direction = "상승" if oil.change_percent > 0 else "하락"
            if not is_strong:
                return f"{direction} 이유: 유가 {oil_direction}({format_change(oil.change_percent)}) 영향으로 에너지주 이익 기대가 눌렸습니다."
            return f"{direction} 이유: 유가 {oil_direction}({format_change(oil.change_percent)})이 에너지주 심리에 직접 영향을 줬습니다."
        return f"{direction} 이유: 유가와 에너지 수급 뉴스 확인이 필요합니다."
    if sector in {"Utilities", "Consumer Staples", "Health Care"}:
        if vix and vix.change_percent < 0 and change_percent < 0:
            return f"{direction} 이유: VIX 하락으로 방어주 선호가 약해지고 성장주로 자금이 이동했습니다."
        if change_percent >= 0:
            return f"{direction} 이유: 방어주 선호가 살아 있어 시장이 조심스러운 상태일 수 있습니다."
        return f"{direction} 이유: 위험선호가 커지며 방어 업종 비중이 줄어든 흐름입니다."
    if sector == "Real Estate":
        if ten_year and ten_year.change_percent >= 0:
            return f"{direction} 이유: 금리 부담이 부동산 섹터 밸류에이션에 압박으로 작용했습니다."
        return f"{direction} 이유: 금리와 배당 매력 변화에 민감한 업종입니다."
    return f"{direction} 이유: 섹터별 자금 이동 영향으로 보입니다."


def _sector_driver_card(sectors: list, snapshot: MarketSnapshot, news_items: list[NewsItem]) -> str:
    if not sectors:
        return "섹터 이유\n섹터 데이터를 가져오지 못했습니다."
    strongest = sectors[0]
    weakest = sectors[-1]
    top_name = SECTOR_KO.get(strongest.name, strongest.name)
    weak_name = SECTOR_KO.get(weakest.name, weakest.name)
    return (
        "섹터 이유\n"
        f"좋았던 {top_name}: {_sector_driver(strongest.name, strongest.change_percent, snapshot, news_items)}\n"
        f"나빴던 {weak_name}: {_sector_driver(weakest.name, weakest.change_percent, snapshot, news_items)}"
    )


def _theme_from_snapshot(snapshot: MarketSnapshot, news_items: list[NewsItem]) -> str:
    sorted_sectors = sorted(
        snapshot.sector_quotes.values(), key=lambda quote: quote.change_percent, reverse=True
    )
    top_sector = sorted_sectors[0].name if sorted_sectors else ""
    news_text = " ".join(item.title.lower() for item in news_items)

    if "nvidia" in news_text or "ai" in news_text or top_sector == "Technology":
        return "AI/반도체와 대형 기술주"
    if "fed" in news_text or "inflation" in news_text or "rate" in news_text:
        return "금리와 물가 지표"
    if top_sector == "Energy" or "oil" in news_text:
        return "유가와 에너지 업종"
    if top_sector in {"Utilities", "Consumer Staples", "Health Care"}:
        return "방어주와 변동성 관리"
    return f"{SECTOR_KO.get(top_sector, top_sector)} 섹터"


def _one_line(snapshot: MarketSnapshot) -> str:
    index_changes = [quote.change_percent for quote in snapshot.index_quotes.values()]
    sector_changes = sorted(
        snapshot.sector_quotes.values(), key=lambda quote: quote.change_percent, reverse=True
    )
    top = sector_changes[0].name if sector_changes else ""
    avg_index = sum(index_changes) / len(index_changes)

    if avg_index > 0.4 and top in {"Technology", "Communication Services", "Consumer Discretionary"}:
        return "위험선호가 살아난 가운데 성장주 중심의 탄력이 돋보였습니다."
    if avg_index < -0.4:
        return "전반적으로 부담이 커진 하루라 방어적인 해석이 필요합니다."
    if top in {"Utilities", "Consumer Staples", "Health Care"}:
        return "지수보다 방어 섹터의 상대 강도가 더 눈에 띕니다."
    return "큰 방향성보다 섹터별 차별화가 더 중요한 하루였습니다."


def _sector_breadth(snapshot: MarketSnapshot) -> str:
    quotes = list(snapshot.sector_quotes.values())
    up = sum(1 for quote in quotes if quote.change_percent > 0)
    down = sum(1 for quote in quotes if quote.change_percent < 0)
    return f"상승 {up} / 하락 {down}"


def _risk_regime(snapshot: MarketSnapshot) -> tuple[str, str]:
    index_changes = [quote.change_percent for quote in snapshot.index_quotes.values()]
    avg_index = sum(index_changes) / len(index_changes)
    vix = snapshot.risk_quotes.get("VIX")
    ten_year = snapshot.risk_quotes.get("10Y Yield")
    dollar = snapshot.risk_quotes.get("Dollar")
    oil = snapshot.risk_quotes.get("Oil")

    risk_points = 0
    if avg_index > 0.4:
        risk_points += 2
    elif avg_index < -0.4:
        risk_points -= 2
    if vix:
        if vix.change_percent < -3:
            risk_points += 1
        elif vix.change_percent > 3:
            risk_points -= 1
    if ten_year and ten_year.change_percent > 1.5:
        risk_points -= 1
    if dollar and dollar.change_percent > 0.4:
        risk_points -= 1
    if oil and oil.change_percent > 2:
        risk_points -= 1

    if risk_points >= 2:
        return "위험선호", "성장주/반도체 강세가 이어질 수 있지만 과열 여부는 거래량으로 확인"
    if risk_points <= -2:
        return "방어 우위", "신규 추격보다 현금 비중과 손절 기준을 먼저 점검"
    return "선별장", "지수보다 강한 섹터와 약한 섹터의 차별화에 집중"


def _risk_card(snapshot: MarketSnapshot) -> str:
    regime, action = _risk_regime(snapshot)
    return (
        "위험판\n"
        f"판단: {regime}\n"
        f"지표: {_quote_line(snapshot.risk_quotes, ['VIX', '10Y Yield', 'Dollar', 'Oil'])}\n"
        f"해석: {action}"
    )


def _today_decision(snapshot: MarketSnapshot, sectors: list, news_items: list[NewsItem]) -> str:
    regime, action = _risk_regime(snapshot)
    strong = _sector_line(sectors[:2], count=2) if sectors else "확인 불가"
    weak = _sector_line(list(reversed(sectors[-2:])), count=2) if sectors else "확인 불가"
    theme = _theme_from_snapshot(snapshot, news_items)
    return (
        "오늘의 결론\n"
        f"시장 모드: {regime}\n"
        f"우선 볼 섹터: {strong}\n"
        f"조심할 섹터: {weak}\n"
        f"핵심 테마: {theme}\n"
        f"행동 원칙: {action}"
    )


def _quick_takeaways(snapshot: MarketSnapshot, sectors: list, news_items: list[NewsItem]) -> list[tuple[str, str]]:
    regime, action = _risk_regime(snapshot)
    strong = _sector_line(sectors[:2], count=2) if sectors else "확인 불가"
    weak = _sector_line(list(reversed(sectors[-2:])), count=2) if sectors else "확인 불가"
    theme = _theme_from_snapshot(snapshot, news_items)
    return [
        ("시장 판단", f"{regime}: {action}"),
        ("우선 볼 섹터", f"{strong} / 핵심 테마: {theme}"),
        ("조심할 것", f"{weak} 약세 확산 여부와 VIX/금리 방향 확인"),
    ]


def _quick_takeaways_text(snapshot: MarketSnapshot, sectors: list, news_items: list[NewsItem]) -> str:
    lines = ["오늘 3줄 결론"]
    lines.extend(f"{label}: {value}" for label, value in _quick_takeaways(snapshot, sectors, news_items))
    return "\n".join(lines)


def _sentiment_points(sentiment: str) -> int:
    if sentiment == "긍정":
        return 2
    if sentiment == "중립+":
        return 1
    if sentiment == "중립-":
        return -1
    if sentiment == "부정":
        return -2
    return 0


def _importance_points(importance: str) -> int:
    if importance.startswith("A"):
        return 3
    if importance.startswith("B"):
        return 2
    return 1


def _ranked_news_items(news_items: list[NewsItem]) -> list[NewsItem]:
    return sorted(
        news_items,
        key=lambda item: (
            _importance_points(korean_news_importance(item)[0]),
            abs(_sentiment_points(korean_news_sentiment(item)[0])),
            item.score,
        ),
        reverse=True,
    )


def _news_market_read(news_items: list[NewsItem]) -> tuple[str, str]:
    if not news_items:
        return "뉴스 부족", "뉴스 피드가 부족해 가격과 섹터맵을 더 신뢰해야 합니다."

    score = sum(_sentiment_points(korean_news_sentiment(item)[0]) for item in news_items)
    a_count = sum(1 for item in news_items if korean_news_importance(item)[0].startswith("A"))
    if score >= 3 and a_count >= 1:
        return "우호적", "주도 테마가 가격으로 확인되면 관심 후보를 우선 검토합니다."
    if score <= -2:
        return "경계", "좋은 뉴스보다 리스크가 크므로 신규 추격보다 방어와 손절 기준을 먼저 봅니다."
    return "혼재", "뉴스 방향이 갈리므로 지수보다 섹터와 종목별 상대강도를 기준으로 판단합니다."


def _news_dashboard(snapshot: MarketSnapshot, news_items: list[NewsItem]) -> str:
    ranked_items = _ranked_news_items(news_items)
    read, action = _news_market_read(news_items)
    label_counts = Counter(korean_news_label(item) for item in news_items)
    importance_counts = Counter(korean_news_importance(item)[0] for item in news_items)
    main_themes = ", ".join(label for label, _count in label_counts.most_common(3)) or "확인 불가"
    top_lines = [
        f"{index}. [{korean_news_label(item)}] {korean_news_headline(item)}"
        for index, item in enumerate(ranked_items[:3], start=1)
    ]
    if not top_lines:
        top_lines = ["1. 주요 뉴스 없음"]

    regime, _regime_action = _risk_regime(snapshot)
    invalidation = "확인 불가"
    if read == "우호적":
        invalidation = "A급 뉴스가 좋아도 관련 ETF가 약하거나 VIX가 급등하면 추격 매수 관점을 낮춥니다."
    elif read == "경계":
        invalidation = "부정 뉴스에도 지수가 버티고 강세 섹터가 확산되면 방어 일변도 관점을 완화합니다."
    elif read == "혼재":
        invalidation = "혼재 장세에서는 한쪽 방향으로 베팅하기보다 강한 섹터가 2일 이상 이어지는지 확인합니다."

    return (
        "뉴스 종합판\n"
        f"뉴스 기류: {read}\n"
        f"시장 모드와 조합: {regime}\n"
        f"A급/B급/C급: {importance_counts.get('A급', 0)} / {importance_counts.get('B급', 0)} / {importance_counts.get('C급', 0)}\n"
        f"핵심 테마: {main_themes}\n"
        f"먼저 읽을 뉴스:\n" + "\n".join(f"- {line}" for line in top_lines) + "\n"
        f"오늘 행동: {action}\n"
        f"무효화 조건: {invalidation}"
    )


def _shorten(text: str, max_chars: int) -> str:
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip(" ,./") + "…"


def _source_name(source: str) -> str:
    lowered = source.lower()
    if "marketwatch" in lowered:
        return "MarketWatch"
    if "cnbc" in lowered or "top news" in lowered:
        return "CNBC"
    if "federal" in lowered:
        return "Fed"
    return _shorten(source, 14)


def _importance_badge_class(importance: str) -> str:
    if importance.startswith("A"):
        return "importance-a"
    if importance.startswith("B"):
        return "importance-b"
    return "importance-c"


def _news_label_sector(label: str) -> str | None:
    label_to_sector = {
        "AI/반도체": "Technology",
        "AI/클라우드": "Technology",
        "소프트웨어": "Technology",
        "실적": "Technology",
        "방산": "Industrials",
        "에너지": "Energy",
        "금리/물가": "Technology",
        "채권": "Technology",
        "고용": "Technology",
        "ETF/수급": "Technology",
        "시장": "Technology",
    }
    return label_to_sector.get(label)


def _news_impact_badge_class(impact: str) -> str:
    if impact == "직접 영향":
        return "impact-direct"
    if impact == "간접 영향":
        return "impact-indirect"
    return "impact-reference"


def _news_impact_classification(
    item: NewsItem,
    watchlist_actions: list[WatchlistAction],
) -> tuple[str, str]:
    text = f"{item.title} {item.description}".lower()
    for action in watchlist_actions:
        symbol = action.symbol.lower()
        aliases = [alias.lower() for alias in SYMBOL_ALIASES.get(action.symbol, [])]
        if symbol in text or any(alias and alias in text for alias in aliases):
            return "직접 영향", f"관심종목 {action.symbol}가 뉴스에 직접 언급됐습니다."

    label = korean_news_label(item)
    sector = _news_label_sector(label)
    if sector:
        sector_name = SECTOR_KO.get(sector, sector)
        affected_symbols = [
            action.symbol
            for action in watchlist_actions
            if action.sector == sector
        ]
        if affected_symbols:
            return "간접 영향", f"{sector_name} 섹터 뉴스라 관심종목 {', '.join(affected_symbols[:4])}에 간접 영향이 있습니다."
        return "간접 영향", f"{sector_name} 섹터 또는 주요 지수에 영향을 줄 수 있습니다."

    return "참고만", "관심종목이나 주요 섹터와 직접 연결이 약해 참고 재료로 봅니다."


def _first_checkpoint(item: NewsItem) -> str:
    checkpoints = korean_news_checkpoints(item)
    return checkpoints[0] if checkpoints else "다음 거래일 가격과 거래량 반응 확인"


def _news_price_reaction(item: NewsItem, snapshot: MarketSnapshot) -> str:
    label = korean_news_label(item)
    sentiment, _reason = korean_news_sentiment(item)
    label_to_sector = {
        "AI/반도체": "Technology",
        "AI/클라우드": "Technology",
        "소프트웨어": "Technology",
        "실적": "Technology",
        "에너지": "Energy",
        "방산": "Industrials",
        "ETF/수급": "Technology",
        "시장": "Technology",
    }
    sector_name = label_to_sector.get(label)
    sector_quote = snapshot.sector_quotes.get(sector_name) if sector_name else None
    if sector_quote:
        sector_label = SECTOR_KO.get(sector_quote.name, sector_quote.name)
        if sentiment in {"긍정", "중립+"} and sector_quote.change_percent > 0:
            return f"{sector_label} 가격도 강해 뉴스가 가격에 일부 인정받았습니다."
        if sentiment in {"긍정", "중립+"} and sector_quote.change_percent < 0:
            return f"뉴스는 우호적이지만 {sector_label} 가격은 약해 기대 선반영/차익실현 가능성을 봅니다."
        if sentiment in {"부정", "중립-"} and sector_quote.change_percent > 0:
            return f"뉴스는 부담이지만 {sector_label} 가격이 버텨 악재 소화 여부를 확인합니다."
        if sentiment in {"부정", "중립-"} and sector_quote.change_percent < 0:
            return f"뉴스와 {sector_label} 가격이 모두 약해 위험 신호로 봅니다."
        return f"{sector_label} 가격 반응은 아직 뚜렷하지 않습니다."

    if label in {"금리/물가", "채권", "고용"}:
        ten_year = snapshot.risk_quotes.get("10Y Yield")
        if ten_year and ten_year.change_percent > 0:
            return f"10년물 금리 상승({format_change(ten_year.change_percent)})으로 성장주 부담을 확인합니다."
        if ten_year and ten_year.change_percent < 0:
            return f"10년물 금리 하락({format_change(ten_year.change_percent)})이면 성장주 반응을 확인합니다."
    return "가격 반응은 관련 ETF와 대형주 움직임으로 재확인합니다."


def _news_card(
    index: int,
    item: NewsItem,
    snapshot: MarketSnapshot,
    max_chars: int = 168,
    watchlist_actions: list[WatchlistAction] | None = None,
) -> str:
    label = korean_news_label(item)
    headline = korean_news_headline(item)
    sentiment, reason = korean_news_sentiment(item)
    importance, importance_reason = korean_news_importance(item)
    impact, impact_reason = _news_impact_classification(item, watchlist_actions or [])
    price_reaction = _news_price_reaction(item, snapshot)
    bull_case, bear_case = korean_news_scenario(item)
    signals = korean_news_next_signals(item)
    card = (
        f"뉴스 {index}/5 [{label}] {sentiment}\n"
        f"중요도: {importance} - {importance_reason}\n"
        f"영향 분류: {impact} - {impact_reason}\n"
        f"원문: {item.title}\n"
        f"핵심: {headline}\n"
        f"무슨 내용: {korean_news_plain_explanation(item)}\n"
        f"왜 중요: {korean_news_why_it_matters(item)}\n"
        f"투자 해석: {reason} {korean_news_thinking_frame(item)}\n"
        f"가격반응: {price_reaction}\n"
        f"긍정 시나리오: {bull_case}\n"
        f"부정 시나리오: {bear_case}\n"
        f"확인 신호: {' / '.join(signals)}\n"
        f"관련: {korean_news_related(item)}\n"
        f"출처: {item.source} {item.link}"
    )
    if len(card) <= max_chars:
        return card

    compact = (
        f"뉴스 {index}/5 [{label}] {sentiment}\n"
        f"영향 분류: {impact}\n"
        f"핵심: {_shorten(headline, 54)}\n"
        f"무슨 내용: {_shorten(korean_news_plain_explanation(item), 92)}\n"
        f"투자 해석: {_shorten(korean_news_thinking_frame(item), 92)}\n"
        f"확인 신호: {_shorten(' / '.join(signals), 72)}"
    )
    if len(compact) <= max_chars:
        return compact

    return (
        f"뉴스 {index}/5 [{label}] {sentiment}\n"
        f"핵심: {_shorten(headline, 62)}\n"
        f"무슨 내용: {_shorten(korean_news_plain_explanation(item), 86)}\n"
        f"투자 해석: {_shorten(korean_news_thinking_frame(item), 86)}"
    )


def _format_news(
    items: list[NewsItem],
    snapshot: MarketSnapshot,
    watchlist_actions: list[WatchlistAction] | None = None,
) -> list[str]:
    if not items:
        return ["1. 주요 뉴스 RSS를 읽지 못했습니다. 설정과 인터넷 연결을 확인해 주세요."]

    cards = []
    for index, item in enumerate(items[:5], start=1):
        cards.append(
            _news_card(
                index,
                item,
                snapshot,
                max_chars=1100,
                watchlist_actions=watchlist_actions or [],
            )
        )
    return cards


def _news_dashboard_html(snapshot: MarketSnapshot, news_items: list[NewsItem]) -> str:
    ranked_items = _ranked_news_items(news_items)
    read, action = _news_market_read(news_items)
    label_counts = Counter(korean_news_label(item) for item in news_items)
    importance_counts = Counter(korean_news_importance(item)[0] for item in news_items)
    main_themes = ", ".join(label for label, _count in label_counts.most_common(3)) or "확인 불가"
    regime, _regime_action = _risk_regime(snapshot)
    top_items = "".join(
        f"<li><b>{index}. {html.escape(korean_news_label(item))}</b> {html.escape(korean_news_headline(item))}</li>"
        for index, item in enumerate(ranked_items[:3], start=1)
    )
    if not top_items:
        top_items = "<li>주요 뉴스 없음</li>"

    if read == "우호적":
        invalidation = "관련 ETF가 약하거나 VIX가 급등하면 추격 매수 관점을 낮춥니다."
        read_class = "read-positive"
    elif read == "경계":
        invalidation = "부정 뉴스에도 지수가 버티고 강세 섹터가 확산되면 방어 일변도 관점을 완화합니다."
        read_class = "read-negative"
    else:
        invalidation = "강한 섹터가 2일 이상 이어지는지 확인하기 전까지 선별 접근합니다."
        read_class = "read-mixed"

    return f"""
    <section class="news-dashboard">
      <div class="dashboard-head">
        <span class="read-badge {read_class}">{html.escape(read)}</span>
        <div>
          <h2>뉴스 종합판</h2>
          <p>개별 뉴스를 읽기 전, 오늘 뉴스가 시장을 어느 쪽으로 밀고 있는지 먼저 보는 영역입니다.</p>
        </div>
      </div>
      <div class="dashboard-grid">
        <div><b>시장 조합</b><span>{html.escape(regime)}</span></div>
        <div><b>A/B/C급</b><span>{importance_counts.get('A급', 0)} / {importance_counts.get('B급', 0)} / {importance_counts.get('C급', 0)}</span></div>
        <div><b>핵심 테마</b><span>{html.escape(main_themes)}</span></div>
      </div>
      <div class="dashboard-action"><b>오늘 행동</b><span>{html.escape(action)}</span></div>
      <div class="dashboard-action"><b>무효화 조건</b><span>{html.escape(invalidation)}</span></div>
      <div class="priority-news"><b>먼저 읽을 뉴스</b><ol>{top_items}</ol></div>
    </section>
    """


def _watchlist_actions_text(actions: list[WatchlistAction]) -> str:
    if not actions:
        return "관심종목별 오늘 대응\n- 관심종목이 설정되지 않았거나 가격 데이터를 가져오지 못했습니다."
    lines = ["관심종목별 오늘 대응"]
    for action in actions:
        lines.append(
            f"- {action.symbol}: {action.stance} / 오늘 확인 가격: {action.check_price} / "
            f"관련 섹터: {action.sector_text} / 뉴스 영향: {action.news_impact} / 주의 이유: {action.caution}"
        )
    return "\n".join(lines)


def _mobile_quick_summary_html(
    snapshot: MarketSnapshot,
    sectors: list,
    news_items: list[NewsItem],
    watchlist_actions: list[WatchlistAction],
) -> str:
    takeaway_items = "".join(
        f"<div><b>{html.escape(label)}</b><span>{html.escape(value)}</span></div>"
        for label, value in _quick_takeaways(snapshot, sectors, news_items)
    )
    if watchlist_actions:
        watch_items = "".join(
            f"""
            <li>
              <strong>{html.escape(action.symbol)}</strong>
              <span class="stance stance-{html.escape(action.stance)}">{html.escape(action.stance)}</span>
              <small>{html.escape(action.check_price)}</small>
              <small>{html.escape(action.news_impact)}</small>
            </li>
            """
            for action in watchlist_actions[:8]
        )
    else:
        watch_items = "<li><strong>관심종목 없음</strong><small>WATCHLIST_SYMBOLS를 넣으면 종목별 대응이 표시됩니다.</small></li>"

    read, action_text = _news_market_read(news_items)
    return f"""
    <section class="quick-summary">
      <div class="quick-head">
        <p class="eyebrow">Mobile Quick View</p>
        <h2>빠른 요약</h2>
        <p>휴대폰에서 먼저 볼 핵심만 모았습니다. 아래 상세 보고서는 근거 확인용입니다.</p>
      </div>
      <div class="three-lines">{takeaway_items}</div>
      <div class="quick-split">
        <div class="quick-panel">
          <b>뉴스 기류</b>
          <span>{html.escape(read)}</span>
          <small>{html.escape(action_text)}</small>
        </div>
        <div class="quick-panel">
          <b>상세 확인 순서</b>
          <span>3줄 결론 → 관심종목 → 뉴스 종합판 → 상세 보고서</span>
          <small>시간이 없으면 여기까지만 봐도 됩니다.</small>
        </div>
      </div>
      <div class="watch-actions">
        <b>관심종목별 오늘 대응</b>
        <ul>{watch_items}</ul>
      </div>
    </section>
    """


def _today_checklist(snapshot: MarketSnapshot, news_items: list[NewsItem]) -> str:
    sectors = sorted(
        snapshot.sector_quotes.values(), key=lambda quote: quote.change_percent, reverse=True
    )
    top_sector = SECTOR_KO.get(sectors[0].name, sectors[0].name) if sectors else "강세 섹터"
    weak_sector = SECTOR_KO.get(sectors[-1].name, sectors[-1].name) if sectors else "약세 섹터"
    theme = _theme_from_snapshot(snapshot, news_items)
    return (
        "오늘 체크리스트\n"
        f"1. {top_sector} 강세가 다음날도 이어지는지\n"
        f"2. {weak_sector} 약세가 시장 부담으로 번지는지\n"
        f"3. {theme} 거래량과 VIX 방향이 맞는지"
    )


def _render_report_body_lines(lines: list[str]) -> str:
    html_parts: list[str] = []
    in_list = False

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            html_parts.append("</ul>")
            in_list = False

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            close_list()
            continue
        if line.startswith("- "):
            if not in_list:
                html_parts.append('<ul class="report-list">')
                in_list = True
            html_parts.append(f"<li>{html.escape(line[2:])}</li>")
            continue

        close_list()
        if len(line) > 3 and line[0].isdigit() and ". " in line[:5]:
            html_parts.append(f'<p class="numbered-line">{html.escape(line)}</p>')
        elif ":" in line and len(line.split(":", 1)[0]) <= 14:
            label, value = line.split(":", 1)
            html_parts.append(
                f'<p class="key-line"><strong>{html.escape(label)}:</strong>{html.escape(value)}</p>'
            )
        else:
            html_parts.append(f"<p>{html.escape(line)}</p>")

    close_list()
    return "\n".join(html_parts)


def _render_report_sections(text: str) -> str:
    sections = []
    for block in [part.strip() for part in text.split("\n\n") if part.strip()]:
        lines = [line.rstrip() for line in block.splitlines()]
        title = lines[0].strip()
        if title.startswith("뉴스 "):
            continue
        body = _render_report_body_lines(lines[1:])
        class_name = "report-section"
        if (
            "오늘의 결론" in title
            or "전문 투자자 체크" in title
            or "오늘 매매 가능 점수" in title
            or "뉴스 종합판" in title
            or "오늘 3줄 결론" in title
        ):
            class_name += " report-decision"
        elif "이벤트" in title or "SEC 공시" in title or "실적 발표" in title:
            class_name += " report-event"
        elif "핵심 리스크" in title or "섹터 로테이션" in title or "오늘의 경고" in title:
            class_name += " report-event"
        elif "전일 후보 추적" in title:
            class_name += " report-tracking"
        elif "관심" in title:
            class_name += " report-positive"
        elif "비선호" in title or "위험" in title:
            class_name += " report-negative"
        if body:
            sections.append(
                f'<section class="{class_name}"><h2>{html.escape(title)}</h2>{body}</section>'
            )
        else:
            sections.append(
                f'<section class="{class_name} report-heading"><h2>{html.escape(title)}</h2></section>'
            )
    return "\n".join(sections)


def _write_html_report(
    report_path: Path,
    text: str,
    snapshot: MarketSnapshot,
    news_items: list[NewsItem],
    watchlist_actions: list[WatchlistAction],
) -> Path:
    html_path = report_path.with_suffix(".html")
    sectors = sorted(
        snapshot.sector_quotes.values(), key=lambda quote: quote.change_percent, reverse=True
    )
    min_change = min((quote.change_percent for quote in sectors), default=-1)
    max_change = max((quote.change_percent for quote in sectors), default=1)

    def color_for(value: float) -> str:
        if value >= 1.5:
            return "#0f7b3b"
        if value >= 0.3:
            return "#46a758"
        if value <= -1.5:
            return "#b42318"
        if value <= -0.3:
            return "#d92d20"
        return "#667085"

    sector_cards = []
    for quote in sectors:
        span = max(abs(min_change), abs(max_change), 1)
        intensity = min(1, abs(quote.change_percent) / span)
        sector_cards.append(
            f"""
            <section class="sector" style="border-left-color: {color_for(quote.change_percent)}">
              <div class="sector-name">{html.escape(SECTOR_KO.get(quote.name, quote.name))}</div>
              <div class="sector-change">{html.escape(format_change(quote.change_percent))}</div>
              <div class="bar"><span style="width: {int(22 + intensity * 78)}%; background: {color_for(quote.change_percent)}"></span></div>
            </section>
            """
        )

    news_cards = []
    for item in news_items[:5]:
        importance, importance_reason = korean_news_importance(item)
        importance_class = _importance_badge_class(importance)
        impact, impact_reason = _news_impact_classification(item, watchlist_actions)
        impact_class = _news_impact_badge_class(impact)
        sentiment, sentiment_reason = korean_news_sentiment(item)
        bull_case, bear_case = korean_news_scenario(item)
        signals = korean_news_next_signals(item)
        signal_items = "".join(f"<li>{html.escape(signal)}</li>" for signal in signals)
        news_cards.append(
            f"""
            <li>
              <strong>{html.escape(korean_news_label(item))}: {html.escape(korean_news_headline(item))}</strong>
              <span class="original-title">원문: {html.escape(item.title)}</span>
              <span class="importance-line">중요도 <b class="importance-badge {importance_class}">{html.escape(importance)}</b> {html.escape(importance_reason)}</span>
              <span class="impact-line">영향 분류 <b class="impact-badge {impact_class}">{html.escape(impact)}</b> {html.escape(impact_reason)}</span>
              <span><b>무슨 내용:</b> {html.escape(korean_news_plain_explanation(item))}</span>
              <span><b>왜 중요:</b> {html.escape(korean_news_why_it_matters(item))}</span>
              <span><b>투자 해석:</b> <em class="sentiment">{html.escape(sentiment)}</em> - {html.escape(sentiment_reason)} {html.escape(korean_news_thinking_frame(item))}</span>
              <span><b>가격반응:</b> {html.escape(_news_price_reaction(item, snapshot))}</span>
              <span><b>긍정 시나리오:</b> {html.escape(bull_case)}</span>
              <span><b>부정 시나리오:</b> {html.escape(bear_case)}</span>
              <span><b>관련:</b> {html.escape(korean_news_related(item))}</span>
              <div class="signal-block"><b>다음날 확인 신호</b><ul>{signal_items}</ul></div>
              <a href="{html.escape(item.link)}">{html.escape(item.source)}</a>
            </li>
            """
        )

    first_block = next((part.strip() for part in text.split("\n\n") if part.strip()), "")
    first_lines = [line.strip() for line in first_block.splitlines() if line.strip()]
    title_line = first_lines[0] if first_lines else "미국장 마감 보고서"
    market_line = first_lines[1] if len(first_lines) > 1 else ""
    one_line = first_lines[2] if len(first_lines) > 2 else ""
    rendered_sections = _render_report_sections(text)
    news_dashboard = _news_dashboard_html(snapshot, news_items)
    quick_summary = _mobile_quick_summary_html(snapshot, sectors, news_items, watchlist_actions)
    html_text = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title_line)}</title>
  <style>
    :root {{
      --bg: #f4f6f8;
      --panel: #ffffff;
      --ink: #14181f;
      --muted: #667085;
      --line: #d9dee7;
      --blue: #2454a6;
      --green: #0f7b3b;
      --red: #b42318;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: Arial, 'Malgun Gothic', sans-serif; background: var(--bg); color: var(--ink); }}
    main {{ max-width: 1120px; margin: 0 auto; padding: 28px 18px 56px; }}
    .hero {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 28px; margin-bottom: 18px; }}
    .eyebrow {{ margin: 0 0 8px; color: var(--blue); font-size: 13px; font-weight: 700; }}
    h1 {{ margin: 0; font-size: clamp(26px, 4vw, 42px); line-height: 1.15; letter-spacing: 0; }}
    .market-line {{ margin-top: 16px; font-size: 18px; font-weight: 700; }}
    .one-line {{ margin: 8px 0 0; color: var(--muted); font-size: 16px; line-height: 1.55; }}
    h2 {{ margin: 28px 0 12px; font-size: 21px; line-height: 1.3; letter-spacing: 0; }}
    .quick-summary {{ background: #111827; color: #fff; border-radius: 8px; padding: 22px; margin: 18px 0; }}
    .quick-summary .eyebrow {{ color: #93c5fd; margin-bottom: 6px; }}
    .quick-head h2 {{ margin: 0 0 6px; font-size: 24px; }}
    .quick-head p:last-child {{ margin: 0; color: #cbd5e1; line-height: 1.5; }}
    .three-lines {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-top: 16px; }}
    .three-lines div, .quick-panel, .watch-actions {{ background: #1f2937; border: 1px solid #374151; border-radius: 8px; padding: 13px; }}
    .three-lines b, .quick-panel b, .watch-actions b {{ display: block; margin-bottom: 6px; color: #f9fafb; }}
    .three-lines span, .quick-panel span, .watch-actions small {{ color: #d1d5db; line-height: 1.5; }}
    .quick-split {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; margin-top: 10px; }}
    .quick-panel small {{ display: block; color: #9ca3af; margin-top: 5px; line-height: 1.45; }}
    .watch-actions {{ margin-top: 10px; }}
    .watch-actions ul {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 8px; margin: 10px 0 0; padding: 0; list-style: none; }}
    .watch-actions li {{ background: #0f172a; border: 1px solid #334155; border-radius: 8px; padding: 10px; }}
    .watch-actions strong {{ display: inline-block; margin-right: 6px; }}
    .watch-actions small {{ display: block; margin-top: 5px; }}
    .stance {{ display: inline-flex; align-items: center; min-height: 21px; padding: 2px 8px; border-radius: 999px; font-size: 12px; font-weight: 800; background: #f8fafc; color: #111827; }}
    .detail-label {{ margin-top: 28px; padding-top: 20px; border-top: 2px solid #cbd5e1; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; }}
    .sector {{ background: #fff; border: 1px solid #e1e5ec; border-left: 6px solid #667085; border-radius: 8px; padding: 14px; }}
    .sector-name {{ font-weight: 700; margin-bottom: 8px; }}
    .sector-change {{ font-size: 24px; font-weight: 700; margin-bottom: 10px; }}
    .bar {{ height: 8px; background: #edf0f5; border-radius: 999px; overflow: hidden; }}
    .bar span {{ display: block; height: 100%; border-radius: 999px; }}
    .news-dashboard {{ margin-top: 20px; background: #fff; border: 1px solid var(--line); border-radius: 8px; padding: 20px; }}
    .dashboard-head {{ display: flex; gap: 14px; align-items: flex-start; margin-bottom: 14px; }}
    .dashboard-head h2 {{ margin: 0 0 5px; }}
    .dashboard-head p {{ margin: 0; color: var(--muted); line-height: 1.5; }}
    .read-badge {{ flex: 0 0 auto; display: inline-flex; align-items: center; min-height: 28px; padding: 5px 12px; border-radius: 999px; font-weight: 800; font-size: 13px; }}
    .read-positive {{ color: #067647; background: #ecfdf3; border: 1px solid #abefc6; }}
    .read-negative {{ color: #b42318; background: #fff1f3; border: 1px solid #fecdca; }}
    .read-mixed {{ color: #b54708; background: #fffaeb; border: 1px solid #fedf89; }}
    .dashboard-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-bottom: 12px; }}
    .dashboard-grid div, .dashboard-action, .priority-news {{ background: #f8fafc; border: 1px solid #e4e7ec; border-radius: 8px; padding: 12px; }}
    .dashboard-grid b, .dashboard-action b, .priority-news b {{ display: block; color: #1d2939; margin-bottom: 5px; }}
    .dashboard-grid span, .dashboard-action span {{ color: #344054; line-height: 1.5; }}
    .dashboard-action {{ margin-top: 8px; }}
    .priority-news {{ margin-top: 8px; }}
    .priority-news ol {{ margin: 8px 0 0; padding-left: 20px; }}
    .priority-news li {{ margin: 6px 0; line-height: 1.5; }}
    .news-list {{ display: grid; grid-template-columns: 1fr; gap: 12px; margin: 0; padding: 0; list-style: none; }}
    .news-list li {{ border: 1px solid #e1e5ec; border-radius: 8px; padding: 16px; background: #fff; line-height: 1.55; }}
    .news-list strong {{ display: block; margin-bottom: 8px; font-size: 17px; }}
    .news-list span {{ display: block; margin: 5px 0; color: #344054; }}
    .news-list b {{ color: #1d2939; }}
    .news-list em {{ font-style: normal; font-weight: 800; }}
    .original-title {{ color: #667085 !important; font-size: 13px; }}
    .news-list .importance-line, .news-list .impact-line {{ display: flex; flex-wrap: wrap; align-items: center; gap: 6px; color: #1d2939; }}
    .importance-badge {{ display: inline-flex; align-items: center; min-height: 22px; padding: 2px 9px; border-radius: 999px; border: 1px solid transparent; font-size: 12px; font-weight: 800; line-height: 1; }}
    .importance-a {{ color: #b42318; background: #fff1f3; border-color: #fecdca; }}
    .importance-b {{ color: #b54708; background: #fffaeb; border-color: #fedf89; }}
    .importance-c {{ color: #175cd3; background: #eff8ff; border-color: #b2ddff; }}
    .impact-badge {{ display: inline-flex; align-items: center; min-height: 22px; padding: 2px 9px; border-radius: 999px; border: 1px solid transparent; font-size: 12px; font-weight: 800; line-height: 1; }}
    .impact-direct {{ color: #b42318; background: #fff1f3; border-color: #fecdca; }}
    .impact-indirect {{ color: #b54708; background: #fffaeb; border-color: #fedf89; }}
    .impact-reference {{ color: #175cd3; background: #eff8ff; border-color: #b2ddff; }}
    .signal-block {{ margin-top: 10px; padding: 12px; background: #f8fafc; border: 1px solid #e4e7ec; border-radius: 8px; }}
    .signal-block ul {{ margin: 8px 0 0; padding-left: 20px; }}
    .signal-block li {{ margin: 4px 0; padding: 0; border: 0; border-radius: 0; background: transparent; line-height: 1.45; }}
    a {{ color: var(--blue); text-decoration: none; font-weight: 700; }}
    .report-flow {{ display: grid; gap: 0; }}
    .report-section {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 22px; margin-top: 16px; line-height: 1.65; }}
    .report-section h2 {{ margin-top: 0; }}
    .report-section p {{ margin: 8px 0; }}
    .report-heading {{ background: #111827; color: #fff; border-color: #111827; }}
    .report-heading h2 {{ margin: 0; }}
    .report-decision {{ border-left: 6px solid var(--blue); background: #f8fbff; }}
    .report-event {{ border-left: 6px solid #b54708; background: #fffbf5; }}
    .report-tracking {{ border-left: 6px solid #7a5af8; background: #fbfaff; }}
    .report-positive {{ border-left: 6px solid var(--green); }}
    .report-negative {{ border-left: 6px solid var(--red); }}
    .report-list {{ margin: 8px 0 12px; padding-left: 20px; }}
    .report-list li {{ margin: 6px 0; }}
    .numbered-line {{ margin-top: 16px !important; padding-top: 14px; border-top: 1px solid #edf0f5; font-weight: 700; }}
    .key-line strong {{ display: inline-block; min-width: 86px; color: #344054; }}
    footer {{ margin-top: 24px; color: var(--muted); font-size: 13px; text-align: center; }}
    @media (max-width: 680px) {{
      main {{ padding: 18px 12px 44px; }}
      .hero, .report-section {{ padding: 18px; }}
      .quick-summary {{ padding: 18px; }}
      .three-lines, .quick-split {{ grid-template-columns: 1fr; }}
      .dashboard-head {{ display: block; }}
      .read-badge {{ margin-bottom: 10px; }}
      .market-line {{ font-size: 16px; }}
    }}
  </style>
</head>
<body>
  <main>
    <header class="hero">
      <p class="eyebrow">Daily US Market Briefing</p>
      <h1>{html.escape(title_line)}</h1>
      <div class="market-line">{html.escape(market_line)}</div>
      <p class="one-line">{html.escape(one_line)}</p>
    </header>
    {quick_summary}
    <h2>섹터맵</h2>
    <div class="grid">{''.join(sector_cards)}</div>
    {news_dashboard}
    <h2>주요 뉴스 분석</h2>
    <ol class="news-list">{''.join(news_cards)}</ol>
    <h2 class="detail-label">상세 보고서</h2>
    <div class="report-flow">{rendered_sections}</div>
    <footer>Source: Yahoo Finance, RSS feeds. This report is rule-based market reference material.</footer>
  </main>
</body>
</html>
"""
    html_path.write_text(html_text, encoding="utf-8")
    return html_path


def build_briefing(config: Config) -> Briefing:
    now_utc = datetime.now(timezone.utc)
    report_tz = get_timezone(config.report_timezone)
    now_local = now_utc.astimezone(report_tz)
    target_date = last_completed_trading_day(now_utc, config.market_timezone)
    market_note = current_market_note(now_utc, config.market_timezone)

    warnings: list[str] = []
    snapshot = fetch_market_snapshot(target_date)
    news_items, news_warnings = fetch_top_news(config.news_rss_urls)
    warnings.extend(snapshot.warnings)
    warnings.extend(news_warnings)

    sectors = sorted(
        snapshot.sector_quotes.values(), key=lambda quote: quote.change_percent, reverse=True
    )
    investment_package = build_investment_package(snapshot, sectors, news_items)
    warnings.extend(investment_package.warnings)
    previous_signals = load_previous_investment_signals(REPORTS_DIR, target_date)
    tracking_text, tracking_warnings = build_previous_signal_review(snapshot, previous_signals)
    warnings.extend(tracking_warnings)
    watchlist_actions, watchlist_action_warnings = build_watchlist_actions(
        config.watchlist_symbols,
        snapshot,
        news_items,
    )
    warnings.extend(watchlist_action_warnings)
    watchlist_text, watchlist_warnings = build_watchlist_review(config.watchlist_symbols, snapshot)
    warnings.extend(watchlist_warnings)
    event_text, event_warnings = build_event_calendar(config.fred_api_key, target_date)
    warnings.extend(event_warnings)
    earnings_text, earnings_warnings = build_earnings_calendar(
        config.watchlist_symbols,
        config.alpha_vantage_api_key,
        target_date,
    )
    warnings.extend(earnings_warnings)
    sec_text, sec_warnings = build_sec_filing_alert(
        config.watchlist_symbols,
        target_date,
        config.sec_user_agent,
    )
    warnings.extend(sec_warnings)
    professional_text = build_professional_review(snapshot, sectors, news_items)
    strongest = sectors[0].name if sectors else ""
    weakest = sectors[-1].name if sectors else ""

    blocks = [
        (
            f"미국장 마감 {target_date.isoformat()}\n"
            f"{_join_quotes(snapshot)}\n"
            f"한줄: {_one_line(snapshot)}"
        ),
        _quick_takeaways_text(snapshot, sectors, news_items),
        _today_decision(snapshot, sectors, news_items),
        _watchlist_actions_text(watchlist_actions),
        _news_dashboard(snapshot, news_items),
        professional_text,
        (
            "섹터맵\n"
            f"강세: {_sector_line(sectors)}\n"
            f"약세: {_sector_line(list(reversed(sectors)))}\n"
            f"폭: {_sector_breadth(snapshot)}\n"
            f"해석: {_sector_reason(strongest, weakest)}"
        ),
        _sector_driver_card(sectors, snapshot, news_items),
        _risk_card(snapshot),
        event_text,
        earnings_text,
        *_format_news(news_items, snapshot, watchlist_actions),
        tracking_text,
        *([watchlist_text] if watchlist_text else []),
        *([sec_text] if sec_text else []),
        investment_package.text,
        _today_checklist(snapshot, news_items),
        "참고: 투자 판단용 참고 정보이며 매수/매도 추천은 아닙니다.\n출처: Yahoo Finance, RSS 뉴스",
    ]

    if warnings:
        blocks.append("확인 필요\n" + "\n".join(f"- {warning}" for warning in warnings[:3]))

    text = "\n\n".join(blocks)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"{target_date.isoformat()}_briefing.md"
    report_path.write_text(text, encoding="utf-8")
    write_investment_signals(REPORTS_DIR, investment_package)
    html_path = _write_html_report(report_path, text, snapshot, news_items, watchlist_actions)

    source_names = [snapshot.source] + sorted({item.source for item in news_items})
    return Briefing(
        text=text,
        report_path=report_path,
        html_path=html_path,
        sources=source_names,
        warnings=warnings,
    )
