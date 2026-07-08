from __future__ import annotations

from dataclasses import dataclass

from .market_data import MarketSnapshot, Quote, SECTOR_KO, fetch_yahoo_daily, format_change
from .news import NewsItem, korean_news_label, korean_news_sentiment


@dataclass(frozen=True)
class StockPlan:
    stance: str
    symbol: str
    name: str
    sector: str
    close: float
    change_percent: float
    score: float
    buy_point: str
    stop_point: str
    buy_basis: str
    stop_basis: str


SECTOR_STOCKS = {
    "Technology": [("NVDA", "엔비디아"), ("MSFT", "마이크로소프트"), ("AAPL", "애플"), ("AVGO", "브로드컴"), ("AMD", "AMD")],
    "Communication Services": [("META", "메타"), ("GOOGL", "알파벳"), ("NFLX", "넷플릭스"), ("DIS", "디즈니")],
    "Financials": [("JPM", "JP모건"), ("BAC", "뱅크오브아메리카"), ("GS", "골드만삭스"), ("MS", "모건스탠리")],
    "Consumer Discretionary": [("AMZN", "아마존"), ("TSLA", "테슬라"), ("HD", "홈디포"), ("MCD", "맥도날드")],
    "Industrials": [("GE", "GE"), ("CAT", "캐터필러"), ("RTX", "RTX"), ("LMT", "록히드마틴")],
    "Health Care": [("LLY", "일라이릴리"), ("UNH", "유나이티드헬스"), ("JNJ", "존슨앤드존슨"), ("MRK", "머크")],
    "Consumer Staples": [("COST", "코스트코"), ("WMT", "월마트"), ("PG", "P&G"), ("KO", "코카콜라")],
    "Energy": [("XOM", "엑손모빌"), ("CVX", "셰브론"), ("COP", "코노코필립스"), ("SLB", "SLB")],
    "Utilities": [("NEE", "넥스트에라"), ("SO", "서던"), ("DUK", "듀크에너지")],
    "Materials": [("LIN", "린데"), ("FCX", "프리포트맥모란"), ("NEM", "뉴몬트")],
    "Real Estate": [("PLD", "프로로지스"), ("AMT", "아메리칸타워"), ("EQIX", "이퀴닉스")],
}


def _money(value: float) -> str:
    return f"${value:.2f}"


def _eligible_rows(symbol: str, snapshot: MarketSnapshot) -> list[dict]:
    rows = [row for row in fetch_yahoo_daily(symbol) if row["date"] <= snapshot.target_date]
    if len(rows) < 5:
        raise RuntimeError(f"{symbol} 가격 데이터가 부족합니다.")
    return rows


def _stock_metrics(symbol: str, snapshot: MarketSnapshot) -> dict:
    rows = _eligible_rows(symbol, snapshot)
    current = rows[-1]
    previous = rows[-2]
    recent = rows[-20:]
    close = float(current["close"])
    previous_close = float(previous["close"])
    change_percent = ((close - previous_close) / previous_close) * 100
    recent_high = max(float(row["close"]) for row in recent)
    recent_low = min(float(row["close"]) for row in recent)
    return {"close": close, "change_percent": change_percent, "recent_high": recent_high, "recent_low": recent_low}


def _news_bias(news_items: list[NewsItem]) -> tuple[float, str]:
    score = 0.0
    labels = []
    for item in news_items:
        label = korean_news_label(item)
        sentiment, _reason = korean_news_sentiment(item)
        labels.append(label)
        if sentiment == "긍정":
            score += 0.4
        elif sentiment == "중립+":
            score += 0.2
        elif sentiment == "부정":
            score -= 0.4
        elif sentiment == "중립-":
            score -= 0.2
    unique_labels = ", ".join(dict.fromkeys(labels[:5])) or "주요 뉴스"
    return score, unique_labels


def _interest_plan(symbol: str, name: str, sector_quote: Quote, snapshot: MarketSnapshot, news_items: list[NewsItem]) -> StockPlan:
    metrics = _stock_metrics(symbol, snapshot)
    close = metrics["close"]
    change_percent = metrics["change_percent"]
    recent_high = metrics["recent_high"]
    recent_low = metrics["recent_low"]
    news_score, news_labels = _news_bias(news_items)
    breakout = max(close * 1.01, recent_high * 1.001)
    support = max(recent_low * 1.01, close * 0.96)
    stop = min(breakout * 0.93, support * 0.985)
    if stop >= breakout:
        stop = breakout * 0.93
    score = sector_quote.change_percent * 2 + change_percent + news_score
    if close >= recent_high * 0.97:
        score += 0.6
    sector_name = SECTOR_KO.get(sector_quote.name, sector_quote.name)
    return StockPlan(
        stance="관심 후보",
        symbol=symbol,
        name=name,
        sector=sector_name,
        close=close,
        change_percent=change_percent,
        score=score,
        buy_point=f"{_money(breakout)} 돌파 확인 또는 {_money(support)} 부근 지지 확인 시 분할매수",
        stop_point=f"{_money(stop)} 종가 이탈 시 손절 또는 비중 축소",
        buy_basis=f"{sector_name} 섹터가 {format_change(sector_quote.change_percent)}로 상대 강세이고 {symbol}은 당일 {format_change(change_percent)} 움직였습니다. 관련 뉴스 축은 {news_labels}입니다.",
        stop_basis=f"최근 가격 지지 구간({_money(support)})이 깨지면 섹터 강세가 종목 수급으로 이어진다는 가정이 훼손됩니다.",
    )


