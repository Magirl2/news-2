from __future__ import annotations

import html
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import REPORTS_DIR, Config
from .ai_news import NewsInterpretation, build_news_interpretations, rule_based_news_interpretation
from .market_calendar import current_market_note, last_completed_trading_day
from .market_data import (
    MarketSnapshot,
    Quote,
    RISK_KO,
    SECTOR_KO,
    fetch_market_snapshot,
    fetch_stooq_daily,
    fetch_yahoo_daily,
    format_change,
)
from .news import (
    NewsItem,
    fetch_top_news,
    korean_news_checkpoints,
    korean_news_headline,
    korean_news_next_signals,
    korean_news_importance,
    korean_news_label,
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
from .watchlist import WatchlistAction, build_watchlist_actions, build_watchlist_review, text_mentions_symbol_or_alias
from .investment_plan import (
    build_investment_package,
    build_previous_signal_review,
    load_previous_investment_signals,
    write_investment_signals,
)
from .selection_review import (
    RecommendationPerformance,
    SignalEvaluation,
    collect_recommendation_performance,
    recommendation_performance_metrics,
    recommendation_performance_read,
    render_recommendation_performance,
)


@dataclass(frozen=True)
class Briefing:
    text: str
    report_path: Path
    html_path: Path
    sources: list[str]
    warnings: list[str]


@dataclass(frozen=True)
class SectorScore:
    sector: str
    label: str
    change_percent: float
    price_score: int
    news_score: int
    rate_score: int
    flow_score: int
    total_score: int
    summary: str
    detail: str


def _join_quotes(snapshot: MarketSnapshot) -> str:
    parts = []
    for name, quote in snapshot.index_quotes.items():
        parts.append(f"{name} {format_change(quote.change_percent)}")
    return ", ".join(parts)


def _quote_group_date_label(quotes: list[Quote]) -> str:
    dates = sorted({quote.trading_date for quote in quotes})
    if not dates:
        return "확인 불가"
    if len(dates) == 1:
        return dates[0].isoformat()
    return f"{dates[0].isoformat()}~{dates[-1].isoformat()}"


def _data_freshness(snapshot: MarketSnapshot) -> tuple[str, str, str]:
    groups = (
        ("지수", list(snapshot.index_quotes.values())),
        ("섹터", list(snapshot.sector_quotes.values())),
        ("위험지표", list(snapshot.risk_quotes.values())),
    )
    basis = " · ".join(
        f"{label} {_quote_group_date_label(quotes)}" for label, quotes in groups
    )
    delayed = [
        label
        for label, quotes in groups
        if any(quote.trading_date < snapshot.target_date for quote in quotes)
    ]
    missing = [label for label, quotes in groups if not quotes]
    if delayed:
        delayed_text = "·".join(delayed)
        return (
            "일부 데이터 지연",
            basis,
            f"{delayed_text}는 {snapshot.target_date.isoformat()} 마감이 아직 반영되지 않았습니다. "
            "해당 순위와 등락은 직전 확인값으로 읽으세요.",
        )
    if missing:
        missing_text = "·".join(missing)
        return (
            "일부 데이터 없음",
            basis,
            f"{missing_text} 데이터를 가져오지 못했습니다. 없는 영역은 판단 근거에서 제외하세요.",
        )
    return (
        "마감 데이터 확인",
        basis,
        f"표시된 데이터가 {snapshot.target_date.isoformat()} 마감 기준과 일치합니다.",
    )


def _data_freshness_text(snapshot: MarketSnapshot) -> str:
    status, basis, guidance = _data_freshness(snapshot)
    return (
        "데이터 기준\n"
        f"상태: {status}\n"
        f"기준일: {basis}\n"
        f"읽는 법: {guidance}"
    )


def _data_freshness_html(snapshot: MarketSnapshot) -> str:
    status, basis, guidance = _data_freshness(snapshot)
    tone = "is-delayed" if status != "마감 데이터 확인" else "is-current"
    return f"""
    <section class="data-freshness {tone}" aria-label="데이터 기준일">
      <div class="freshness-title">
        <strong>데이터 기준</strong>
        <span>{html.escape(status)}</span>
      </div>
      <p>{html.escape(basis)}</p>
      <small>{html.escape(guidance)}</small>
    </section>
    """


def _sector_basis_label(snapshot: MarketSnapshot) -> str:
    quotes = list(snapshot.sector_quotes.values())
    if not quotes:
        return "확인 불가"
    basis = _quote_group_date_label(quotes)
    if any(quote.trading_date < snapshot.target_date for quote in quotes):
        return f"{basis} 종가 기준 · 최신 마감 미반영"
    return f"{basis} 종가 기준"


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
    avg_index = sum(index_changes) / len(index_changes) if index_changes else 0.0
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
    index_changes = [quote.change_percent for quote in snapshot.index_quotes.values()]
    avg_index = sum(index_changes) / len(index_changes) if index_changes else 0.0
    risk_rows = []
    headwinds: list[str] = []
    tailwinds: list[str] = []

    for name in ("VIX", "10Y Yield", "Dollar", "Oil"):
        quote = snapshot.risk_quotes.get(name)
        if not quote:
            continue
        label = RISK_KO.get(name, name)
        value = _risk_value(name, quote)
        reading, direction = _risk_indicator_read(name, quote)
        if direction == "부담":
            headwinds.append(label)
        elif direction == "완화":
            tailwinds.append(label)
        risk_rows.append(
            f"|{label}|{value}|{format_change(quote.change_percent)}|{reading}|"
        )

    if headwinds and tailwinds:
        signal_mix = (
            f"{'·'.join(headwinds)}는 부담이고 {'·'.join(tailwinds)}는 완화 신호라 "
            "한 방향으로 크게 베팅하기보다 지수 반응을 함께 봐야 합니다."
        )
    elif headwinds:
        signal_mix = (
            f"{'·'.join(headwinds)}가 동시에 부담을 주고 있어 반등이 나와도 "
            "추격 매수보다 방어 기준이 우선입니다."
        )
    elif tailwinds:
        signal_mix = (
            f"{'·'.join(tailwinds)}가 위험 부담을 낮추고 있습니다. 다만 실제 위험선호는 "
            "나스닥과 강세 섹터의 거래량까지 확인돼야 인정할 수 있습니다."
        )
    else:
        signal_mix = "위험지표가 뚜렷하게 한쪽으로 기울지 않아 지수와 섹터 가격을 우선합니다."

    table = [
        "|지표|현재|당일 변화|쉽게 읽기|",
        "|---|---|---|---|",
        *risk_rows,
    ]
    if not risk_rows:
        table = ["위험지표 데이터를 충분히 가져오지 못했습니다."]

    return "\n".join(
        [
            "위험판",
            f"한줄 판정: {regime} — {action}",
            (
                f"왜 이렇게 봤나: 주요 지수 평균이 {format_change(avg_index)}였고, "
                f"위험지표를 함께 놓고 보면 {signal_mix}"
            ),
            "지표별 해설",
            *table,
            "다음날 읽는 순서: 1) VIX가 전일 급등분을 되돌리는지 2) 10년물 금리와 달러가 같이 오르는지 3) 유가 움직임이 에너지 강세를 넘어 물가 부담으로 번지는지 확인합니다.",
            f"행동 기준: {action}. 지수 반등과 VIX 안정이 같이 나오기 전에는 포지션 크기를 먼저 제한합니다.",
        ]
    )


def _risk_value(name: str, quote: Quote) -> str:
    if name == "10Y Yield":
        return f"{quote.close:.2f}%"
    if name == "Oil":
        return f"${quote.close:.2f}"
    return f"{quote.close:.2f}"


def _risk_indicator_read(name: str, quote: Quote) -> tuple[str, str]:
    change = quote.change_percent
    if name == "VIX":
        if change >= 5:
            return "공포지수가 빠르게 올라 단기 변동성과 손절 위험이 커졌습니다.", "부담"
        if change <= -5:
            return "공포가 빠르게 진정돼 위험자산에는 숨통이 트이는 신호입니다.", "완화"
        return "변동성 변화가 제한적이라 다른 지표와 함께 봐야 합니다.", "중립"
    if name == "10Y Yield":
        if change >= 1:
            return "장기금리 상승은 고PER 성장주와 금리민감 섹터의 할인율 부담을 키웁니다.", "부담"
        if change <= -1:
            return "장기금리 하락은 성장주 밸류에이션 부담을 덜어줍니다.", "완화"
        return "금리 변화가 작아 당일 섹터 영향은 제한적입니다.", "중립"
    if name == "Dollar":
        if change >= 0.4:
            return "달러 강세는 글로벌 유동성과 해외매출 비중이 큰 기업에 부담입니다.", "부담"
        if change <= -0.4:
            return "달러 약세는 위험자산과 해외매출 기업에 비교적 우호적입니다.", "완화"
        return "달러 방향은 아직 시장을 압도할 정도로 강하지 않습니다.", "중립"
    if name == "Oil":
        if change >= 2:
            return "유가 급등은 에너지주에는 호재지만 시장 전체에는 물가·금리 부담이 될 수 있습니다.", "부담"
        if change <= -2:
            return "유가 하락은 물가 부담을 낮추지만 에너지주에는 역풍입니다.", "완화"
        return "유가 변화가 작아 에너지와 물가 해석 모두 중립에 가깝습니다.", "중립"
    return "다른 시장 지표와 함께 확인해야 합니다.", "중립"


def _today_decision(snapshot: MarketSnapshot, sectors: list, news_items: list[NewsItem]) -> str:
    regime, action = _risk_regime(snapshot)
    strong = _sector_line(sectors[:3], count=3) if sectors else "확인 불가"
    weak = _sector_line(list(reversed(sectors[-2:])), count=2) if sectors else "확인 불가"
    theme = _theme_from_snapshot(snapshot, news_items)
    sector_basis = _sector_basis_label(snapshot)
    return (
        "오늘의 결론\n"
        f"시장 모드: {regime}\n"
        f"우선 볼 섹터: {strong}\n"
        f"조심할 섹터: {weak}\n"
        f"섹터 기준: {sector_basis}\n"
        f"핵심 테마: {theme}\n"
        f"행동 원칙: {action}"
    )


def _quick_takeaways(snapshot: MarketSnapshot, sectors: list, news_items: list[NewsItem]) -> list[tuple[str, str]]:
    regime, action = _risk_regime(snapshot)
    strong = _sector_line(sectors[:3], count=3) if sectors else "확인 불가"
    weak = _sector_line(list(reversed(sectors[-2:])), count=2) if sectors else "확인 불가"
    theme = _theme_from_snapshot(snapshot, news_items)
    sector_basis = _sector_basis_label(snapshot)
    return [
        ("시장 판단", f"{regime}: {action}"),
        ("우선 볼 섹터", f"{strong} / 핵심 테마: {theme} / {sector_basis}"),
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
        if text_mentions_symbol_or_alias(text, action.symbol):
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


def _clamp_score(value: int, low: int = -3, high: int = 3) -> int:
    return max(low, min(high, value))


def _format_score(score: int) -> str:
    return f"{score:+d}"


def _sector_price_score(quote: Quote) -> tuple[int, str]:
    change = quote.change_percent
    if change >= 1.5:
        return 3, "가격 자체가 강하게 올라 매수세가 뚜렷합니다."
    if change >= 0.5:
        return 2, "시장 대비 우호적인 상승 흐름입니다."
    if change > 0:
        return 1, "상승은 했지만 강한 주도까지는 아닙니다."
    if change <= -1.5:
        return -3, "가격 하락이 커서 자금 이탈 신호가 강합니다."
    if change <= -0.5:
        return -2, "시장 안에서 약한 편에 속합니다."
    if change < 0:
        return -1, "소폭 약세라 추세 확인이 필요합니다."
    return 0, "가격 변화가 거의 없어 판단 근거가 약합니다."


def _sector_news_score(sector: str, news_items: list[NewsItem]) -> tuple[int, str]:
    sector_keywords = {
        "Technology": ["ai", "chip", "semiconductor", "nvidia", "amd", "micron", "intel", "cloud", "software"],
        "Communication Services": ["meta", "alphabet", "google", "advertising", "streaming"],
        "Consumer Discretionary": ["tesla", "amazon", "consumer", "retail", "auto"],
        "Industrials": ["defense", "aerospace", "infrastructure", "industrial"],
        "Materials": ["copper", "steel", "lithium", "materials", "mining"],
        "Financials": ["bank", "banks", "yield curve", "credit", "financial"],
        "Energy": ["oil", "crude", "energy", "gas", "opec"],
        "Utilities": ["utilities", "power", "electricity", "grid"],
        "Real Estate": ["real estate", "reit", "mortgage", "property"],
        "Health Care": ["health", "drug", "pharma", "biotech", "medicare"],
        "Consumer Staples": ["staples", "grocery", "food", "beverage"],
    }
    score = 0
    hits: list[str] = []
    for item in news_items:
        label = korean_news_label(item)
        mapped_sector = _news_label_sector(label)
        text = f"{item.title} {item.description}".lower()
        keyword_hit = any(keyword in text for keyword in sector_keywords.get(sector, []))
        if mapped_sector != sector and not keyword_hit:
            continue
        sentiment = korean_news_sentiment(item)[0]
        points = _sentiment_points(sentiment)
        score += points
        hits.append(f"{label} {sentiment}")

    score = _clamp_score(score)
    if not hits:
        return 0, "직접 연결되는 주요 뉴스가 부족해 가격과 수급을 더 봐야 합니다."
    if score > 0:
        return score, f"관련 뉴스가 우호적입니다({', '.join(hits[:2])})."
    if score < 0:
        return score, f"관련 뉴스에 부담 요인이 있습니다({', '.join(hits[:2])})."
    return 0, f"관련 뉴스가 섞여 있어 방향성이 뚜렷하지 않습니다({', '.join(hits[:2])})."


def _sector_rate_score(sector: str, snapshot: MarketSnapshot) -> tuple[int, str]:
    ten_year = snapshot.risk_quotes.get("10Y Yield")
    if not ten_year:
        return 0, "10년물 금리 데이터를 확인하지 못했습니다."

    change = ten_year.change_percent
    if abs(change) < 0.2:
        return 0, "금리 변화가 작아 섹터 영향은 제한적입니다."

    growth = {"Technology", "Communication Services", "Consumer Discretionary"}
    rate_sensitive = growth | {"Real Estate", "Utilities"}
    if sector in rate_sensitive:
        if change > 1.0:
            return -2, "금리 상승이 성장주/금리민감 섹터 밸류에이션에 부담입니다."
        if change > 0:
            return -1, "금리 상승 방향이 섹터에 약한 부담입니다."
        if change < -1.0:
            return 2, "금리 하락이 성장주/금리민감 섹터에 우호적입니다."
        return 1, "금리 하락 방향이 섹터에 약한 우호 요인입니다."

    if sector == "Financials":
        if change > 0:
            return 1, "금리 상승은 은행 순이자마진 기대에 일부 우호적입니다."
        return -1, "금리 하락은 금융주 이익 기대를 낮출 수 있습니다."

    if change > 1.0:
        return -1, "금리 상승은 시장 전반 위험선호를 낮추는 요인입니다."
    if change < -1.0:
        return 1, "금리 하락은 시장 전반 위험선호에 우호적입니다."
    return 0, "금리 영향은 중립에 가깝습니다."


def _sector_flow_score(quote: Quote, sectors: list[Quote]) -> tuple[int, str]:
    if not sectors:
        return 0, "섹터 순위 데이터가 부족합니다."

    ranked = sorted(sectors, key=lambda item: item.change_percent, reverse=True)
    rank = next((index for index, item in enumerate(ranked, start=1) if item.name == quote.name), len(ranked))
    count = len(ranked)
    top_cut = max(1, count // 3)
    bottom_cut = count - top_cut + 1

    if rank == 1:
        return 3, "섹터 순위 1위로 자금 유입이 가장 강한 축입니다."
    if rank <= top_cut:
        return 2, "상위권 섹터라 상대 자금 유입이 추정됩니다."
    if rank == count:
        return -3, "섹터 순위 최하위로 자금 이탈 압력이 큽니다."
    if rank >= bottom_cut:
        return -2, "하위권 섹터라 상대적으로 소외되고 있습니다."
    if quote.change_percent > 0:
        return 1, "중간권이지만 플러스 흐름은 유지했습니다."
    if quote.change_percent < 0:
        return -1, "중간권이지만 마이너스 흐름이라 확인이 필요합니다."
    return 0, "수급 우위가 뚜렷하지 않습니다."


def _sector_total_summary(total_score: int) -> str:
    if total_score >= 6:
        return "강한 우위"
    if total_score >= 3:
        return "우위 관찰"
    if total_score <= -6:
        return "강한 경계"
    if total_score <= -3:
        return "약세 경계"
    return "중립 확인"


def _sector_focus_reason(card: SectorScore) -> str:
    components = [
        ("가격", card.price_score),
        ("뉴스", card.news_score),
        ("금리", card.rate_score),
        ("수급", card.flow_score),
    ]
    dominant_name, dominant_score = max(
        components, key=lambda item: (abs(item[1]), item[1])
    )
    if dominant_score == 0:
        return "어느 한 요인도 방향을 만들지 못해 가격 확인이 더 필요합니다."

    same_direction = "끌어올린" if dominant_score > 0 else "끌어내린"
    opposite = [
        (name, score)
        for name, score in components
        if score and (score > 0) != (dominant_score > 0)
    ]
    sentence = (
        f"{dominant_name} 점수({_format_score(dominant_score)})가 총점을 가장 크게 {same_direction} 요인입니다."
    )
    if opposite:
        counter_name, counter_score = max(opposite, key=lambda item: abs(item[1]))
        sentence += f" 다만 {counter_name} 점수({_format_score(counter_score)})는 반대 신호입니다."
    return sentence


def _unique_news_signals(item: NewsItem, checkpoints: list[str]) -> list[str]:
    checkpoint_set = {checkpoint.strip() for checkpoint in checkpoints}
    unique: list[str] = []
    seen: set[str] = set()
    for signal in korean_news_next_signals(item):
        cleaned = signal.strip()
        if not cleaned or cleaned in checkpoint_set or cleaned in seen:
            continue
        seen.add(cleaned)
        unique.append(cleaned)
    return unique


def _sector_scorecards(
    snapshot: MarketSnapshot,
    sectors: list[Quote] | None = None,
    news_items: list[NewsItem] | None = None,
) -> list[SectorScore]:
    sector_quotes = sectors or sorted(
        snapshot.sector_quotes.values(), key=lambda quote: quote.change_percent, reverse=True
    )
    items = news_items or []
    cards: list[SectorScore] = []
    for quote in sector_quotes:
        price_score, price_reason = _sector_price_score(quote)
        news_score, news_reason = _sector_news_score(quote.name, items)
        rate_score, rate_reason = _sector_rate_score(quote.name, snapshot)
        flow_score, flow_reason = _sector_flow_score(quote, sector_quotes)
        total_score = price_score + news_score + rate_score + flow_score
        cards.append(
            SectorScore(
                sector=quote.name,
                label=SECTOR_KO.get(quote.name, quote.name),
                change_percent=quote.change_percent,
                price_score=price_score,
                news_score=news_score,
                rate_score=rate_score,
                flow_score=flow_score,
                total_score=total_score,
                summary=_sector_total_summary(total_score),
                detail=(
                    f"가격: {price_reason} 뉴스: {news_reason} "
                    f"금리: {rate_reason} 수급: {flow_reason}"
                ),
            )
        )
    return sorted(cards, key=lambda card: (card.total_score, card.change_percent), reverse=True)


def _sector_score_report(
    snapshot: MarketSnapshot,
    sectors: list[Quote],
    news_items: list[NewsItem],
) -> str:
    cards = _sector_scorecards(snapshot, sectors, news_items)
    if not cards:
        return "섹터 점수판\n섹터 데이터를 가져오지 못해 점수화할 수 없습니다."

    top_cards = cards[:3]
    top_sectors = {card.sector for card in top_cards}
    bottom_cards = [
        card for card in reversed(cards) if card.sector not in top_sectors
    ][:3]
    selected_sectors = {card.sector for card in [*top_cards, *bottom_cards]}
    middle_cards = [card for card in cards if card.sector not in selected_sectors]

    lines = [
        "섹터 점수판",
        "읽는 법: 모든 섹터를 훑지 않고 상위 3개와 하위 3개만 먼저 봅니다. 총점은 가격·뉴스·금리·수급을 합친 상대 우선순위이며, +3 이상은 우위, -3 이하는 경계입니다.",
        (
            "한눈에: 먼저 볼 곳은 "
            + ", ".join(f"{card.label} {_format_score(card.total_score)}" for card in top_cards)
            + " / 피할 곳은 "
            + ", ".join(f"{card.label} {_format_score(card.total_score)}" for card in bottom_cards)
        ),
        "우선순위 3",
        "|순위|섹터|총점|당일 등락|핵심 해석|",
        "|---|---|---|---|---|",
    ]
    for rank, card in enumerate(top_cards, start=1):
        lines.append(
            f"|{rank}|{card.label}|{_format_score(card.total_score)}|"
            f"{format_change(card.change_percent)}|{_sector_focus_reason(card)}|"
        )

    lines.extend(
        [
            "경계 3",
            "|순위|섹터|총점|당일 등락|핵심 해석|",
            "|---|---|---|---|---|",
        ]
    )
    for rank, card in enumerate(bottom_cards, start=1):
        lines.append(
            f"|{rank}|{card.label}|{_format_score(card.total_score)}|"
            f"{format_change(card.change_percent)}|{_sector_focus_reason(card)}|"
        )

    if middle_cards:
        lines.append(
            "나머지 묶음: "
            + ", ".join(
                f"{card.label} {_format_score(card.total_score)}" for card in middle_cards
            )
            + ". 상·하위 3개에서 제외했으며, 총점 동점은 당일 등락으로 정렬합니다."
        )
    lines.append("세부 점수: 웹 점수판에서 필요한 섹터만 ‘세부 점수 보기’를 열어 가격·뉴스·금리·수급 근거를 확인합니다.")
    return "\n".join(lines)


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
    interpretation: NewsInterpretation | None = None,
) -> str:
    label = korean_news_label(item)
    headline = korean_news_headline(item)
    sentiment, reason = korean_news_sentiment(item)
    importance, importance_reason = korean_news_importance(item)
    impact, impact_reason = _news_impact_classification(item, watchlist_actions or [])
    price_reaction = _news_price_reaction(item, snapshot)
    bull_case, bear_case = korean_news_scenario(item)
    interpretation = interpretation or rule_based_news_interpretation(item)
    signals = _unique_news_signals(item, interpretation.checkpoints)
    checkpoint_text = " / ".join(interpretation.checkpoints)
    signal_text = " / ".join(signals) or "위 확인 포인트와 동일"
    why_it_matters = korean_news_why_it_matters(item)
    risk_line = (
        f"리스크: {interpretation.risks}\n"
        if interpretation.risks.strip() != why_it_matters.strip()
        else ""
    )
    card = (
        f"뉴스 {index}/5 [{label}] {sentiment}\n"
        f"중요도: {importance} - {importance_reason}\n"
        f"영향 분류: {impact} - {impact_reason}\n"
        f"원문: {item.title}\n"
        f"핵심: {headline}\n"
        f"핵심 요약({interpretation.source}): {interpretation.core_summary}\n"
        f"왜 중요: {why_it_matters}\n"
        f"투자 해석: {interpretation.investment_read}\n"
        f"{risk_line}"
        f"가격반응: {price_reaction}\n"
        f"긍정 시나리오: {bull_case}\n"
        f"부정 시나리오: {bear_case}\n"
        f"확인 포인트: {checkpoint_text}\n"
        f"확인 신호: {signal_text}\n"
        f"관련: {korean_news_related(item)}\n"
        f"출처: {item.source} {item.link}"
    )
    if len(card) <= max_chars:
        return card

    compact = (
        f"뉴스 {index}/5 [{label}] {sentiment}\n"
        f"영향 분류: {impact}\n"
        f"핵심: {_shorten(headline, 54)}\n"
        f"핵심 요약: {_shorten(interpretation.core_summary, 92)}\n"
        f"투자 해석: {_shorten(interpretation.investment_read, 92)}\n"
        f"리스크: {_shorten(interpretation.risks, 72)}\n"
        f"확인 포인트: {_shorten(checkpoint_text, 72)}"
    )
    if len(compact) <= max_chars:
        return compact

    return (
        f"뉴스 {index}/5 [{label}] {sentiment}\n"
        f"핵심: {_shorten(headline, 62)}\n"
        f"핵심 요약: {_shorten(interpretation.core_summary, 86)}\n"
        f"투자 해석: {_shorten(interpretation.investment_read, 86)}"
    )


def _format_news(
    items: list[NewsItem],
    snapshot: MarketSnapshot,
    watchlist_actions: list[WatchlistAction] | None = None,
    interpretations: dict[str, NewsInterpretation] | None = None,
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
                interpretation=(interpretations or {}).get(item.link),
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
    <section class="news-dashboard" id="news-dashboard">
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
    <section class="quick-summary" id="quick-summary">
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


def _warnings_block(warnings: list[str], limit: int = 6) -> str:
    visible = warnings[:limit]
    extra_count = max(0, len(warnings) - len(visible))
    lines = ["확인 필요"]
    lines.extend(f"- {warning}" for warning in visible)
    if extra_count:
        lines.append(f"- 외 {extra_count}개 경고가 더 있습니다. GitHub Actions 로그와 보고서 artifacts를 확인하세요.")
    return "\n".join(lines)


def _chart_rows_for_quote(quote: Quote, target_date) -> list[dict]:
    if quote.source.startswith("Stooq") or quote.symbol.endswith(".us"):
        rows = fetch_stooq_daily(quote.symbol)
    else:
        try:
            rows = fetch_yahoo_daily(quote.symbol)
        except Exception:  # noqa: BLE001 - a chart should never break the report.
            if quote.symbol.isalpha():
                rows = fetch_stooq_daily(f"{quote.symbol.lower()}.us")
            else:
                raise
    rows = [row for row in rows if row["date"] <= target_date and row.get("close") is not None]
    if len(rows) < 2:
        raise RuntimeError("차트용 가격 데이터가 부족합니다.")
    return rows[-20:]


def _mini_chart_svg(rows: list[dict], color: str = "#2454a6") -> str:
    closes = [float(row["close"]) for row in rows]
    low = min(closes)
    high = max(closes)
    spread = high - low
    width = 220
    height = 96
    left = 8
    right = 212
    top = 16
    bottom = 78
    if len(closes) < 2:
        raise RuntimeError("차트용 가격 데이터가 부족합니다.")

    points = []
    for index, close in enumerate(closes):
        x = left + (right - left) * index / (len(closes) - 1)
        if spread == 0:
            y = (top + bottom) / 2
        else:
            y = bottom - (close - low) / spread * (bottom - top)
        points.append(f"{x:.1f},{y:.1f}")

    return (
        '<svg class="mini-chart" viewBox="0 0 220 96" role="img" aria-label="20일 가격 차트">'
        '<line x1="8" y1="78" x2="212" y2="78" stroke="#e4e7ec" stroke-width="1"/>'
        '<line x1="8" y1="16" x2="8" y2="78" stroke="#eef2f6" stroke-width="1"/>'
        f'<polyline points="{" ".join(points)}" fill="none" stroke="{html.escape(color)}" stroke-width="3" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
        f'<circle cx="{points[-1].split(",")[0]}" cy="{points[-1].split(",")[1]}" r="3.5" fill="{html.escape(color)}"/>'
        '</svg>'
    )


def _chart_card(title: str, quote: Quote, target_date, color: str = "#2454a6") -> str:
    try:
        rows = _chart_rows_for_quote(quote, target_date)
        svg = _mini_chart_svg(rows, color)
        start = float(rows[0]["close"])
        end = float(rows[-1]["close"])
        period_change = ((end - start) / start) * 100 if start else 0.0
        first_date = rows[0]["date"].isoformat()
        last_date = rows[-1]["date"].isoformat()
        return f"""
        <section class="chart-card">
          <div class="chart-title">
            <strong>{html.escape(title)}</strong>
            <span>{html.escape(format_change(quote.change_percent))}</span>
          </div>
          {svg}
          <div class="chart-meta">
            <span>20일 {html.escape(format_change(period_change))}</span>
            <span>{html.escape(first_date)} ~ {html.escape(last_date)}</span>
          </div>
          <small>출처: {html.escape(quote.source)}</small>
        </section>
        """
    except Exception as exc:  # noqa: BLE001 - render a clear placeholder instead.
        return f"""
        <section class="chart-card chart-missing">
          <div class="chart-title">
            <strong>{html.escape(title)}</strong>
            <span>확인 필요</span>
          </div>
          <div class="chart-placeholder">차트 데이터를 가져오지 못했습니다.</div>
          <small>{html.escape(str(exc))}</small>
        </section>
        """


def _market_charts_html(snapshot: MarketSnapshot, sectors: list[Quote]) -> str:
    chart_items: list[tuple[str, Quote, str]] = []
    for name, color in (("S&P 500", "#2454a6"), ("Nasdaq", "#7a5af8")):
        quote = snapshot.index_quotes.get(name)
        if quote:
            chart_items.append((name, quote, color))
    for name, color in (("VIX", "#b42318"), ("10Y Yield", "#b54708")):
        quote = snapshot.risk_quotes.get(name)
        if quote:
            chart_items.append((RISK_KO.get(name, name), quote, color))

    selected_sectors = []
    if sectors:
        selected_sectors.extend(sectors[:3])
        selected_sectors.extend(list(reversed(sectors[-2:])))
    seen = {quote.name for _title, quote, _color in chart_items}
    for quote in selected_sectors:
        if quote.name in seen:
            continue
        seen.add(quote.name)
        color = "#0f7b3b" if quote.change_percent >= 0 else "#b42318"
        chart_items.append((SECTOR_KO.get(quote.name, quote.name), quote, color))

    cards = "".join(
        _chart_card(title, quote, snapshot.target_date, color)
        for title, quote, color in chart_items
    )
    if not cards:
        cards = (
            '<section class="chart-card chart-missing">'
            '<div class="chart-title"><strong>차트</strong><span>확인 필요</span></div>'
            '<div class="chart-placeholder">차트로 표시할 가격 데이터가 없습니다.</div>'
            '</section>'
        )
    return f"""
    <section class="charts-section" id="charts">
      <div class="charts-head">
        <h2>가격 차트</h2>
        <p>S&P500, Nasdaq, VIX, 10년물 금리와 주요 섹터 ETF의 최근 20거래일 흐름입니다.</p>
      </div>
      <div class="chart-grid">{cards}</div>
    </section>
    """


def _report_badge_class(cell: str) -> str | None:
    if cell == "성공":
        return "report-badge tracking-success"
    if cell == "실패":
        return "report-badge tracking-failure"
    if cell == "보류":
        return "report-badge tracking-hold"
    if cell in {"진입 후보", "오늘 진입 검토"}:
        return "report-badge action-ok"
    if cell in {"눌림 관찰", "20일선 회복 대기"}:
        return "report-badge action-wait"
    if cell in {"과이격/추격주의", "추세 약화"}:
        return "report-badge action-risk"
    if cell == "제외":
        return "report-badge position-bad"
    if cell in {"지금 소량 가능", "지금은 1차 진입만 가능"}:
        return "report-badge action-ok"
    if cell in {"눌림 확인 후 가능", "돌파 확인 후 가능", "거래량 확인 후 가능"}:
        return "report-badge action-wait"
    if cell in {"추격 금지", "제외"}:
        return "report-badge action-risk"
    if cell == "공격 비중 가능":
        return "report-badge position-aggressive"
    if cell == "손익비 우수":
        return "report-badge position-good"
    if cell == "비중 확대 가능":
        return "report-badge position-add"
    if cell == "작게만 가능":
        return "report-badge position-small"
    if cell == "진입 부적합":
        return "report-badge position-bad"
    if cell == "우수":
        return "report-badge rr-excellent"
    if cell == "양호":
        return "report-badge rr-good"
    if cell == "보통":
        return "report-badge rr-normal"
    if cell == "나쁨":
        return "report-badge rr-bad"
    if cell == "A급" or cell.startswith("A("):
        return "report-badge grade-a"
    if cell == "B급" or cell.startswith("B("):
        return "report-badge grade-b"
    if cell == "C급" or cell.startswith("C("):
        return "report-badge grade-c"
    return None


def _report_cell_html(cell: str) -> str:
    badge_class = _report_badge_class(cell)
    if badge_class:
        return f'<span class="{badge_class}">{html.escape(cell)}</span>'
    return html.escape(cell)


def _render_report_body_lines(lines: list[str]) -> str:
    html_parts: list[str] = []
    in_list = False
    table_rows: list[list[str]] = []

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            html_parts.append("</ul>")
            in_list = False

    def close_table() -> None:
        nonlocal table_rows
        if not table_rows:
            return
        header = table_rows[0]
        body = table_rows[1:]
        is_wide = len(header) >= 8
        wrap_class = "report-table-wrap report-table-wrap-wide" if is_wide else "report-table-wrap"
        table_class = "report-table report-table-wide" if is_wide else "report-table"
        head_html = "".join(f"<th>{html.escape(cell)}</th>" for cell in header)

        def td_html(index: int, cell: str) -> str:
            label = header[index] if index < len(header) else ""
            return f'<td data-label="{html.escape(label, quote=True)}">{_report_cell_html(cell)}</td>'

        body_html = "".join(
            "<tr>" + "".join(td_html(index, cell) for index, cell in enumerate(row)) + "</tr>"
            for row in body
        )
        html_parts.append(
            f'<div class="{wrap_class}"><table class="{table_class}"><thead><tr>{head_html}</tr></thead><tbody>{body_html}</tbody></table></div>'
        )
        table_rows = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            close_list()
            close_table()
            continue
        if line.startswith("|") and line.endswith("|"):
            close_list()
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if cells and all(set(cell) <= {"-"} for cell in cells if cell):
                continue
            table_rows.append(cells)
            continue

        close_table()
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
    close_table()
    return "\n".join(html_parts)


def _render_report_sections(text: str) -> str:
    sections = []
    for block in [part.strip() for part in text.split("\n\n") if part.strip()]:
        lines = [line.rstrip() for line in block.splitlines()]
        title = lines[0].strip()
        if title.startswith("뉴스 ") or title == "추천 후보 성과판":
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
        elif (
            "이벤트" in title
            or "SEC 공시" in title
            or "실적 발표" in title
            or "데이터 기준" in title
        ):
            class_name += " report-event"
        elif "핵심 리스크" in title or "섹터 로테이션" in title or "섹터 점수판" in title or "오늘의 경고" in title:
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


def _report_css_text() -> str:
    css_path = Path(__file__).with_name("report.css")
    try:
        return css_path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _sector_scoreboard_html(
    snapshot: MarketSnapshot,
    sectors: list[Quote],
    news_items: list[NewsItem],
) -> str:
    cards = _sector_scorecards(snapshot, sectors, news_items)
    if not cards:
        return '<p class="scoreboard-empty">섹터 데이터를 가져오지 못했습니다.</p>'

    top_cards = cards[:3]
    top_sectors = {card.sector for card in top_cards}
    bottom_cards = [
        card for card in reversed(cards) if card.sector not in top_sectors
    ][:3]
    selected_sectors = {card.sector for card in [*top_cards, *bottom_cards]}
    middle_cards = [card for card in cards if card.sector not in selected_sectors]

    def lane(title: str, description: str, lane_cards: list[SectorScore], tone: str) -> str:
        rows = []
        for rank, card in enumerate(lane_cards, start=1):
            score_parts = "".join(
                f"<span>{label}<b>{html.escape(_format_score(score))}</b></span>"
                for label, score in (
                    ("가격", card.price_score),
                    ("뉴스", card.news_score),
                    ("금리", card.rate_score),
                    ("수급", card.flow_score),
                )
            )
            rows.append(
                f"""
                <article class="scoreboard-row scoreboard-{tone}">
                  <div class="scoreboard-rank">{rank}</div>
                  <div class="scoreboard-main">
                    <div class="scoreboard-name">
                      <strong>{html.escape(card.label)}</strong>
                      <span>{html.escape(format_change(card.change_percent))}</span>
                    </div>
                    <p>{html.escape(_sector_focus_reason(card))}</p>
                    <details>
                      <summary>세부 점수 보기</summary>
                      <div class="score-parts">{score_parts}</div>
                      <small>{html.escape(card.detail)}</small>
                    </details>
                  </div>
                  <b class="scoreboard-total">{html.escape(_format_score(card.total_score))}</b>
                </article>
                """
            )
        return f"""
        <section class="scoreboard-lane">
          <div class="scoreboard-lane-head">
            <h3>{html.escape(title)}</h3>
            <p>{html.escape(description)}</p>
          </div>
          {''.join(rows)}
        </section>
        """

    middle_text = ", ".join(
        f"{card.label} {_format_score(card.total_score)}" for card in middle_cards
    ) or "없음"
    sector_basis = _sector_basis_label(snapshot)
    return f"""
    <section class="scoreboard-shell">
      <div class="scoreboard-intro">
        <strong>상·하위만 먼저 읽으세요.</strong>
        <span>가격·뉴스·금리·수급을 합친 상대 순위입니다. 전체 11개를 펼치지 않고 결정에 필요한 6개만 보여줍니다.</span>
        <small>{html.escape(sector_basis)}</small>
      </div>
      <div class="scoreboard-lanes">
        {lane('먼저 볼 3', '점수가 높은 순서입니다. 가격 지속성과 거래량을 다음날 확인합니다.', top_cards, 'positive')}
        {lane('경계할 3', '점수가 낮은 순서입니다. 반등보다 약세 해소 확인이 먼저입니다.', bottom_cards, 'negative')}
      </div>
      <p class="scoreboard-middle"><b>나머지:</b> {html.escape(middle_text)} — 상·하위 3개에서 제외했으며, 총점 동점은 당일 등락으로 정렬합니다.</p>
    </section>
    """


def _jump_nav_html() -> str:
    return """
    <nav class="jump-nav" aria-label="보고서 바로가기">
      <strong>바로가기</strong>
      <a href="#quick-summary">빠른 요약</a>
      <a href="#recommendation-performance">추천 성과</a>
      <a href="#charts">가격 차트</a>
      <a href="#sector-view">섹터</a>
      <a href="#news-dashboard">뉴스 요약</a>
      <a href="#news-analysis">뉴스 상세</a>
      <a href="#full-report">전체 근거</a>
    </nav>
    """


def _performance_percent(value: float | int | None) -> str:
    if value is None:
        return "-"
    return f"{float(value):+.2f}%"


def _performance_money(value: object) -> str:
    try:
        if value is None or value == "":
            return "-"
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "-"


def _return_meter_html(value: float | None) -> str:
    if value is None:
        return '<span class="return-none">-</span>'
    tone = "positive" if value > 0.001 else "negative" if value < -0.001 else "flat"
    width = min(100, 12 + abs(value) * 8)
    return (
        f'<span class="return-meter return-{tone}">'
        f'<span aria-hidden="true" style="width:{width:.1f}%"></span>'
        f'<b>{html.escape(_performance_percent(value))}</b></span>'
    )


def _performance_outcome_label(value: str) -> str:
    return {
        "TARGET_FIRST": "목표 먼저",
        "STOP_FIRST": "무효화 먼저",
        "MIXED_SAME_DAY": "동일일 혼재",
        "OPEN": "진행 중",
        "NO_PRICE": "기준가 없음",
        "NO_DATA": "시세 부족",
        "NO_RISK_LEVELS": "가격 기준 부족",
    }.get(value, value)


def _performance_rows_html(items: list[SignalEvaluation], repeats: Counter[str]) -> str:
    rows = []
    for item in items:
        grade = item.bucket.split(" / ", 1)[0]
        grade_class = _report_badge_class(grade) or "report-badge"
        repeated = repeats[item.symbol]
        repeat_html = f"<small>{repeated}회 등장</small>" if repeated > 1 else ""
        outcome = _performance_outcome_label(item.outcome)
        outcome_tone = (
            "tracking-success"
            if item.outcome == "TARGET_FIRST"
            else "tracking-failure"
            if item.outcome == "STOP_FIRST"
            else "tracking-hold"
        )
        cells = [
            ("추천일", html.escape(item.signal_date.isoformat())),
            (
                "종목",
                f"<strong>{html.escape(item.name)}({html.escape(item.symbol)})</strong>{repeat_html}",
            ),
            ("등급", f'<span class="{grade_class}">{html.escape(grade)}</span>'),
            ("기준가", html.escape(_performance_money(item.reference_price))),
            ("최신가", html.escape(_performance_money(item.latest_price))),
            ("1D", _return_meter_html(item.returns.get(1))),
            ("5D", _return_meter_html(item.returns.get(5))),
            ("현재", _return_meter_html(item.current_return)),
            ("SPY 대비", _return_meter_html(item.spy_relative_current)),
            ("경과", f"{item.holding_days}거래일"),
            ("상태", f'<span class="report-badge {outcome_tone}">{html.escape(outcome)}</span>'),
        ]
        rows.append(
            "<tr>"
            + "".join(
                f'<td data-label="{html.escape(label, quote=True)}">{value}</td>'
                for label, value in cells
            )
            + "</tr>"
        )
    return "".join(rows)


def _recommendation_performance_html(performance: RecommendationPerformance) -> str:
    metrics = recommendation_performance_metrics(performance)
    tracked_count = int(metrics["tracked_count"] or 0)
    sample_label = "초기 표본" if tracked_count < 30 else "누적 표본"
    sample_class = "sample-early" if tracked_count < 30 else "sample-mature"
    positive_ratio = metrics["positive_ratio"]
    positive_text = "-" if positive_ratio is None else f"{float(positive_ratio):.1f}%"
    current_cards = []
    for signal in performance.current_candidates:
        grade = str(signal.get("candidate_grade") or "등급 미정")
        grade_class = _report_badge_class(grade) or "report-badge"
        reference = signal.get("start_entry_price") or signal.get("close")
        current_cards.append(
            f"""
            <article class="recommendation-card">
              <div class="recommendation-card-head">
                <strong>{html.escape(str(signal.get('name') or signal.get('symbol') or '종목'))}</strong>
                <span>{html.escape(str(signal.get('symbol') or ''))}</span>
                <b class="{grade_class}">{html.escape(grade)}</b>
              </div>
              <p>{html.escape(str(signal.get('entry_action') or signal.get('recommendation_label') or '조건 확인'))}</p>
              <dl>
                <div><dt>기준가</dt><dd>{html.escape(_performance_money(reference))}</dd></div>
                <div><dt>1차 목표</dt><dd>{html.escape(_performance_money(signal.get('first_target_price')))}</dd></div>
                <div><dt>무효화</dt><dd>{html.escape(_performance_money(signal.get('invalidation_price')))}</dd></div>
              </dl>
              <small>{html.escape(str(signal.get('entry_style') or signal.get('position_mode') or '가격 조건을 확인하세요.'))}</small>
            </article>
            """
        )
    current_html = "".join(current_cards) or (
        '<p class="performance-empty">오늘은 A·B급 조건을 통과한 추천/관찰 후보가 없습니다.</p>'
    )

    evaluations = performance.evaluations
    repeats: Counter[str] = Counter(item.symbol for item in evaluations)
    visible = evaluations[:8]
    remaining = evaluations[8:]
    table_head = (
        "<thead><tr><th>추천일</th><th>종목</th><th>등급</th><th>기준가</th><th>최신가</th>"
        "<th>1D</th><th>5D</th><th>현재</th><th>SPY 대비</th><th>경과</th><th>상태</th></tr></thead>"
    )
    if visible:
        recent_table = (
            '<div class="report-table-wrap report-table-wrap-wide performance-table-wrap">'
            f'<table class="report-table report-table-wide performance-table">{table_head}'
            f"<tbody>{_performance_rows_html(visible, repeats)}</tbody></table></div>"
        )
    else:
        recent_table = (
            '<p class="performance-empty">아직 평가할 과거 추천이 없습니다. 다음 거래일부터 수익률이 쌓입니다.</p>'
        )
    more_html = ""
    if remaining:
        more_html = (
            '<details class="performance-more"><summary>'
            f"나머지 추천 이력 {len(remaining)}건 보기</summary>"
            '<div class="report-table-wrap report-table-wrap-wide performance-table-wrap">'
            f'<table class="report-table report-table-wide performance-table">{table_head}'
            f"<tbody>{_performance_rows_html(remaining, repeats)}</tbody></table></div></details>"
        )

    warning_html = ""
    if performance.warnings:
        warning_html = (
            '<p class="performance-warning">'
            f"가격 확인 실패 {len(performance.warnings)}건은 성과 집계에서 제외했습니다."
            "</p>"
        )

    return f"""
    <section class="recommendation-performance" id="recommendation-performance">
      <div class="performance-head">
        <div>
          <p class="eyebrow">Recommendation Ledger</p>
          <h2>추천 후보 성과판</h2>
          <p>A급 진입 후보와 B급 조건부 관찰 후보만 모았습니다. C급 추격 금지·제외 {performance.excluded_current_count}개는 성과에서 뺐습니다.</p>
        </div>
        <span class="sample-badge {sample_class}">{sample_label}</span>
      </div>
      <div class="performance-metrics">
        <div><span>누적 추천</span><strong>{tracked_count}건</strong><small>{int(metrics['symbol_count'] or 0)}종목 · {performance.history_days}거래일</small></div>
        <div><span>평균 수익률</span><strong>{html.escape(_performance_percent(metrics['average_return']))}</strong><small>평가 가능 {int(metrics['measured_count'] or 0)}건</small></div>
        <div><span>플러스 비율</span><strong>{html.escape(positive_text)}</strong><small>0% 초과 비중</small></div>
        <div><span>SPY 대비</span><strong>{html.escape(_performance_percent(metrics['average_relative']))}</strong><small>같은 보유기간 평균</small></div>
      </div>
      <p class="performance-read"><b>한줄 해석</b>{html.escape(recommendation_performance_read(performance))}</p>
      <div class="performance-note">
        <b>읽기 전에</b>
        <span>시작 진입가가 있으면 그 가격, 없으면 추천일 종가가 기준입니다. 배당·수수료·실제 체결은 반영하지 않습니다.</span>
        <span>최근 최대 60건을 추적합니다. 5D는 5거래일이 지난 {int(metrics['mature_5d_count'] or 0)}건만 표시하며, 30건 미만은 결론보다 기록 축적으로 보세요.</span>
      </div>
      <div class="performance-subhead"><h3>오늘의 추천·관찰 후보</h3><span>{len(performance.current_candidates)}개</span></div>
      <div class="recommendation-grid">{current_html}</div>
      <div class="performance-subhead"><h3>추천 이력과 수익률</h3><span>최근 8건 우선</span></div>
      {recent_table}
      {more_html}
      {warning_html}
    </section>
    """


def _news_cards_html(
    snapshot: MarketSnapshot,
    news_items: list[NewsItem],
    watchlist_actions: list[WatchlistAction],
    interpretations: dict[str, NewsInterpretation] | None = None,
) -> str:
    cards: list[str] = []
    ranked_items = _ranked_news_items(news_items)[:5]
    for index, item in enumerate(ranked_items, start=1):
        importance, importance_reason = korean_news_importance(item)
        importance_class = _importance_badge_class(importance)
        impact, impact_reason = _news_impact_classification(item, watchlist_actions)
        impact_class = _news_impact_badge_class(impact)
        sentiment, _sentiment_reason = korean_news_sentiment(item)
        bull_case, bear_case = korean_news_scenario(item)
        interpretation = (interpretations or {}).get(item.link) or rule_based_news_interpretation(item)
        signals = _unique_news_signals(item, interpretation.checkpoints)
        signal_items = "".join(f"<li>{html.escape(signal)}</li>" for signal in signals)
        checkpoint_items = "".join(
            f"<li>{html.escape(checkpoint)}</li>" for checkpoint in interpretation.checkpoints
        )
        why_it_matters = korean_news_why_it_matters(item)
        risk_html = ""
        if interpretation.risks.strip() != why_it_matters.strip():
            risk_html = f"<span><b>리스크:</b> {html.escape(interpretation.risks)}</span>"
        signals_html = ""
        if signal_items:
            signals_html = (
                '<div class="signal-block"><b>다음날 확인 신호</b>'
                f"<ul>{signal_items}</ul></div>"
            )
        open_attribute = " open" if index == 1 else ""
        cards.append(
            f"""
            <li>
              <details{open_attribute}>
                <summary>
                  <span class="news-summary-title">{html.escape(korean_news_headline(item))}</span>
                  <span class="news-summary-meta">
                    <b class="news-topic">{html.escape(korean_news_label(item))}</b>
                    <b class="importance-badge {importance_class}">{html.escape(importance)}</b>
                    <b class="impact-badge {impact_class}">{html.escape(impact)}</b>
                    <em class="sentiment">{html.escape(sentiment)}</em>
                  </span>
                  <span class="news-summary-brief">{html.escape(interpretation.core_summary)}</span>
                </summary>
                <div class="news-detail">
                  <span class="original-title">원문: {html.escape(item.title)}</span>
                  <span class="importance-line"><b>중요도:</b> {html.escape(importance_reason)}</span>
                  <span class="impact-line"><b>영향:</b> {html.escape(impact_reason)}</span>
                  <span><b>왜 중요:</b> {html.escape(why_it_matters)}</span>
                  <span><b>투자 해석:</b> {html.escape(interpretation.investment_read)}</span>
                  {risk_html}
                  <span><b>가격반응:</b> {html.escape(_news_price_reaction(item, snapshot))}</span>
                  <span><b>긍정 시나리오:</b> {html.escape(bull_case)}</span>
                  <span><b>부정 시나리오:</b> {html.escape(bear_case)}</span>
                  <span><b>관련:</b> {html.escape(korean_news_related(item))}</span>
                  <div class="signal-block"><b>확인 포인트</b><ul>{checkpoint_items}</ul></div>
                  {signals_html}
                  <a href="{html.escape(item.link)}" target="_blank" rel="noopener noreferrer">{html.escape(item.source)} 원문 열기</a>
                </div>
              </details>
            </li>
            """
        )
    return "".join(cards)


def _write_html_report(
    report_path: Path,
    text: str,
    snapshot: MarketSnapshot,
    news_items: list[NewsItem],
    watchlist_actions: list[WatchlistAction],
    interpretations: dict[str, NewsInterpretation] | None = None,
    recommendation_performance: RecommendationPerformance | None = None,
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

    sector_scoreboard = _sector_scoreboard_html(snapshot, sectors, news_items)

    news_cards = _news_cards_html(
        snapshot,
        news_items,
        watchlist_actions,
        interpretations,
    )

    first_block = next((part.strip() for part in text.split("\n\n") if part.strip()), "")
    first_lines = [line.strip() for line in first_block.splitlines() if line.strip()]
    title_line = first_lines[0] if first_lines else "미국장 마감 보고서"
    market_line = first_lines[1] if len(first_lines) > 1 else ""
    one_line = first_lines[2] if len(first_lines) > 2 else ""
    rendered_sections = _render_report_sections(text)
    news_dashboard = _news_dashboard_html(snapshot, news_items)
    quick_summary = _mobile_quick_summary_html(snapshot, sectors, news_items, watchlist_actions)
    performance_section = (
        _recommendation_performance_html(recommendation_performance)
        if recommendation_performance is not None
        else ""
    )
    chart_section = _market_charts_html(snapshot, sectors)
    freshness = _data_freshness_html(snapshot)
    jump_nav = _jump_nav_html()
    report_css = _report_css_text()
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
      --page-x: 18px;
      --panel-pad: 22px;
      --section-gap: 16px;
      --content-max: 1240px;
    }}
    * {{ box-sizing: border-box; }}
    html {{ -webkit-text-size-adjust: 100%; }}
    body {{ margin: 0; font-family: Arial, 'Malgun Gothic', sans-serif; background: var(--bg); color: var(--ink); overflow-x: hidden; }}
    main {{ width: 100%; max-width: var(--content-max); margin: 0 auto; padding: 28px var(--page-x) 56px; }}
    .hero {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 28px; margin-bottom: var(--section-gap); }}
    .eyebrow {{ margin: 0 0 8px; color: var(--blue); font-size: 13px; font-weight: 700; }}
    h1 {{ margin: 0; font-size: clamp(26px, 4vw, 42px); line-height: 1.15; letter-spacing: 0; }}
    .market-line {{ margin-top: 16px; font-size: 18px; font-weight: 700; }}
    .one-line {{ margin: 8px 0 0; color: var(--muted); font-size: 16px; line-height: 1.55; }}
    h2 {{ margin: 28px 0 12px; font-size: 21px; line-height: 1.3; letter-spacing: 0; }}
    .quick-summary {{ background: #111827; color: #fff; border-radius: 8px; padding: var(--panel-pad); margin: var(--section-gap) 0; }}
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
    .sector-score-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 10px; margin-top: 10px; }}
    .sector-score {{ background: #fff; border: 1px solid #e1e5ec; border-left: 6px solid #98a2b3; border-radius: 8px; padding: 14px; line-height: 1.5; }}
    .sector-score.score-positive {{ border-left-color: var(--green); background: #f6fef9; }}
    .sector-score.score-negative {{ border-left-color: var(--red); background: #fff8f7; }}
    .sector-score.score-neutral {{ border-left-color: #d0d5dd; background: #fff; }}
    .score-top {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; }}
    .score-top strong {{ display: block; font-size: 17px; }}
    .score-top span {{ display: block; color: var(--muted); margin-top: 2px; }}
    .score-top > b {{ font-size: 26px; line-height: 1; }}
    .sector-score p {{ margin: 10px 0; font-weight: 800; }}
    .score-parts {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; margin: 10px 0; }}
    .score-parts span {{ display: block; background: #f2f4f7; border-radius: 6px; padding: 7px 5px; text-align: center; color: #475467; font-size: 12px; }}
    .score-parts b {{ display: block; margin-top: 2px; color: #111827; font-size: 15px; }}
    .sector-score small {{ display: block; color: #475467; }}
    .charts-section {{ margin: var(--section-gap) 0; background: #fff; border: 1px solid var(--line); border-radius: 8px; padding: var(--panel-pad); }}
    .charts-head h2 {{ margin: 0 0 6px; }}
    .charts-head p {{ margin: 0 0 14px; color: var(--muted); line-height: 1.5; }}
    .chart-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 10px; }}
    .chart-card {{ border: 1px solid #e4e7ec; border-radius: 8px; padding: 12px; background: #fcfcfd; }}
    .chart-title {{ display: flex; align-items: baseline; justify-content: space-between; gap: 8px; margin-bottom: 8px; }}
    .chart-title strong {{ font-size: 15px; }}
    .chart-title span {{ font-size: 13px; font-weight: 800; color: #344054; }}
    .mini-chart {{ display: block; width: 100%; height: 96px; }}
    .chart-meta {{ display: flex; justify-content: space-between; gap: 8px; color: #475467; font-size: 12px; margin-top: 6px; }}
    .chart-card small {{ display: block; color: #667085; font-size: 12px; margin-top: 6px; }}
    .chart-placeholder {{ display: flex; align-items: center; justify-content: center; min-height: 96px; background: #f2f4f7; border-radius: 6px; color: #667085; text-align: center; padding: 10px; }}
    .chart-missing {{ border-style: dashed; background: #fff; }}
    .news-dashboard {{ margin-top: var(--section-gap); background: #fff; border: 1px solid var(--line); border-radius: 8px; padding: var(--panel-pad); }}
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
    .news-list li {{ border: 1px solid #e1e5ec; border-radius: 8px; padding: 16px; background: #fff; line-height: 1.55; overflow-wrap: anywhere; }}
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
    .report-flow {{ display: grid; gap: var(--section-gap); min-width: 0; }}
    .report-section {{ min-width: 0; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: var(--panel-pad); margin-top: 0; line-height: 1.65; overflow-wrap: anywhere; box-shadow: 0 1px 2px rgba(16, 24, 40, 0.04); }}
    .report-section h2 {{ margin-top: 0; }}
    .report-section p {{ margin: 8px 0; max-width: 92ch; }}
    .report-heading {{ background: #111827; color: #fff; border-color: #111827; }}
    .report-heading h2 {{ margin: 0; }}
    .report-decision {{ border-left: 6px solid var(--blue); background: #f8fbff; }}
    .report-event {{ border-left: 6px solid #b54708; background: #fffbf5; }}
    .report-tracking {{ border-left: 6px solid #7a5af8; background: #fbfaff; }}
    .report-positive {{ border-left: 6px solid var(--green); }}
    .report-negative {{ border-left: 6px solid var(--red); }}
    .report-list {{ margin: 8px 0 12px; padding-left: 20px; }}
    .report-list li {{ margin: 6px 0; }}
    .report-table-wrap {{ width: 100%; max-width: 100%; overflow-x: auto; margin: 10px 0 16px; border: 1px solid #e4e7ec; border-radius: 8px; background: #fff; -webkit-overflow-scrolling: touch; }}
    .report-table {{ width: 100%; min-width: 720px; border-collapse: collapse; font-size: 13px; }}
    .report-table-wide {{ width: max-content; min-width: 100%; }}
    .report-table th, .report-table td {{ padding: 9px 10px; border-bottom: 1px solid #edf0f5; text-align: left; vertical-align: top; }}
    .report-table th {{ background: #f8fafc; color: #344054; font-weight: 800; white-space: normal; line-height: 1.25; }}
    .report-table td {{ color: #1d2939; line-height: 1.45; max-width: 280px; overflow-wrap: anywhere; }}
    .report-table td:first-child, .report-table th:first-child {{ position: sticky; left: 0; z-index: 1; background: #fff; box-shadow: 1px 0 0 #edf0f5; }}
    .report-table th:first-child {{ background: #f8fafc; z-index: 2; }}
    .report-table-wide td:last-child {{ max-width: 420px; }}
    .report-badge {{ display: inline-flex; align-items: center; min-height: 22px; padding: 2px 9px; border-radius: 999px; border: 1px solid transparent; font-size: 12px; font-weight: 800; line-height: 1; white-space: nowrap; }}
    .action-ok, .grade-a {{ background: #ecfdf3; color: #067647; border-color: #abefc6; }}
    .action-wait, .grade-b {{ background: #fffaeb; color: #b54708; border-color: #fedf89; }}
    .action-risk, .grade-c {{ background: #fef3f2; color: #b42318; border-color: #fecdca; }}
    .position-aggressive {{ background: #064e3b; color: #ecfdf3; border-color: #047857; }}
    .position-good, .rr-excellent {{ background: #ecfdf3; color: #067647; border-color: #abefc6; }}
    .position-add, .rr-good {{ background: #eff8ff; color: #175cd3; border-color: #b2ddff; }}
    .position-small, .rr-normal {{ background: #fffaeb; color: #b54708; border-color: #fedf89; }}
    .position-bad, .rr-bad {{ background: #fef3f2; color: #b42318; border-color: #fecdca; }}
    .numbered-line {{ margin-top: 16px !important; padding-top: 14px; border-top: 1px solid #edf0f5; font-weight: 700; }}
    .key-line strong {{ display: inline-block; min-width: 86px; color: #344054; }}
    footer {{ margin-top: 24px; color: var(--muted); font-size: 13px; text-align: center; }}
    @media (max-width: 900px) {{
      :root {{
        --page-x: 10px;
        --panel-pad: 14px;
        --section-gap: 10px;
      }}
      main {{ padding-top: 10px; padding-bottom: 36px; }}
      .hero, .quick-summary, .charts-section, .news-dashboard, .report-section, .news-list li {{
        border-radius: 8px;
        padding: var(--panel-pad);
      }}
      .hero {{ margin-bottom: var(--section-gap); }}
      h1 {{ font-size: 25px; line-height: 1.2; }}
      h2 {{ margin: 20px 0 10px; font-size: 19px; }}
      .quick-head h2 {{ margin-top: 0; font-size: 21px; }}
      .three-lines, .quick-split, .grid, .sector-score-grid, .chart-grid, .dashboard-grid {{
        grid-template-columns: 1fr;
        gap: 8px;
      }}
      .watch-actions ul {{ grid-template-columns: 1fr; }}
      .sector, .sector-score, .chart-card, .dashboard-grid div, .dashboard-action, .priority-news, .signal-block {{
        border-radius: 8px;
        padding: 12px;
      }}
      .score-parts {{ grid-template-columns: repeat(2, 1fr); }}
      .dashboard-head {{ display: block; }}
      .read-badge {{ margin-bottom: 10px; }}
      .market-line {{ font-size: 16px; }}
      .one-line, .quick-head p:last-child, .charts-head p, .dashboard-head p, .report-section {{
        line-height: 1.58;
      }}
      .news-list strong {{ font-size: 16px; line-height: 1.35; }}
      .news-list .importance-line, .news-list .impact-line {{ align-items: flex-start; }}
      .report-decision, .report-event, .report-tracking, .report-positive, .report-negative {{
        border-left-width: 4px;
      }}
      .report-list {{ padding-left: 17px; }}
      .key-line strong {{ display: block; min-width: 0; margin-bottom: 2px; }}
      .report-flow {{ gap: 10px; }}
      .report-section p {{ max-width: none; }}
      .report-table-wrap, .report-table-wrap-wide {{
        overflow-x: visible;
        border: 0;
        background: transparent;
        margin: 10px 0 12px;
      }}
      .report-table, .report-table-wide {{
        display: block;
        min-width: 0;
        width: 100%;
        border-collapse: separate;
        border-spacing: 0;
        font-size: 13px;
      }}
      .report-table thead {{ display: none; }}
      .report-table tbody, .report-table tr, .report-table td {{ display: block; width: 100%; }}
      .report-table tr {{
        margin-bottom: 10px;
        border: 1px solid #e4e7ec;
        border-radius: 8px;
        background: #fff;
        overflow: hidden;
      }}
      .report-table td {{
        display: grid;
        grid-template-columns: minmax(96px, 34%) minmax(0, 1fr);
        gap: 8px;
        padding: 9px 10px;
        border-bottom: 1px solid #edf0f5;
        max-width: none;
      }}
      .report-table td:first-child, .report-table th:first-child {{
        position: static;
        box-shadow: none;
      }}
      .report-table td:last-child {{ border-bottom: 0; }}
      .report-table td::before {{
        content: attr(data-label);
        color: #667085;
        font-weight: 800;
        overflow-wrap: anywhere;
      }}
      .report-badge, .importance-badge, .impact-badge {{
        white-space: normal;
        text-align: left;
        line-height: 1.2;
      }}
    }}
    {report_css}
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
    {jump_nav}
    {freshness}
    {quick_summary}
    {performance_section}
    {chart_section}
    <section class="sector-overview" id="sector-view">
      <div class="section-title-row">
        <h2>섹터맵</h2>
        <span>{html.escape(_sector_basis_label(snapshot))}</span>
      </div>
      <div class="grid">{''.join(sector_cards)}</div>
      <h2>섹터 점수판</h2>
      {sector_scoreboard}
    </section>
    {news_dashboard}
    <section class="news-analysis" id="news-analysis">
      <h2>주요 뉴스 분석</h2>
      <p class="section-guide">중요도 순으로 정렬했습니다. 첫 뉴스만 펼쳐 두고 나머지는 제목과 한줄 요약을 보고 선택해서 여세요.</p>
      <ol class="news-list">{news_cards}</ol>
    </section>
    <details class="full-report" id="full-report">
      <summary>
        <strong>전체 상세 근거 펼치기</strong>
        <span>위험판, 이벤트 일정, 전일 후보 추적, 투자 액션 표를 포함합니다.</span>
      </summary>
      <div class="report-flow">{rendered_sections}</div>
    </details>
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
    news_interpretations, interpretation_warnings = build_news_interpretations(
        news_items,
        api_key=config.openai_api_key,
        model=config.openai_model,
    )
    warnings.extend(interpretation_warnings)

    sectors = sorted(
        snapshot.sector_quotes.values(), key=lambda quote: quote.change_percent, reverse=True
    )
    investment_package = build_investment_package(snapshot, sectors, news_items)
    warnings.extend(investment_package.warnings)
    previous_signals = load_previous_investment_signals(REPORTS_DIR, target_date)
    tracking_text, tracking_warnings = build_previous_signal_review(snapshot, previous_signals)
    warnings.extend(tracking_warnings)
    recommendation_performance = collect_recommendation_performance(
        target_date,
        investment_package.signals,
        previous_signals,
    )
    warnings.extend(recommendation_performance.warnings)
    recommendation_performance_text = render_recommendation_performance(
        recommendation_performance
    )
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
        _data_freshness_text(snapshot),
        _quick_takeaways_text(snapshot, sectors, news_items),
        recommendation_performance_text,
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
        _sector_score_report(snapshot, sectors, news_items),
        _sector_driver_card(sectors, snapshot, news_items),
        _risk_card(snapshot),
        event_text,
        earnings_text,
        *_format_news(news_items, snapshot, watchlist_actions, news_interpretations),
        tracking_text,
        *([watchlist_text] if watchlist_text else []),
        *([sec_text] if sec_text else []),
        investment_package.text,
        _today_checklist(snapshot, news_items),
        "참고: 투자 판단용 참고 정보이며 매수/매도 추천은 아닙니다.\n출처: Yahoo Finance, RSS 뉴스",
    ]

    if warnings:
        blocks.append(_warnings_block(warnings))

    text = "\n\n".join(blocks)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"{target_date.isoformat()}_briefing.md"
    report_path.write_text(text, encoding="utf-8")
    write_investment_signals(REPORTS_DIR, investment_package, previous_signals)
    html_path = _write_html_report(
        report_path,
        text,
        snapshot,
        news_items,
        watchlist_actions,
        news_interpretations,
        recommendation_performance,
    )

    source_names = [snapshot.source] + sorted({item.source for item in news_items})
    return Briefing(
        text=text,
        report_path=report_path,
        html_path=html_path,
        sources=source_names,
        warnings=warnings,
    )
