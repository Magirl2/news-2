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
    setup_type: str
    judgement: str
    ma20: float | None
    ma50: float | None
    ma20_distance_percent: float | None
    ma50_distance_percent: float | None
    ma20_slope_percent: float | None
    ma50_slope_percent: float | None
    volume_ratio: float | None
    volume_ratio_3d: float | None
    volume_status: str
    chart_confidence_score: int
    chart_confidence_grade: str
    today_score: int
    today_grade: str
    check_price: float
    invalidation_price: float
    why_today: str
    why_not_yet: str


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


def _pct(current: float, base: float | None) -> float | None:
    if not base:
        return None
    return ((current - base) / base) * 100


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _moving_average(rows: list[dict], period: int, key: str = "close") -> float | None:
    values = [float(row[key]) for row in rows[-period:] if row.get(key) is not None]
    if len(values) < period:
        return None
    return _avg(values)


def _moving_average_slope(rows: list[dict], period: int, lookback: int = 5) -> float | None:
    if len(rows) < period + lookback:
        return None
    current = _moving_average(rows, period)
    previous = _moving_average(rows[:-lookback], period)
    return _pct(current, previous) if current is not None else None


def _distance_text(value: float | None) -> str:
    return "데이터 부족" if value is None else format_change(value)


def _price_text(value: float | None) -> str:
    return "데이터 부족" if value is None else _money(value)


def _ratio_text(value: float | None) -> str:
    return "확인 필요" if value is None else f"{value:.2f}배"


def _grade(score: int) -> str:
    if score >= 80:
        return "A"
    if score >= 60:
        return "B"
    return "C"


def _volume_status(volume_ratio: float | None, volume_ratio_3d: float | None, up_day: bool) -> str:
    if volume_ratio is None:
        return "거래량 확인 필요"
    if up_day and volume_ratio >= 1.3:
        return "반등 거래량 강함"
    if volume_ratio >= 1.2 or (volume_ratio_3d is not None and volume_ratio_3d >= 1.2):
        return "거래량 양호"
    if volume_ratio < 0.8:
        return "거래량 부족"
    return "거래량 보통"


def _support_price(close: float, ma20: float | None, recent_low: float) -> float:
    if ma20:
        return max(ma20, recent_low)
    return recent_low


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
    highs = [float(row.get("high", row["close"])) for row in recent]
    lows = [float(row.get("low", row["close"])) for row in recent]
    recent_high = max(highs)
    recent_low = min(lows)
    closes = [float(row["close"]) for row in rows]
    ma20 = _moving_average(rows, 20)
    ma50 = _moving_average(rows, 50)
    ma20_distance = _pct(close, ma20)
    ma50_distance = _pct(close, ma50)
    ma20_slope = _moving_average_slope(rows, 20)
    ma50_slope = _moving_average_slope(rows, 50)
    recent_5_change = _pct(close, closes[-6]) if len(closes) >= 6 else None
    volumes = [float(row["volume"]) for row in rows if row.get("volume") not in (None, "")]
    recent_volumes = [float(row["volume"]) for row in rows[-20:] if row.get("volume") not in (None, "")]
    volume_20_avg = _avg(recent_volumes) if len(recent_volumes) >= 10 else None
    current_volume = float(current["volume"]) if current.get("volume") not in (None, "") else None
    recent_3_volumes = [float(row["volume"]) for row in rows[-3:] if row.get("volume") not in (None, "")]
    volume_ratio = current_volume / volume_20_avg if current_volume and volume_20_avg else None
    volume_ratio_3d = (
        (_avg(recent_3_volumes) or 0) / volume_20_avg
        if len(recent_3_volumes) == 3 and volume_20_avg
        else None
    )
    has_ohlcv = all(
        key in current for key in ("open", "high", "low", "volume")
    ) and bool(volumes)
    up_day = close > previous_close
    return {
        "close": close,
        "change_percent": change_percent,
        "recent_high": recent_high,
        "recent_low": recent_low,
        "ma20": ma20,
        "ma50": ma50,
        "ma20_distance_percent": ma20_distance,
        "ma50_distance_percent": ma50_distance,
        "ma20_slope_percent": ma20_slope,
        "ma50_slope_percent": ma50_slope,
        "recent_5_change_percent": recent_5_change,
        "volume_ratio": volume_ratio,
        "volume_ratio_3d": volume_ratio_3d,
        "volume_status": _volume_status(volume_ratio, volume_ratio_3d, up_day),
        "has_ohlcv": has_ohlcv,
        "up_day": up_day,
    }