def _avoid_plan(symbol: str, name: str, sector_quote: Quote, snapshot: MarketSnapshot, news_items: list[NewsItem]) -> StockPlan:
    metrics = _stock_metrics(symbol, snapshot)
    close = metrics["close"]
    change_percent = metrics["change_percent"]
    recent_high = metrics["recent_high"]
    recent_low = metrics["recent_low"]
    news_score, news_labels = _news_bias(news_items)
    reclaim = max(close * 1.03, recent_high * 0.98)
    stop = min(close * 0.95, recent_low * 0.995)
    if stop >= close:
        stop = close * 0.95
    score = -(sector_quote.change_percent * 2 + change_percent + news_score)
    sector_name = SECTOR_KO.get(sector_quote.name, sector_quote.name)
    return StockPlan(
        stance="비선호 후보",
        symbol=symbol,
        name=name,
        sector=sector_name,
        close=close,
        change_percent=change_percent,
        score=score,
        buy_point=f"신규매수 보류. 최소 {_money(reclaim)} 회복 후 재검토",
        stop_point=f"보유 중이면 {_money(stop)} 종가 이탈 시 손절 또는 비중 축소",
        buy_basis=f"{sector_name} 섹터가 {format_change(sector_quote.change_percent)}로 약하고 {symbol}도 당일 {format_change(change_percent)} 흐름입니다. 관련 뉴스 축은 {news_labels}입니다.",
        stop_basis=f"최근 저점권({_money(recent_low)})이 다시 깨지면 반등 실패와 추가 매도 압력이 확인됩니다.",
    )


def _format_plan_list(title: str, plans: list[StockPlan]) -> str:
    if not plans:
        return f"{title}\n데이터 부족으로 후보를 만들지 못했습니다."
    lines = [title]
    for index, plan in enumerate(plans, start=1):
        lines.extend([
            f"{index}. {plan.name}({plan.symbol}) / {plan.sector} / 종가 {_money(plan.close)}({format_change(plan.change_percent)})",
            f"   매수 타점: {plan.buy_point}",
            f"   손절 타점: {plan.stop_point}",
            f"   매수 근거: {plan.buy_basis}",
            f"   손절 근거: {plan.stop_basis}",
        ])
    return "\n".join(lines)


def build_investment_report(snapshot: MarketSnapshot, sectors: list[Quote], news_items: list[NewsItem]) -> tuple[str, list[str]]:
    warnings: list[str] = []
    if not sectors:
        return "투자 액션 보고서\n섹터 데이터가 없어 종목 후보를 만들지 못했습니다.", warnings
    strong_sectors = sectors[:2]
    weak_sectors = list(reversed(sectors[-2:]))
    interest_plans: list[StockPlan] = []
    avoid_plans: list[StockPlan] = []
    for sector_quote in strong_sectors:
        for symbol, name in SECTOR_STOCKS.get(sector_quote.name, [])[:5]:
            try:
                interest_plans.append(_interest_plan(symbol, name, sector_quote, snapshot, news_items))
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{symbol} 관심 후보 계산 실패: {exc}")
    for sector_quote in weak_sectors:
        for symbol, name in SECTOR_STOCKS.get(sector_quote.name, [])[:5]:
            try:
                avoid_plans.append(_avoid_plan(symbol, name, sector_quote, snapshot, news_items))
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{symbol} 비선호 후보 계산 실패: {exc}")
    interest_plans.sort(key=lambda plan: plan.score, reverse=True)
    avoid_plans.sort(key=lambda plan: plan.score, reverse=True)
    strong_names = ", ".join(f"{SECTOR_KO.get(quote.name, quote.name)} {format_change(quote.change_percent)}" for quote in strong_sectors)
    weak_names = ", ".join(f"{SECTOR_KO.get(quote.name, quote.name)} {format_change(quote.change_percent)}" for quote in weak_sectors)
    text = "\n\n".join([
        "투자 액션 보고서",
        "유의 섹터\n"
        f"- 강하게 볼 섹터: {strong_names}\n"
        f"- 조심할 섹터: {weak_names}\n"
        "- 원칙: 강한 섹터 안에서 지지/돌파가 확인되는 종목만 보고, 약한 섹터 종목은 회복 전 신규매수를 보류합니다.",
        _format_plan_list("관심 후보", interest_plans),
        _format_plan_list("비선호 후보", avoid_plans),
        "주의\n개인 맞춤 투자자문이 아니라 규칙 기반 시장 참고자료입니다. 실제 주문 전 호가, 거래량, 실적 일정, 뉴스 원문을 다시 확인하세요.",
    ])
    return text, warnings
