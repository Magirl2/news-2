from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

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
    raw_score: float
    score: int
    grade: str
    score_reasons: list[str]
    entry_price: float
    support_price: float
    stop_price: float
    buy_point: str
    stop_point: str
    buy_basis: str
    stop_basis: str


@dataclass(frozen=True)
class InvestmentPackage:
    text: str
    warnings: list[str]
    interest_plans: list[StockPlan]
    avoid_plans: list[StockPlan]
    signals: dict[str, Any]


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
    "Real Estate": [("PLD", "프로로지스"), ("AMT", "아메리칸타워"), ("EQIX", "에퀴닉스")],
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
    return {
        "close": close,
        "change_percent": change_percent,
        "recent_high": recent_high,
        "recent_low": recent_low,
    }


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


def _score_100(raw_score: float) -> int:
    return max(0, min(100, round(50 + raw_score * 5)))


def _score_grade(score: int) -> str:
    if score >= 80:
        return "강함"
    if score >= 65:
        return "관찰 우위"
    if score >= 50:
        return "중립 이상"
    return "낮음"


def _score_reasons(
    sector_quote: Quote,
    change_percent: float,
    close: float,
    recent_high: float,
    news_labels: str,
    *,
    is_interest: bool,
) -> list[str]:
    reasons: list[str] = []
    if is_interest:
        if sector_quote.change_percent >= 1.5:
            reasons.append("섹터 강한 상승")
        elif sector_quote.change_percent > 0:
            reasons.append("섹터 상대 강세")
        if change_percent >= 2:
            reasons.append("종목 당일 강세")
        elif change_percent > 0:
            reasons.append("종목 플러스 유지")
        if recent_high and close >= recent_high * 0.97:
            reasons.append("20일 고점권")
    else:
        if sector_quote.change_percent <= -1.5:
            reasons.append("섹터 강한 약세")
        elif sector_quote.change_percent < 0:
            reasons.append("섹터 상대 약세")
        if change_percent <= -2:
            reasons.append("종목 당일 급락")
        elif change_percent < 0:
            reasons.append("종목 마이너스")
        if recent_high and close <= recent_high * 0.9:
            reasons.append("고점 대비 이탈")
    reasons.append(f"뉴스 축: {news_labels}")
    return reasons


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
    raw_score = sector_quote.change_percent * 2 + change_percent + news_score
    if close >= recent_high * 0.97:
        raw_score += 0.6
    score = _score_100(raw_score)
    sector_name = SECTOR_KO.get(sector_quote.name, sector_quote.name)
    reasons = _score_reasons(
        sector_quote,
        change_percent,
        close,
        recent_high,
        news_labels,
        is_interest=True,
    )
    return StockPlan(
        stance="관심 후보",
        symbol=symbol,
        name=name,
        sector=sector_name,
        close=close,
        change_percent=change_percent,
        raw_score=raw_score,
        score=score,
        grade=_score_grade(score),
        score_reasons=reasons,
        entry_price=breakout,
        support_price=support,
        stop_price=stop,
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
    raw_score = -(sector_quote.change_percent * 2 + change_percent + news_score)
    score = _score_100(raw_score)
    sector_name = SECTOR_KO.get(sector_quote.name, sector_quote.name)
    reasons = _score_reasons(
        sector_quote,
        change_percent,
        close,
        recent_high,
        news_labels,
        is_interest=False,
    )
    return StockPlan(
        stance="비선호 후보",
        symbol=symbol,
        name=name,
        sector=sector_name,
        close=close,
        change_percent=change_percent,
        raw_score=raw_score,
        score=score,
        grade=_score_grade(score),
        score_reasons=reasons,
        entry_price=reclaim,
        support_price=recent_low,
        stop_price=stop,
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
            f"{index}. {plan.name}({plan.symbol}) / {plan.sector} / 점수 {plan.score}/100({plan.grade}) / 종가 {_money(plan.close)}({format_change(plan.change_percent)})",
            f"   점수 이유: {', '.join(plan.score_reasons)}",
            f"   매수 타점: {plan.buy_point}",
            f"   손절 타점: {plan.stop_point}",
            f"   매수 근거: {plan.buy_basis}",
            f"   손절 근거: {plan.stop_basis}",
        ])
    return "\n".join(lines)


def _plan_signal(plan: StockPlan, target_date: date) -> dict[str, Any]:
    return {
        "date": target_date.isoformat(),
        "stance": plan.stance,
        "symbol": plan.symbol,
        "name": plan.name,
        "sector": plan.sector,
        "close": round(plan.close, 4),
        "change_percent": round(plan.change_percent, 4),
        "score": plan.score,
        "grade": plan.grade,
        "entry_price": round(plan.entry_price, 4),
        "support_price": round(plan.support_price, 4),
        "stop_price": round(plan.stop_price, 4),
    }


def _signals(target_date: date, interest_plans: list[StockPlan], avoid_plans: list[StockPlan]) -> dict[str, Any]:
    return {
        "target_date": target_date.isoformat(),
        "interest": [_plan_signal(plan, target_date) for plan in interest_plans],
        "avoid": [_plan_signal(plan, target_date) for plan in avoid_plans],
    }


