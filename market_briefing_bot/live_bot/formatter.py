from __future__ import annotations

from ..investment_plan import _money
from .alerts import Alert
from .analyzer import LiveAnalysis, news_lines
from .commands import condition_label


def _money_or_check(value: float | None) -> str:
    return "확인 필요" if value is None else _money(value)


def _risk_reward(value: float | None) -> str:
    return "확인 필요" if value is None else f"{value:.1f}R"


def format_analysis(analysis: LiveAnalysis, *, max_chars: int = 3500) -> str:
    plan = analysis.plan
    lines = [
        f"{analysis.symbol} 최신 분석",
        f"- 현재가: {_money(plan.close)} ({plan.change_percent:+.2f}%)",
        f"- 판단: {plan.entry_action} + {plan.position_mode}",
        f"- 시작 비중: {plan.start_weight_percent}% / 최대 시작 비중: {plan.max_start_weight_percent}%",
        f"- 손익비: {_risk_reward(plan.risk_reward_ratio)}, {plan.risk_reward_grade}",
        f"- 시작 진입가: {_money_or_check(plan.start_entry_price)}",
        f"- 추가 진입가: {_money_or_check(plan.add_entry_price)}",
        f"- 확인 진입가: {_money_or_check(plan.confirm_entry_price)}",
        f"- 무효화 가격: {_money(plan.invalidation_price)}",
        f"- 1차 목표가: {_money_or_check(plan.first_target_price)}",
        f"- 지금 가능한 이유: {plan.can_enter_reason}",
        f"- 위험한 이유: {plan.entry_risk}",
        f"- 크게 들어가도 되는 조건: {plan.size_up_condition}",
        f"- 작게만 봐야 하는 이유: {plan.small_only_reason}",
        f"- 추가 매수 조건: {plan.add_condition}",
        f"- 아침 보고서 비교: {analysis.comparison}",
        "- 최신 뉴스:",
        *[f"  {line}" for line in news_lines(analysis.related_news)],
        f"- 기준 시각: {analysis.data_time}",
        "- 주의: 실제 주문 전 호가, 거래량, 장중 뉴스는 다시 확인하세요.",
    ]
    if analysis.warnings:
        lines.append(f"- 확인 필요: {analysis.warnings[0]}")
    text = "\n".join(lines)
    if len(text) <= max_chars:
        return text
    keep = text[: max_chars - 90].rstrip()
    return keep + "\n- 내용이 길어 일부를 줄였습니다. 핵심 가격과 무효화 기준을 우선 확인하세요."


def format_alert_registered(alert: Alert, current_price: float | None = None) -> str:
    lines = [
        f"{alert.symbol} 알림 등록 완료",
        f"- 조건: {_condition_text(alert)}",
    ]
    if current_price is not None:
        lines.append(f"- 현재가: {_money(current_price)}")
    lines.extend([
        f"- 알림 가격: {_money(alert.target_price)}",
        "- 도달 시 1회 알림 후 자동 비활성화됩니다.",
    ])
    return "\n".join(lines)


def format_alert_list(alerts: list[Alert]) -> str:
    if not alerts:
        return "활성 알림이 없습니다."
    lines = ["활성 알림"]
    for alert in alerts:
        lines.append(f"{alert.id}. {alert.symbol} {_condition_text(alert)}")
    return "\n".join(lines)


def format_alert_deleted(count: int) -> str:
    return f"알림 {count}개를 삭제했습니다." if count else "삭제할 활성 알림을 찾지 못했습니다."


def format_alert_triggered(alert: Alert, current_price: float, analysis: LiveAnalysis | None = None) -> str:
    lines = [
        f"{alert.symbol} 조건 도달",
        f"- 조건: {_condition_text(alert)}",
        f"- 현재가: {_money(current_price)}",
    ]
    if analysis:
        plan = analysis.plan
        lines.extend([
            f"- 기존 판단: {plan.entry_action} + {plan.position_mode}",
            f"- 확인할 것: {plan.size_up_condition}",
            f"- 무효화: {_money(plan.invalidation_price)} 이탈",
            f"- 기준 시각: {analysis.data_time}",
        ])
    return "\n".join(lines)


def help_text() -> str:
    return "\n".join([
        "텔레그램 투자 보조 봇 사용법",
        "- AMD",
        "- NVDA 지금 어때",
        "- TSLA 손익비",
        "- AMD 155 돌파 알림",
        "- NVDA 150 이탈 알림",
        "- AMD 추가진입 알림",
        "- AMD 무효화 알림",
        "- 알림 목록",
        "- 알림 삭제 AMD",
        "- 알림 전체 삭제",
        "표현은 리스크 관리용 참고이며 주문 지시가 아닙니다.",
    ])


def _condition_text(alert: Alert) -> str:
    return f"{_format_price(alert.target_price)} {condition_label(alert.condition_type)}"


def _format_price(price: float) -> str:
    return f"{price:g}"
