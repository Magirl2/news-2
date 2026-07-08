from __future__ import annotations

from .market_data import MarketSnapshot, Quote, SECTOR_KO, format_change
from .news import NewsItem, korean_news_label, korean_news_sentiment


GROWTH_SECTORS = {"Technology", "Communication Services", "Consumer Discretionary"}
DEFENSIVE_SECTORS = {"Utilities", "Consumer Staples", "Health Care"}


def _average_index_change(snapshot: MarketSnapshot) -> float:
    changes = [quote.change_percent for quote in snapshot.index_quotes.values()]
    if not changes:
        return 0.0
    return sum(changes) / len(changes)


def _sector_breadth(sectors: list[Quote]) -> tuple[int, int]:
    up = sum(1 for quote in sectors if quote.change_percent > 0)
    down = sum(1 for quote in sectors if quote.change_percent < 0)
    return up, down


def _risk_quote(snapshot: MarketSnapshot, name: str) -> Quote | None:
    return snapshot.risk_quotes.get(name)


def _risk_flags(snapshot: MarketSnapshot) -> tuple[list[str], list[str]]:
    positive: list[str] = []
    negative: list[str] = []
    vix = _risk_quote(snapshot, "VIX")
    ten_year = _risk_quote(snapshot, "10Y Yield")
    dollar = _risk_quote(snapshot, "Dollar")
    oil = _risk_quote(snapshot, "Oil")

    if vix:
        if vix.change_percent <= -3:
            positive.append(f"VIX 하락({format_change(vix.change_percent)})")
        elif vix.change_percent >= 3:
            negative.append(f"VIX 급등({format_change(vix.change_percent)})")
    if ten_year:
        if ten_year.change_percent <= -1:
            positive.append(f"10년물 금리 부담 완화({format_change(ten_year.change_percent)})")
        elif ten_year.change_percent >= 1.5:
            negative.append(f"10년물 금리 상승({format_change(ten_year.change_percent)})")
    if dollar and dollar.change_percent >= 0.4:
        negative.append(f"달러 강세({format_change(dollar.change_percent)})")
    if oil and oil.change_percent >= 2:
        negative.append(f"유가 상승({format_change(oil.change_percent)})")

    return positive, negative


def _news_tone(news_items: list[NewsItem]) -> tuple[list[str], list[str]]:
    positives: list[str] = []
    negatives: list[str] = []
    seen: set[str] = set()
    for item in news_items[:5]:
        label = korean_news_label(item)
        sentiment, reason = korean_news_sentiment(item)
        key = f"{label}:{reason}"
        if key in seen:
            continue
        seen.add(key)
        if sentiment in {"긍정", "중립+"}:
            positives.append(f"{label}: {reason}")
        elif sentiment in {"부정", "중립-"}:
            negatives.append(f"{label}: {reason}")
    return positives[:3], negatives[:3]


def _mode(snapshot: MarketSnapshot, sectors: list[Quote]) -> tuple[str, str, str]:
    avg_index = _average_index_change(snapshot)
    up, down = _sector_breadth(sectors)
    positive_risk, negative_risk = _risk_flags(snapshot)
    top_sector = sectors[0].name if sectors else ""

    if avg_index >= 0.45 and up >= max(6, down + 2) and len(negative_risk) == 0:
        return (
            "공격적 관찰",
            "신규 진입은 강세 섹터의 상위 후보만 분할로 접근",
            "강세 섹터가 장 초반에도 버티고 VIX가 재상승하지 않는지 확인",
        )
    if avg_index <= -0.45 or len(negative_risk) >= 2:
        return (
            "방어 우선",
            "신규 매수는 보류하고 보유 종목의 손절/축소 기준을 먼저 점검",
            "지수 반등보다 VIX, 금리, 달러가 진정되는지 먼저 확인",
        )
    if top_sector in DEFENSIVE_SECTORS and up <= down:
        return (
            "방어적 선별",
            "방어 섹터 강세는 추격보다 현금 비중과 리스크 관리를 우선",
            "성장주와 경기민감주가 함께 회복되는지 확인 전까지 관망",
        )
    if top_sector in GROWTH_SECTORS and positive_risk:
        return (
            "성장주 선별",
            "AI/기술주 후보는 점수와 타점이 맞는 종목만 제한적으로 관찰",
            "금리 반등이나 섹터 내부 약세가 나오면 추격하지 않기",
        )
    return (
        "선별 관찰",
        "신규 진입은 평소보다 작게, 강세 섹터와 개별 종목이 같이 강한 경우만 확인",
        "섹터 순위가 장중에도 유지되는지와 주요 뉴스의 추가 반응을 확인",
    )