def build_investment_package(snapshot: MarketSnapshot, sectors: list[Quote], news_items: list[NewsItem]) -> InvestmentPackage:
    warnings: list[str] = []
    if not sectors:
        text = "투자 액션 보고서\n섹터 데이터가 없어 종목 후보를 만들지 못했습니다."
        return InvestmentPackage(text=text, warnings=warnings, interest_plans=[], avoid_plans=[], signals=_signals(snapshot.target_date, [], []))

    strong_sectors = sectors[:2]
    weak_sectors = list(reversed(sectors[-2:]))
    interest_plans: list[StockPlan] = []
    avoid_plans: list[StockPlan] = []

    for sector_quote in strong_sectors:
        for symbol, name in SECTOR_STOCKS.get(sector_quote.name, []):
            try:
                interest_plans.append(_interest_plan(symbol, name, sector_quote, snapshot, news_items))
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{symbol} 관심 후보 계산 실패: {exc}")

    for sector_quote in weak_sectors:
        for symbol, name in SECTOR_STOCKS.get(sector_quote.name, []):
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
    return InvestmentPackage(
        text=text,
        warnings=warnings,
        interest_plans=interest_plans,
        avoid_plans=avoid_plans,
        signals=_signals(snapshot.target_date, interest_plans, avoid_plans),
    )


def build_investment_report(snapshot: MarketSnapshot, sectors: list[Quote], news_items: list[NewsItem]) -> tuple[str, list[str]]:
    package = build_investment_package(snapshot, sectors, news_items)
    return package.text, package.warnings


def write_investment_signals(reports_dir: Path, package: InvestmentPackage) -> None:
    signals_dir = reports_dir / "signals"
    signals_dir.mkdir(parents=True, exist_ok=True)
    target_date = package.signals["target_date"]
    text = json.dumps(package.signals, ensure_ascii=False, indent=2)
    (signals_dir / f"{target_date}_signals.json").write_text(text, encoding="utf-8")
    (signals_dir / "latest.json").write_text(text, encoding="utf-8")


def load_previous_investment_signals(reports_dir: Path, current_date: date) -> dict[str, Any] | None:
    workflow_seed = reports_dir / "previous_signals.json"
    if workflow_seed.exists():
        try:
            data = json.loads(workflow_seed.read_text(encoding="utf-8"))
            if data.get("target_date") != current_date.isoformat():
                return data
        except json.JSONDecodeError:
            return None

    signals_dir = reports_dir / "signals"
    if not signals_dir.exists():
        return None
    candidates = []
    for path in signals_dir.glob("*_signals.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if data.get("target_date") and data["target_date"] < current_date.isoformat():
            candidates.append(data)
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item["target_date"])[-1]


def _current_close(symbol: str, target_date: date) -> float:
    rows = [row for row in fetch_yahoo_daily(symbol) if row["date"] <= target_date]
    if len(rows) < 1:
        raise RuntimeError(f"{symbol} 현재 가격 데이터가 없습니다.")
    return float(rows[-1]["close"])


def _track_signal(signal: dict[str, Any], snapshot: MarketSnapshot) -> str:
    symbol = str(signal["symbol"])
    current_close = _current_close(symbol, snapshot.target_date)
    previous_close = float(signal["close"])
    change = ((current_close - previous_close) / previous_close) * 100
    entry_price = float(signal["entry_price"])
    stop_price = float(signal["stop_price"])
    stance = str(signal.get("stance", ""))

    if stance == "관심 후보":
        if current_close >= entry_price:
            state = "매수 타점 도달"
        elif current_close <= stop_price:
            state = "손절/무효화 기준 이탈"
        else:
            state = "관찰 지속"
    else:
        if current_close >= entry_price:
            state = "회복 확인, 비선호 해제 검토"
        elif current_close <= stop_price:
            state = "약세 지속, 회피 판단 유효"
        else:
            state = "매수 보류 유지"

    return (
        f"- {signal.get('name', symbol)}({symbol}) / 전일 점수 {signal.get('score', '?')}/100 "
        f"/ 현재 {_money(current_close)}({format_change(change)}) / 상태: {state}"
    )


def build_previous_signal_review(snapshot: MarketSnapshot, previous_signals: dict[str, Any] | None) -> tuple[str, list[str]]:
    warnings: list[str] = []
    if not previous_signals:
        return "전일 후보 추적\n이전 후보 기록이 없어 오늘부터 추적을 시작합니다.", warnings

    previous_date = previous_signals.get("target_date", "이전 거래일")
    lines = [f"전일 후보 추적\n기준: {previous_date} 후보를 {snapshot.target_date.isoformat()} 종가로 점검"]

    interest = previous_signals.get("interest") or []
    avoid = previous_signals.get("avoid") or []
    if interest:
        lines.append("관심 후보 점검")
        for signal in interest:
            try:
                lines.append(_track_signal(signal, snapshot))
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{signal.get('symbol', '후보')} 추적 실패: {exc}")
    if avoid:
        lines.append("비선호 후보 점검")
        for signal in avoid:
            try:
                lines.append(_track_signal(signal, snapshot))
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{signal.get('symbol', '후보')} 추적 실패: {exc}")
    return "\n".join(lines), warnings