def _news_bias(news_items: list[NewsItem]) -> tuple[float, str]:
    score = 0.0
    labels = []
    for item in news_items:
        label = korean_news_label(item)
        sentiment, _reason = korean_news_sentiment(item)
        if label not in {"뉴스", "시장"}:
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


def _chart_confidence(metrics: dict, sector_quote: Quote) -> int:
    score = 45
    ma20_distance = metrics["ma20_distance_percent"]
    ma50_distance = metrics["ma50_distance_percent"]
    ma20_slope = metrics["ma20_slope_percent"]
    ma50_slope = metrics["ma50_slope_percent"]
    volume_ratio = metrics["volume_ratio"]
    if metrics["has_ohlcv"]:
        score += 15
    else:
        score -= 25
    if ma20_slope is not None and ma20_slope > 0:
        score += 10
    if ma50_slope is not None and ma50_slope > 0:
        score += 10
    if ma20_distance is not None and ma20_distance >= -2:
        score += 5
    if ma50_distance is not None and ma50_distance >= 0:
        score += 5
    if volume_ratio is not None:
        if volume_ratio >= 1.3 and metrics["up_day"]:
            score += 15
        elif volume_ratio >= 1.2:
            score += 10
        elif volume_ratio < 0.8:
            score -= 10
    if metrics["change_percent"] > sector_quote.change_percent:
        score += 5
    if ma20_distance is not None and ma20_distance >= 8:
        score -= 18
    if ma20_distance is not None and ma20_distance <= -3:
        score -= 15
    if ma50_distance is not None and ma50_distance < 0:
        score -= 10
    return max(0, min(100, round(score)))


def _today_attractiveness(metrics: dict, sector_quote: Quote, news_score: float) -> int:
    score = 35
    ma20_distance = metrics["ma20_distance_percent"]
    ma50_distance = metrics["ma50_distance_percent"]
    ma20_slope = metrics["ma20_slope_percent"]
    ma50_slope = metrics["ma50_slope_percent"]
    volume_ratio = metrics["volume_ratio"]
    recent_5_change = metrics["recent_5_change_percent"]
    if ma20_distance is not None and -2 <= ma20_distance <= 5:
        score += 30
    elif ma20_distance is not None and 5 < ma20_distance < 8:
        score += 8
    if ma20_slope is not None and ma20_slope > 0:
        score += 10
    if ma50_slope is not None and ma50_slope > 0:
        score += 6
    if ma50_distance is not None and ma50_distance >= 0:
        score += 8
    if volume_ratio is not None:
        if volume_ratio >= 1.3 and metrics["up_day"]:
            score += 18
        elif volume_ratio >= 1.2:
            score += 14
        elif volume_ratio < 0.8:
            score -= 15
    else:
        score -= 10
    if sector_quote.change_percent > 0:
        score += min(12, sector_quote.change_percent * 4)
    if metrics["change_percent"] > sector_quote.change_percent:
        score += 6
    score += max(-6, min(6, news_score * 5))
    if ma20_distance is not None and ma20_distance >= 8:
        score -= 35
    if recent_5_change is not None and recent_5_change >= 10:
        score -= 20
    if ma20_distance is not None and ma20_distance <= -3:
        score -= 25
    if ma50_distance is not None and ma50_distance < 0:
        score -= 18
    return max(0, min(100, round(score)))