def _sector_rotation_text(sectors: list[Quote]) -> str:
    if not sectors:
        return "섹터 데이터가 부족해 로테이션 판단을 만들 수 없습니다."

    top = sectors[0]
    bottom = sectors[-1]
    up, down = _sector_breadth(sectors)
    top_name = SECTOR_KO.get(top.name, top.name)
    bottom_name = SECTOR_KO.get(bottom.name, bottom.name)

    if top.name in GROWTH_SECTORS and bottom.name in DEFENSIVE_SECTORS:
        interpretation = "위험 선호가 살아난 흐름입니다. 다만 강세 섹터 안에서도 상위 종목과 후발 종목을 구분해야 합니다."
    elif top.name in DEFENSIVE_SECTORS:
        interpretation = "방어주가 상대적으로 강한 날입니다. 지수 상승이 있더라도 공격적으로 보기보다 리스크 관리를 우선합니다."
    elif bottom.name in GROWTH_SECTORS:
        interpretation = "성장주 쪽 부담이 큽니다. 반등 매수보다 금리와 대형 기술주의 회복 여부를 먼저 봅니다."
    else:
        interpretation = "특정 섹터로 자금이 이동한 날입니다. 강한 섹터 안에서 거래대금과 지지선을 같이 확인합니다."

    return (
        f"주도 섹터: {top_name} {format_change(top.change_percent)}\n"
        f"약한 섹터: {bottom_name} {format_change(bottom.change_percent)}\n"
        f"시장 폭: 상승 {up} / 하락 {down}\n"
        f"해석: {interpretation}"
    )


def _trading_score(
    snapshot: MarketSnapshot,
    sectors: list[Quote],
    positive_risk: list[str],
    negative_risk: list[str],
) -> tuple[int, str, str]:
    avg_index = _average_index_change(snapshot)
    up, down = _sector_breadth(sectors)
    score = 50
    score += round(avg_index * 18)
    score += min(14, max(-14, (up - down) * 3))
    score += min(12, len(positive_risk) * 4)
    score -= min(18, len(negative_risk) * 6)

    if sectors:
        top = sectors[0]
        bottom = sectors[-1]
        if top.name in GROWTH_SECTORS and top.change_percent > 1:
            score += 7
        if top.name in DEFENSIVE_SECTORS and up <= down:
            score -= 8
        if bottom.name in GROWTH_SECTORS and bottom.change_percent < -1:
            score -= 9

    score = max(0, min(100, score))
    if score >= 75:
        return score, "선별 매수 가능", "강세 섹터의 상위 후보만 분할로 접근합니다."
    if score >= 60:
        return score, "관찰 우위", "매수보다 후보 압축과 타점 확인을 우선합니다."
    if score >= 45:
        return score, "관망 우위", "신규 진입은 줄이고 보유 종목 리스크를 먼저 봅니다."
    return score, "방어 우선", "현금 비중, 손절 기준, 포트폴리오 쏠림을 먼저 점검합니다."


def _warning_text(sectors: list[Quote], negative_risk: list[str]) -> str:
    warnings: list[str] = []
    if negative_risk:
        warnings.append("리스크 지표가 불리합니다: " + " / ".join(negative_risk[:3]))
    if sectors:
        top = sectors[0]
        bottom = sectors[-1]
        top_name = SECTOR_KO.get(top.name, top.name)
        bottom_name = SECTOR_KO.get(bottom.name, bottom.name)
        if top.name in DEFENSIVE_SECTORS:
            warnings.append(f"{top_name} 주도는 공격적 매수보다 방어적 수급일 수 있습니다.")
        if bottom.name in GROWTH_SECTORS:
            warnings.append(f"{bottom_name} 약세는 성장주 반등 매수의 무효화 신호가 될 수 있습니다.")
        if abs(top.change_percent - bottom.change_percent) >= 3:
            warnings.append("섹터 간 격차가 커서 종목 선택 실패 시 손실이 빨라질 수 있습니다.")
    if not warnings:
        warnings.append("큰 경고 신호는 제한적이지만, 뉴스보다 실제 가격 반응을 우선 확인합니다.")
    return "\n".join(f"- {item}" for item in warnings[:4])


def build_professional_review(
    snapshot: MarketSnapshot,
    sectors: list[Quote],
    news_items: list[NewsItem],
) -> str:
    mode, position_rule, action_rule = _mode(snapshot, sectors)
    positive_risk, negative_risk = _risk_flags(snapshot)
    positive_news, negative_news = _news_tone(news_items)

    positive_items = positive_risk + positive_news
    negative_items = negative_risk + negative_news
    positive_text = " / ".join(positive_items[:4]) if positive_items else "뚜렷한 긍정 촉매는 제한적입니다."
    negative_text = " / ".join(negative_items[:4]) if negative_items else "즉시 경계할 악재 신호는 제한적입니다."
    trading_score, trading_grade, trading_rule = _trading_score(
        snapshot,
        sectors,
        positive_risk,
        negative_risk,
    )

    invalidation = (
        "강세 섹터가 장 초반 약세로 돌아서거나, VIX/금리/달러가 동시에 튀면 당일 신규 진입 판단을 낮춥니다."
    )

    return "\n\n".join(
        [
            (
                "오늘 매매 가능 점수\n"
                f"점수: {trading_score}/100 ({trading_grade})\n"
                f"판단: {trading_rule}\n"
                "사용법: 점수는 매수 추천이 아니라 당일 시장 환경의 우호도를 나타냅니다."
            ),
            (
                "전문 투자자 체크\n"
                f"판단: {mode}\n"
                f"매매 강도: {position_rule}\n"
                f"오늘 할 일: {action_rule}\n"
                f"무효화 조건: {invalidation}"
            ),
            "오늘의 경고\n" + _warning_text(sectors, negative_risk),
            (
                "핵심 리스크와 촉매\n"
                f"긍정 신호: {positive_text}\n"
                f"부정 신호: {negative_text}\n"
                "확인 순서: 지수 선물 → 주도 섹터 ETF → 후보 종목 거래대금 → 손절 기준"
            ),
            "섹터 로테이션 판정\n" + _sector_rotation_text(sectors),
        ]
    )