def _classify_interest(metrics: dict, today_score: int) -> tuple[str, str]:
    ma20_distance = metrics["ma20_distance_percent"]
    ma50_distance = metrics["ma50_distance_percent"]
    volume_ratio = metrics["volume_ratio"]
    recent_5_change = metrics["recent_5_change_percent"]
    if (ma20_distance is not None and ma20_distance >= 8) or (
        recent_5_change is not None and recent_5_change >= 10
    ):
        return "추격 위험형", "신규 진입 관망"
    if ma20_distance is not None and ma20_distance <= -3:
        return "관망형", "20일선 회복 전 관망"
    if ma50_distance is not None and ma50_distance < 0:
        return "관망형", "50일선 회복 전 관망"
    if ma20_distance is not None and -2 <= ma20_distance <= 5:
        if volume_ratio is None:
            return "거래량 부족형", "거래량 확인 필요"
        if volume_ratio >= 1.2 and today_score >= 70:
            return "20일선 지지 확인형", "오늘 확인 후보"
        return "눌림목 대기형", "거래량 확인 후 판단"
    if ma20_distance is not None and 5 < ma20_distance < 8:
        return "돌파 대기형", "돌파 확인 전 관망"
    return "관망형", "가격 확인 전 관망"


def _interest_plan(symbol: str, name: str, sector_quote: Quote, snapshot: MarketSnapshot, news_items: list[NewsItem]) -> StockPlan:
    metrics = _stock_metrics(symbol, snapshot)
    close = metrics["close"]
    change_percent = metrics["change_percent"]
    recent_high = metrics["recent_high"]
    recent_low = metrics["recent_low"]
    ma20 = metrics["ma20"]
    ma50 = metrics["ma50"]
    news_score, news_labels = _news_bias(news_items)
    breakout = max(close * 1.01, recent_high * 1.001)
    support = _support_price(close, ma20, recent_low)
    stop = min(support * 0.985, close * 0.96)
    if stop >= close:
        stop = close * 0.96
    today_score = _today_attractiveness(metrics, sector_quote, news_score)
    chart_score = _chart_confidence(metrics, sector_quote)
    setup_type, judgement = _classify_interest(metrics, today_score)
    raw_score = (today_score - 50) / 5
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
    if metrics["ma20_distance_percent"] is not None:
        reasons.append(f"20일선 거리 {_distance_text(metrics['ma20_distance_percent'])}")
    reasons.append(metrics["volume_status"])
    if setup_type == "추격 위험형":
        reasons.append("20일선 이격/단기 급등으로 추격 위험")
    check_price = breakout
    invalidation_price = stop
    why_today = (
        f"{sector_name} 섹터가 {format_change(sector_quote.change_percent)}이고 "
        f"{symbol}은 20일선 거리 {_distance_text(metrics['ma20_distance_percent'])}, "
        f"거래량은 20일 평균 대비 {_ratio_text(metrics['volume_ratio'])}입니다."
    )
    if judgement == "오늘 확인 후보":
        why_today += " 20일선 근처에서 거래량이 받쳐주는지 확인할 만합니다."
    elif setup_type == "추격 위험형":
        why_today += " 추세와 관심은 살아 있지만 신규 진입은 눌림 확인이 먼저입니다."
    why_not_yet = (
        f"확인 가격 {_money(check_price)}를 넘기 전에는 단순 반등일 수 있고, "
        f"{_money(invalidation_price)} 이탈 시 20일선 지지 시나리오가 약해집니다."
    )
    return StockPlan(
        stance=judgement,
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
        buy_point=f"확인 가격 {_money(check_price)} 돌파 또는 20일선 근처({_price_text(ma20)}) 지지 확인",
        stop_point=f"무효화 가격 {_money(invalidation_price)} 이탈 시 관망 전환",
        buy_basis=why_today,
        stop_basis=why_not_yet,
        setup_type=setup_type,
        judgement=judgement,
        ma20=ma20,
        ma50=ma50,
        ma20_distance_percent=metrics["ma20_distance_percent"],
        ma50_distance_percent=metrics["ma50_distance_percent"],
        ma20_slope_percent=metrics["ma20_slope_percent"],
        ma50_slope_percent=metrics["ma50_slope_percent"],
        volume_ratio=metrics["volume_ratio"],
        volume_ratio_3d=metrics["volume_ratio_3d"],
        volume_status=metrics["volume_status"],
        chart_confidence_score=chart_score,
        chart_confidence_grade=_grade(chart_score),
        today_score=today_score,
        today_grade=_grade(today_score),
        check_price=check_price,
        invalidation_price=invalidation_price,
        why_today=why_today,
        why_not_yet=why_not_yet,
    )


def _avoid_plan(symbol: str, name: str, sector_quote: Quote, snapshot: MarketSnapshot, news_items: list[NewsItem]) -> StockPlan:
    metrics = _stock_metrics(symbol, snapshot)
    close = metrics["close"]
    change_percent = metrics["change_percent"]
    recent_high = metrics["recent_high"]
    recent_low = metrics["recent_low"]
    ma20 = metrics["ma20"]
    ma50 = metrics["ma50"]
    news_score, news_labels = _news_bias(news_items)
    reclaim = max(close * 1.03, recent_high * 0.98)
    stop = min(close * 0.95, recent_low * 0.995)
    if stop >= close:
        stop = close * 0.95
    raw_score = -(sector_quote.change_percent * 2 + change_percent + news_score)
    score = _score_100(raw_score)
    chart_score = _chart_confidence(metrics, sector_quote)
    today_score = _today_attractiveness(metrics, sector_quote, news_score)
    setup_type = "관망형"
    judgement = "비선호 후보"
    why_today = (
        f"{sector_quote.name} 섹터가 약하거나 종목 흐름이 약해 신규매수보다 회복 확인이 우선입니다. "
        f"20일선 거리 {_distance_text(metrics['ma20_distance_percent'])}, 거래량 {_ratio_text(metrics['volume_ratio'])}입니다."
    )
    why_not_yet = f"{_money(reclaim)} 회복 전에는 약세 흐름이 끝났다고 보기 어렵습니다."
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
        buy_basis=why_today,
        stop_basis=why_not_yet,
        setup_type=setup_type,
        judgement=judgement,
        ma20=ma20,
        ma50=ma50,
        ma20_distance_percent=metrics["ma20_distance_percent"],
        ma50_distance_percent=metrics["ma50_distance_percent"],
        ma20_slope_percent=metrics["ma20_slope_percent"],
        ma50_slope_percent=metrics["ma50_slope_percent"],
        volume_ratio=metrics["volume_ratio"],
        volume_ratio_3d=metrics["volume_ratio_3d"],
        volume_status=metrics["volume_status"],
        chart_confidence_score=chart_score,
        chart_confidence_grade=_grade(chart_score),
        today_score=today_score,
        today_grade=_grade(today_score),
        check_price=reclaim,
        invalidation_price=stop,
        why_today=why_today,
        why_not_yet=why_not_yet,
    )


def _format_plan_list(title: str, plans: list[StockPlan]) -> str:
    if not plans:
        return f"{title}\n데이터 부족으로 후보를 만들지 못했습니다."
    lines = [title]
    lines.extend(
        [
            "|종목|유형|판단|현재가|20일선/거리|50일선|거래량|차트 신뢰도|오늘 매력도|확인 가격|무효화 가격|",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for plan in plans:
        lines.append(
            "|"
            + "|".join(
                [
                    f"{plan.name}({plan.symbol})",
                    plan.setup_type,
                    plan.judgement,
                    f"{_money(plan.close)} {format_change(plan.change_percent)}",
                    f"{_price_text(plan.ma20)} / {_distance_text(plan.ma20_distance_percent)}",
                    _price_text(plan.ma50),
                    f"{plan.volume_status} {_ratio_text(plan.volume_ratio)}",
                    f"{plan.chart_confidence_grade}({plan.chart_confidence_score})",
                    f"{plan.today_grade}({plan.today_score})",
                    _money(plan.check_price),
                    _money(plan.invalidation_price),
                ]
            )
            + "|"
        )
    for index, plan in enumerate(plans, start=1):
        lines.extend([
            f"{index}. {plan.name}({plan.symbol}) / {plan.sector} / 유형 {plan.setup_type} / 판단 {plan.judgement} / 점수 {plan.score}/100({plan.grade})",
            f"   20일선: {_price_text(plan.ma20)} / 거리 {_distance_text(plan.ma20_distance_percent)} / 50일선: {_price_text(plan.ma50)} / 거래량: {plan.volume_status}({_ratio_text(plan.volume_ratio)})",
            f"   차트 신뢰도: {plan.chart_confidence_grade}({plan.chart_confidence_score}/100) / 오늘 매력도: {plan.today_grade}({plan.today_score}/100)",
            f"   확인 가격: {_money(plan.check_price)} / 무효화 가격: {_money(plan.invalidation_price)}",
            f"   오늘 봐야 하는 이유: {plan.why_today}",
            f"   아직 사면 안 되는 이유: {plan.why_not_yet}",
            f"   매수 타점: {plan.buy_point}",
            f"   손절 타점: {plan.stop_point}",
            f"   매수 근거: {plan.buy_basis}",
            f"   손절 근거: {plan.stop_basis}",
            f"   점수 이유: {', '.join(plan.score_reasons)}",
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
        "setup_type": plan.setup_type,
        "judgement": plan.judgement,
        "ma20": round(plan.ma20, 4) if plan.ma20 is not None else None,
        "ma50": round(plan.ma50, 4) if plan.ma50 is not None else None,
        "ma20_distance_percent": round(plan.ma20_distance_percent, 4) if plan.ma20_distance_percent is not None else None,
        "ma50_distance_percent": round(plan.ma50_distance_percent, 4) if plan.ma50_distance_percent is not None else None,
        "volume_ratio": round(plan.volume_ratio, 4) if plan.volume_ratio is not None else None,
        "volume_status": plan.volume_status,
        "chart_confidence_score": plan.chart_confidence_score,
        "chart_confidence_grade": plan.chart_confidence_grade,
        "today_score": plan.today_score,
        "today_grade": plan.today_grade,
        "check_price": round(plan.check_price, 4),
        "invalidation_price": round(plan.invalidation_price, 4),
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

def _evaluate_signal(signal: dict[str, Any], snapshot: MarketSnapshot) -> dict[str, Any]:
    symbol = str(signal["symbol"])
    current_close = _current_close(symbol, snapshot.target_date)
    previous_close = float(signal["close"])
    change = ((current_close - previous_close) / previous_close) * 100
    entry_price = float(signal["entry_price"])
    stop_price = float(signal["stop_price"])
    support_price = float(signal.get("support_price", previous_close))
    stance = str(signal.get("stance", ""))
    is_interest = "관심" in stance or "愿" in stance

    if is_interest:
        if current_close >= entry_price:
            state = "매수 가격 도달"
            verdict = "성공"
            reason = "전일 관심 후보가 제시한 매수 조건까지 올라왔습니다."
            next_action = (
                f"무리한 추격보다 {_money(entry_price)} 위에서 버티는지 보고, "
                f"{_money(support_price)} 이탈 시 비중 확대를 멈춥니다."
            )
        elif current_close <= stop_price:
            state = "손절/무효화 기준 이탈"
            verdict = "실패"
            reason = "관심 후보였지만 가격이 방어 기준을 깨서 전일 아이디어가 훼손됐습니다."
            next_action = f"관심 후보에서 제외하고 {_money(entry_price)} 회복 전까지 신규 매수는 보류합니다."
        elif current_close >= support_price or change > 0:
            state = "관찰 유지"
            verdict = "보류"
            reason = "매수 조건에는 못 닿았지만 지지권 또는 플러스 흐름은 유지했습니다."
            next_action = f"{_money(entry_price)} 돌파 여부를 다시 확인하고, {_money(stop_price)} 이탈 시 실패로 전환합니다."
        else:
            state = "관찰 유지"
            verdict = "보류"
            reason = "아직 매수 조건과 무효화 조건 사이에 있어 결론을 미루는 구간입니다."
            next_action = f"{_money(entry_price)} 회복 전에는 추격하지 말고 {_money(stop_price)} 방어 여부를 봅니다."
    else:
        if current_close >= entry_price:
            state = "회복 확인, 비선호 해제 검토"
            verdict = "실패"
            reason = "비선호 후보가 회복 기준을 넘어 약세 판단이 틀렸을 가능성이 커졌습니다."
            next_action = f"비선호에서 제외하고 강세가 유지되면 {_money(support_price)} 지지 여부를 새로 봅니다."
        elif current_close <= stop_price:
            state = "약세 지속, 회피 판단 유효"
            verdict = "성공"
            reason = "전일 비선호 판단대로 가격이 더 약해져 회피 아이디어가 맞았습니다."
            next_action = f"반등 매수는 계속 보류하고 {_money(entry_price)} 회복 전까지 위험 후보로 둡니다."
        else:
            state = "매수 보류 유지"
            verdict = "보류"
            reason = "회복 기준도 추가 약세 기준도 아직 확인되지 않았습니다."
            next_action = f"{_money(entry_price)} 회복이면 비선호 해제, {_money(stop_price)} 이탈이면 회피 판단 성공으로 봅니다."

    return {
        "symbol": symbol,
        "name": signal.get("name", symbol),
        "stance": stance,
        "score": signal.get("score", "?"),
        "current_close": current_close,
        "previous_close": previous_close,
        "change": change,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "support_price": support_price,
        "state": state,
        "verdict": verdict,
        "reason": reason,
        "next_action": next_action,
    }


def _format_tracked_signal(result: dict[str, Any]) -> str:
    return (
        f"- {result['name']}({result['symbol']}) / 전일 점수 {result['score']}/100 "
        f"/ 현재 {_money(float(result['current_close']))}({format_change(float(result['change']))}) "
        f"/ 판정: {result['verdict']} / 상태: {result['state']}\n"
        f"  이유: {result['reason']}\n"
        f"  다음 대응: {result['next_action']}"
    )


def _track_signal(signal: dict[str, Any], snapshot: MarketSnapshot) -> str:
    return _format_tracked_signal(_evaluate_signal(signal, snapshot))


def build_previous_signal_review(snapshot: MarketSnapshot, previous_signals: dict[str, Any] | None) -> tuple[str, list[str]]:
    warnings: list[str] = []
    if not previous_signals:
        return "전일 후보 추적\n이전 후보 기록이 없어 오늘부터 추적을 시작합니다.", warnings

    previous_date = previous_signals.get("target_date", "이전 거래일")
    lines = [f"전일 후보 추적\n기준: {previous_date} 후보를 {snapshot.target_date.isoformat()} 종가로 평가"]
    verdict_counts = {"성공": 0, "실패": 0, "보류": 0}

    interest = previous_signals.get("interest") or []
    avoid = previous_signals.get("avoid") or []
    if interest:
        lines.append("관심 후보 평가")
        for signal in interest:
            try:
                result = _evaluate_signal(signal, snapshot)
                verdict_counts[str(result["verdict"])] += 1
                lines.append(_format_tracked_signal(result))
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{signal.get('symbol', '후보')} 추적 실패: {exc}")
    if avoid:
        lines.append("비선호 후보 평가")
        for signal in avoid:
            try:
                result = _evaluate_signal(signal, snapshot)
                verdict_counts[str(result["verdict"])] += 1
                lines.append(_format_tracked_signal(result))
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{signal.get('symbol', '후보')} 추적 실패: {exc}")

    total = sum(verdict_counts.values())
    if total:
        lines.insert(
            1,
            f"요약: 성공 {verdict_counts['성공']} / 실패 {verdict_counts['실패']} / 보류 {verdict_counts['보류']}",
        )
    else:
        lines.append("평가할 전일 후보가 없습니다.")
    return "\n".join(lines), warnings
