from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from statistics import mean
from typing import Any, Callable, Iterable

from .market_data import fetch_yahoo_daily


PriceFetcher = Callable[[str], list[dict[str, Any]]]


@dataclass(frozen=True)
class SignalEvaluation:
    signal_date: date
    section: str
    symbol: str
    name: str
    bucket: str
    reference_price: float | None
    invalidation_price: float | None
    first_target_price: float | None
    returns: dict[int, float | None]
    spy_relative_5d: float | None
    latest_price: float | None
    holding_days: int
    current_return: float | None
    spy_relative_current: float | None
    max_favorable_percent: float | None
    max_adverse_percent: float | None
    outcome: str
    r_result: float | None
    note: str


@dataclass(frozen=True)
class RecommendationPerformance:
    """Consumer-facing performance view for recommendations saved by prior reports."""

    current_candidates: list[dict[str, Any]]
    evaluations: list[SignalEvaluation]
    history_days: int
    excluded_current_count: int
    warnings: list[str]


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def _float_value(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _percent(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:+.2f}%"


def _money(value: float | None) -> str:
    if value is None:
        return "-"
    return f"${value:,.2f}"


def _r_text(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:+.2f}R"


def _signal_bucket(signal: dict[str, Any], section: str) -> str:
    candidate_grade = str(signal.get("candidate_grade") or "").strip()
    entry_style = str(signal.get("entry_style") or "").strip()
    if candidate_grade and entry_style:
        return f"{candidate_grade} / {entry_style}"
    if candidate_grade:
        return candidate_grade
    state = str(signal.get("recommendation_state") or "").strip()
    if state:
        return state
    entry_action = str(signal.get("entry_action") or signal.get("judgement") or signal.get("stance") or "").strip()
    position_mode = str(signal.get("position_mode") or "").strip()
    if entry_action and position_mode:
        return f"{entry_action} / {position_mode}"
    if entry_action in {"관심 후보", "비선호 후보"}:
        return "구버전 관심목록(진입 기준 없음)" if section == "interest" else "구버전 비선호목록(진입 기준 없음)"
    if entry_action:
        return entry_action
    return "구버전 관심목록(진입 기준 없음)" if section == "interest" else "구버전 비선호목록(진입 기준 없음)"


def _is_unclassified_bucket(bucket: str) -> bool:
    return bucket.startswith("구버전 ")


def _reference_price(signal: dict[str, Any]) -> float | None:
    for key in ("start_entry_price", "close", "check_price", "entry_price"):
        value = _float_value(signal.get(key))
        if value is not None and value > 0:
            return value
    return None


def _future_rows(rows: Iterable[dict[str, Any]], signal_date: date, horizon: int) -> list[dict[str, Any]]:
    future = [row for row in rows if row.get("date") and row["date"] > signal_date]
    future.sort(key=lambda row: row["date"])
    return future[:horizon]


def _return_after(
    future: list[dict[str, Any]],
    reference_price: float | None,
    trading_days: int,
) -> float | None:
    if reference_price is None or reference_price <= 0 or len(future) < trading_days:
        return None
    close = _float_value(future[trading_days - 1].get("close"))
    if close is None:
        return None
    return (close / reference_price - 1) * 100


def _path_result(
    future: list[dict[str, Any]],
    reference_price: float | None,
    invalidation_price: float | None,
    first_target_price: float | None,
) -> tuple[str, float | None, str]:
    if reference_price is None:
        return "NO_PRICE", None, "기준가가 없어 성과를 계산하지 못했습니다."
    if not future:
        return "NO_DATA", None, "이후 가격 데이터가 부족합니다."

    valid_stop = invalidation_price is not None and invalidation_price < reference_price
    valid_target = first_target_price is not None and first_target_price > reference_price
    if not valid_stop and not valid_target:
        return "NO_RISK_LEVELS", None, "목표가와 무효화 가격이 기준가를 기준으로 유효하지 않습니다."

    risk = reference_price - invalidation_price if valid_stop else None
    reward_r = None
    if valid_stop and valid_target and risk is not None:
        reward = first_target_price - reference_price
        if risk > 0 and reward > 0:
            reward_r = reward / risk

    target_day = None
    stop_day = None
    for index, row in enumerate(future, start=1):
        high = _float_value(row.get("high")) or _float_value(row.get("close"))
        low = _float_value(row.get("low")) or _float_value(row.get("close"))
        if high is not None and valid_target and first_target_price is not None and high >= first_target_price and target_day is None:
            target_day = index
        if low is not None and valid_stop and invalidation_price is not None and low <= invalidation_price and stop_day is None:
            stop_day = index
        if target_day is not None or stop_day is not None:
            break

    if target_day is not None and stop_day is not None and target_day == stop_day:
        return "MIXED_SAME_DAY", 0.0, "같은 날 목표가와 무효화 가격을 모두 건드렸습니다."
    if target_day is not None and (stop_day is None or target_day < stop_day):
        return "TARGET_FIRST", reward_r, f"{target_day}거래일 안에 1차 목표가가 먼저 닿았습니다."
    if stop_day is not None and (target_day is None or stop_day < target_day):
        return "STOP_FIRST", -1.0 if reward_r is not None else None, f"{stop_day}거래일 안에 무효화 가격이 먼저 깨졌습니다."

    last_close = _float_value(future[-1].get("close"))
    if risk is not None and risk > 0 and last_close is not None:
        return "OPEN", (last_close - reference_price) / risk, "목표가/무효화 모두 닿지 않아 마지막 종가 기준으로 봅니다."
    return "OPEN", None, "목표가/무효화 모두 닿지 않았고 R 계산 기준이 부족합니다."


def _extremes(
    future: list[dict[str, Any]],
    reference_price: float | None,
) -> tuple[float | None, float | None]:
    if reference_price is None or reference_price <= 0 or not future:
        return None, None
    highs = [_float_value(row.get("high")) for row in future]
    lows = [_float_value(row.get("low")) for row in future]
    valid_highs = [value for value in highs if value is not None]
    valid_lows = [value for value in lows if value is not None]
    max_favorable = (max(valid_highs) / reference_price - 1) * 100 if valid_highs else None
    max_adverse = (min(valid_lows) / reference_price - 1) * 100 if valid_lows else None
    return max_favorable, max_adverse


def evaluate_signal(
    signal: dict[str, Any],
    section: str,
    price_rows: list[dict[str, Any]],
    spy_rows: list[dict[str, Any]] | None = None,
    horizon: int = 10,
) -> SignalEvaluation | None:
    signal_date = _parse_date(signal.get("date") or signal.get("target_date"))
    symbol = str(signal.get("symbol") or "").strip().upper()
    if signal_date is None or not symbol:
        return None

    reference_price = _reference_price(signal)
    invalidation_price = _float_value(signal.get("invalidation_price") or signal.get("stop_price"))
    first_target_price = _float_value(signal.get("first_target_price") or signal.get("entry_price"))
    all_future = _future_rows(price_rows, signal_date, len(price_rows))
    future = all_future[:horizon]
    spy_future = _future_rows(spy_rows or [], signal_date, len(spy_rows or []))
    returns = {day: _return_after(all_future, reference_price, day) for day in (1, 3, 5, 10, 20)}
    spy_return_5d = _return_after(
        spy_future,
        _reference_from_rows(spy_rows or [], signal_date),
        5,
    )
    relative_5d = None
    if returns[5] is not None and spy_return_5d is not None:
        relative_5d = returns[5] - spy_return_5d

    latest_price = _float_value(all_future[-1].get("close")) if all_future else reference_price
    current_return = None
    if reference_price is not None and reference_price > 0 and latest_price is not None:
        current_return = (latest_price / reference_price - 1) * 100
    spy_reference = _reference_from_rows(spy_rows or [], signal_date)
    latest_date = all_future[-1].get("date") if all_future else signal_date
    spy_latest = _reference_from_rows(
        [row for row in (spy_rows or []) if row.get("date") and row["date"] <= latest_date],
        latest_date,
    )
    spy_current_return = None
    if spy_reference is not None and spy_reference > 0 and spy_latest is not None:
        spy_current_return = (spy_latest / spy_reference - 1) * 100
    relative_current = None
    if current_return is not None and spy_current_return is not None:
        relative_current = current_return - spy_current_return

    max_favorable, max_adverse = _extremes(future, reference_price)
    outcome, r_result, note = _path_result(future, reference_price, invalidation_price, first_target_price)
    return SignalEvaluation(
        signal_date=signal_date,
        section=section,
        symbol=symbol,
        name=str(signal.get("name") or symbol),
        bucket=_signal_bucket(signal, section),
        reference_price=reference_price,
        invalidation_price=invalidation_price,
        first_target_price=first_target_price,
        returns=returns,
        spy_relative_5d=relative_5d,
        latest_price=latest_price,
        holding_days=len(all_future),
        current_return=current_return,
        spy_relative_current=relative_current,
        max_favorable_percent=max_favorable,
        max_adverse_percent=max_adverse,
        outcome=outcome,
        r_result=r_result,
        note=note,
    )


def _is_recommendation_candidate(signal: dict[str, Any]) -> bool:
    """Track actionable and conditional ideas, never explicit chase/avoid ideas."""

    grade = str(signal.get("candidate_grade") or "").strip()
    if grade:
        return grade in {"A급", "B급"}
    state = str(signal.get("recommendation_state") or "").strip()
    if state in {"TREND_WEAK", "EXCLUDED"}:
        return False
    action = str(signal.get("entry_action") or "").strip()
    if action in {"추격 금지", "제외"}:
        return False
    return bool(signal.get("symbol"))


def _history_payloads(
    previous_signals: dict[str, Any] | None,
    current_date: date,
) -> list[dict[str, Any]]:
    if not previous_signals:
        return []

    candidates: list[dict[str, Any]] = []
    embedded_history = previous_signals.get("recommendation_history")
    if not isinstance(embedded_history, list):
        embedded_history = previous_signals.get("history")
    if isinstance(embedded_history, list):
        candidates.extend(item for item in embedded_history if isinstance(item, dict))
    candidates.append(previous_signals)

    by_date: dict[str, dict[str, Any]] = {}
    for payload in candidates:
        payload_date = str(payload.get("target_date") or "")
        if payload_date and payload_date < current_date.isoformat():
            by_date[payload_date] = payload
    return [by_date[key] for key in sorted(by_date)]


def collect_recommendation_performance(
    current_date: date,
    current_signals: dict[str, Any],
    previous_signals: dict[str, Any] | None,
    *,
    horizon: int = 20,
    limit: int = 60,
    price_fetcher: PriceFetcher = fetch_yahoo_daily,
) -> RecommendationPerformance:
    """Build a bounded recommendation ledger from the history carried by latest.json."""

    current_interest = [
        signal
        for signal in (current_signals.get("interest") or [])
        if isinstance(signal, dict)
    ]
    current_candidates = [signal for signal in current_interest if _is_recommendation_candidate(signal)]
    current_candidates.sort(
        key=lambda signal: (
            0 if signal.get("candidate_grade") == "A급" else 1,
            -int(signal.get("score") or 0),
            str(signal.get("symbol") or ""),
        )
    )
    excluded_current_count = len(current_interest) - len(current_candidates)
    payloads = _history_payloads(previous_signals, current_date)

    historical_signals: list[dict[str, Any]] = []
    for payload in reversed(payloads):
        day_signals = [
            signal
            for signal in (payload.get("interest") or [])
            if isinstance(signal, dict) and _is_recommendation_candidate(signal)
        ]
        day_signals.sort(
            key=lambda signal: (
                0 if signal.get("candidate_grade") == "A급" else 1,
                -int(signal.get("score") or 0),
            )
        )
        historical_signals.extend(day_signals)

    warnings: list[str] = []
    price_cache: dict[str, list[dict[str, Any]]] = {}

    def rows_for(symbol: str) -> list[dict[str, Any]]:
        if symbol not in price_cache:
            try:
                price_cache[symbol] = [
                    row
                    for row in price_fetcher(symbol)
                    if row.get("date") and row["date"] <= current_date
                ]
            except Exception as exc:  # noqa: BLE001 - keep the rest of the ledger usable.
                warnings.append(f"{symbol}: 추천 성과용 가격 데이터를 가져오지 못했습니다. {exc}")
                price_cache[symbol] = []
        return price_cache[symbol]

    spy_rows = rows_for("SPY")
    evaluations: list[SignalEvaluation] = []
    seen: set[tuple[date, str]] = set()
    for signal in historical_signals:
        signal_date = _parse_date(signal.get("date") or signal.get("target_date"))
        symbol = str(signal.get("symbol") or "").strip().upper()
        if signal_date is None or not symbol or (signal_date, symbol) in seen:
            continue
        seen.add((signal_date, symbol))
        rows = rows_for(symbol)
        if not rows:
            continue
        evaluation = evaluate_signal(
            signal,
            "interest",
            rows,
            spy_rows=spy_rows,
            horizon=horizon,
        )
        if evaluation is not None:
            evaluations.append(evaluation)
        if len(evaluations) >= limit:
            break

    return RecommendationPerformance(
        current_candidates=current_candidates,
        evaluations=evaluations,
        history_days=len(payloads),
        excluded_current_count=excluded_current_count,
        warnings=warnings,
    )


def _reference_from_rows(rows: list[dict[str, Any]], signal_date: date) -> float | None:
    eligible = [row for row in rows if row.get("date") and row["date"] <= signal_date]
    if not eligible:
        return None
    eligible.sort(key=lambda row: row["date"])
    return _float_value(eligible[-1].get("close"))


def load_signal_snapshots(reports_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    signal_dir = reports_dir / "signals"
    snapshots = []
    for path in sorted(signal_dir.glob("*_signals.json")):
        if path.name == "latest.json":
            continue
        try:
            snapshots.append((path, json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError):
            continue
    return snapshots


def collect_signal_evaluations(
    reports_dir: Path,
    horizon: int = 10,
    limit: int = 24,
    price_fetcher: PriceFetcher = fetch_yahoo_daily,
) -> tuple[list[SignalEvaluation], list[str]]:
    warnings: list[str] = []
    snapshots = load_signal_snapshots(reports_dir)
    daily_groups: list[list[tuple[str, dict[str, Any]]]] = []
    for _, payload in sorted(snapshots, key=lambda item: str(item[1].get("target_date") or ""), reverse=True):
        daily_signals: list[tuple[str, dict[str, Any]]] = []
        for section in ("interest", "avoid"):
            for signal in payload.get(section) or []:
                daily_signals.append((section, signal))
        if daily_signals:
            daily_groups.append(daily_signals)

    signals: list[tuple[str, dict[str, Any]]] = []
    index = 0
    while True:
        added = False
        for daily_signals in daily_groups:
            if index < len(daily_signals):
                signals.append(daily_signals[index])
                added = True
        if not added:
            break
        index += 1

    price_cache: dict[str, list[dict[str, Any]]] = {}

    def rows_for(symbol: str) -> list[dict[str, Any]] | None:
        if symbol not in price_cache:
            try:
                price_cache[symbol] = price_fetcher(symbol)
            except Exception as exc:  # noqa: BLE001 - keep other samples usable.
                warnings.append(f"{symbol}: 가격 데이터를 가져오지 못했습니다. {exc}")
                price_cache[symbol] = []
        return price_cache[symbol] or None

    spy_rows = rows_for("SPY") or []
    evaluations: list[SignalEvaluation] = []
    seen: set[tuple[date, str, str]] = set()
    for section, signal in signals:
        symbol = str(signal.get("symbol") or "").strip().upper()
        signal_date = _parse_date(signal.get("date") or signal.get("target_date"))
        if not symbol or signal_date is None:
            continue
        key = (signal_date, symbol, section)
        if key in seen:
            continue
        seen.add(key)
        rows = rows_for(symbol)
        if not rows:
            continue
        evaluation = evaluate_signal(signal, section, rows, spy_rows=spy_rows, horizon=horizon)
        if evaluation is not None:
            evaluations.append(evaluation)
        if len(evaluations) >= limit:
            break
    return evaluations, warnings


def _avg(values: Iterable[float | None]) -> float | None:
    valid = [value for value in values if value is not None]
    if not valid:
        return None
    return mean(valid)


def _positive_ratio(values: Iterable[float | None]) -> float | None:
    valid = [value for value in values if value is not None]
    if not valid:
        return None
    return sum(1 for value in valid if value > 0) / len(valid) * 100


def _outcome_label(value: str) -> str:
    labels = {
        "TARGET_FIRST": "목표 먼저",
        "STOP_FIRST": "무효화 먼저",
        "MIXED_SAME_DAY": "동일일 혼재",
        "OPEN": "미결",
        "NO_PRICE": "기준가 없음",
        "NO_DATA": "시세 부족",
        "NO_RISK_LEVELS": "가격 기준 부족",
    }
    return labels.get(value, value)


def recommendation_performance_metrics(
    performance: RecommendationPerformance,
) -> dict[str, int | float | None]:
    measured = [
        item
        for item in performance.evaluations
        if item.holding_days > 0 and item.current_return is not None
    ]
    mature_5d = [item for item in performance.evaluations if item.returns.get(5) is not None]
    return {
        "tracked_count": len(performance.evaluations),
        "symbol_count": len({item.symbol for item in performance.evaluations}),
        "measured_count": len(measured),
        "mature_5d_count": len(mature_5d),
        "average_return": _avg(item.current_return for item in measured),
        "positive_ratio": _positive_ratio(item.current_return for item in measured),
        "average_relative": _avg(item.spy_relative_current for item in measured),
        "average_5d": _avg(item.returns.get(5) for item in mature_5d),
        "target_first": sum(1 for item in performance.evaluations if item.outcome == "TARGET_FIRST"),
        "stop_first": sum(1 for item in performance.evaluations if item.outcome == "STOP_FIRST"),
    }


def recommendation_performance_read(performance: RecommendationPerformance) -> str:
    metrics = recommendation_performance_metrics(performance)
    measured_count = int(metrics["measured_count"] or 0)
    if measured_count == 0:
        return "성과 기록을 막 시작했습니다. 다음 거래일부터 수익률과 SPY 대비 성과가 채워집니다."

    average_return = metrics["average_return"]
    average_relative = metrics["average_relative"]
    average_value = float(average_return or 0)
    direction = "플러스" if average_value > 0 else "마이너스" if average_value < 0 else "보합"
    if average_relative is not None:
        benchmark_value = float(average_relative)
        benchmark_read = "SPY를 웃돌았습니다" if benchmark_value > 0 else "SPY보다 약했습니다" if benchmark_value < 0 else "SPY와 같았습니다"
        return (
            f"평가 가능한 {measured_count}건의 단순 평균은 {direction}이고, "
            f"같은 기간 기준으로는 {benchmark_read}. 표본 수와 보유기간을 함께 보세요."
        )
    return f"평가 가능한 {measured_count}건의 단순 평균은 {direction}입니다. 표본 수와 보유기간을 함께 보세요."


def render_recommendation_performance(performance: RecommendationPerformance) -> str:
    metrics = recommendation_performance_metrics(performance)
    sample_label = "초기 표본" if int(metrics["tracked_count"] or 0) < 30 else "누적 표본"
    positive_text = "-" if metrics["positive_ratio"] is None else f"{metrics['positive_ratio']:.1f}%"
    lines = [
        "추천 후보 성과판",
        "범위: 보고서의 A급 진입 후보와 B급 조건부 관찰 후보 중 최근 최대 60건만 집계합니다. C급 추격 금지·제외 후보는 성과에서 뺍니다.",
        (
            f"누적: {performance.history_days}거래일 / {metrics['tracked_count']}건 / "
            f"{metrics['symbol_count']}종목 / {sample_label}"
        ),
        f"해석: {recommendation_performance_read(performance)}",
        (
            f"요약: 평균 수익률 {_percent(metrics['average_return'])} / "
            f"플러스 비율 {positive_text} / "
            f"SPY 대비 {_percent(metrics['average_relative'])} / "
            f"5거래일 성숙 표본 {metrics['mature_5d_count']}건"
        ),
        "계산: 시작 진입가가 있으면 그 가격, 없으면 추천일 종가를 기준가로 사용합니다. 수익률은 배당·수수료·실제 체결을 반영하지 않은 단순 비교입니다.",
    ]

    if performance.current_candidates:
        lines.append("현재 추천/관찰 후보")
        for signal in performance.current_candidates[:8]:
            reference = _reference_price(signal)
            lines.append(
                f"- {signal.get('name') or signal.get('symbol')}({signal.get('symbol')}) / "
                f"{signal.get('candidate_grade') or '등급 미정'} / "
                f"{signal.get('entry_action') or signal.get('recommendation_label') or '조건 확인'} / "
                f"기준가 {_money(reference)}"
            )
        if len(performance.current_candidates) > 8:
            lines.append(f"- 그 외 {len(performance.current_candidates) - 8}개 후보")
    else:
        lines.append("현재 추천/관찰 후보\nA·B급 조건을 통과한 후보가 없습니다.")

    if performance.evaluations:
        lines.extend(
            [
                "추천 이력과 수익률",
                "|추천일|종목|당시 등급|기준가|최신가|1D|5D|현재|SPY 대비|경과|결과|",
                "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
        for item in performance.evaluations:
            lines.append(
                "|"
                + "|".join(
                    [
                        item.signal_date.isoformat(),
                        f"{item.name}({item.symbol})",
                        item.bucket,
                        _money(item.reference_price),
                        _money(item.latest_price),
                        _percent(item.returns.get(1)),
                        _percent(item.returns.get(5)),
                        _percent(item.current_return),
                        _percent(item.spy_relative_current),
                        f"{item.holding_days}일",
                        _outcome_label(item.outcome),
                    ]
                )
                + "|"
            )
    else:
        lines.append("추천 이력과 수익률\n아직 평가할 과거 추천이 없습니다.")

    if performance.warnings:
        lines.append(f"데이터 주의: 가격 확인 실패 {len(performance.warnings)}건은 집계에서 제외했습니다.")
    return "\n".join(lines)


def render_selection_review(evaluations: list[SignalEvaluation], warnings: list[str]) -> str:
    lines = [
        "# 주식 선정 기준 표본 검증",
        "",
        "이 검증은 과거 보고서에 남은 후보가 이후 며칠 동안 실제로 유효했는지 확인하는 1차 표본 점검입니다.",
        "기준가는 보고서의 시작 진입가가 있으면 그 가격을 쓰고, 없으면 당시 종가를 씁니다.",
        "`구버전 관심목록`은 사용자가 넣은 관심종목 또는 옛 보고서 후보군이라 추천 로직 성과로 해석하지 않습니다.",
        "",
    ]
    if warnings:
        lines.append("## 데이터 주의")
        for warning in warnings[:8]:
            lines.append(f"- {warning}")
        if len(warnings) > 8:
            lines.append(f"- 그 외 {len(warnings) - 8}건")
        lines.append("")
    if not evaluations:
        lines.append("검증 가능한 표본이 아직 없습니다.")
        return "\n".join(lines)

    classified = [item for item in evaluations if not _is_unclassified_bucket(item.bucket)]
    summary_base = classified or evaluations
    target_first = sum(1 for item in summary_base if item.outcome == "TARGET_FIRST")
    stop_first = sum(1 for item in summary_base if item.outcome == "STOP_FIRST")
    avg_5d = _avg(item.returns.get(5) for item in summary_base)
    avg_10d = _avg(item.returns.get(10) for item in summary_base)
    avg_r = _avg(item.r_result for item in summary_base)
    positive_5d = _positive_ratio(item.returns.get(5) for item in summary_base)
    lines.extend(
        [
            "## 전체 요약",
            "",
            f"- 전체 표본 수: {len(evaluations)}개",
            f"- 진입/관망 판정이 있는 표본 수: {len(classified)}개",
            f"- 구버전 관심/비선호 목록 표본 수: {len(evaluations) - len(classified)}개",
            f"- 1차 목표가 먼저 닿은 표본: {target_first}개",
            f"- 무효화 가격이 먼저 깨진 표본: {stop_first}개",
            f"- 평균 5거래일 수익률: {_percent(avg_5d)}",
            f"- 평균 10거래일 수익률: {_percent(avg_10d)}",
            f"- 5거래일 플러스 비율: {'-' if positive_5d is None else f'{positive_5d:.1f}%'}",
            f"- 평균 R 결과: {_r_text(avg_r)}",
            "",
            "## 판정별 요약",
            "",
            "|판정|표본|목표 먼저|무효화 먼저|평균 5D|평균 10D|평균 R|5D 플러스|",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    buckets = sorted({item.bucket for item in evaluations})
    for bucket in buckets:
        group = [item for item in evaluations if item.bucket == bucket]
        group_positive_5d = _positive_ratio(item.returns.get(5) for item in group)
        positive_text = "-" if group_positive_5d is None else f"{group_positive_5d:.1f}%"
        lines.append(
            "|"
            + "|".join(
                [
                    bucket,
                    str(len(group)),
                    str(sum(1 for item in group if item.outcome == "TARGET_FIRST")),
                    str(sum(1 for item in group if item.outcome == "STOP_FIRST")),
                    _percent(_avg(item.returns.get(5) for item in group)),
                    _percent(_avg(item.returns.get(10) for item in group)),
                    _r_text(_avg(item.r_result for item in group)),
                    positive_text,
                ]
            )
            + "|"
        )

    lines.extend(
        [
            "",
            "## 표본 상세",
            "",
            "|날짜|판정|종목|기준가|1D|3D|5D|10D|SPY 대비 5D|최대 유리|최대 불리|결과|R|",
            "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|",
        ]
    )
    for item in evaluations:
        lines.append(
            "|"
            + "|".join(
                [
                    item.signal_date.isoformat(),
                    item.bucket,
                    item.symbol,
                    _money(item.reference_price),
                    _percent(item.returns.get(1)),
                    _percent(item.returns.get(3)),
                    _percent(item.returns.get(5)),
                    _percent(item.returns.get(10)),
                    _percent(item.spy_relative_5d),
                    _percent(item.max_favorable_percent),
                    _percent(item.max_adverse_percent),
                    _outcome_label(item.outcome),
                    _r_text(item.r_result),
                ]
            )
            + "|"
        )

    lines.extend(
        [
            "",
            "## 읽는 법",
            "",
            "- `목표 먼저`가 많고 평균 R이 플러스면 해당 기준은 유지할 가치가 큽니다.",
            "- `무효화 먼저`가 많으면 그 기준은 진입 기준을 더 엄격하게 바꿔야 합니다.",
            "- 5D 수익률이 플러스여도 SPY 대비가 낮으면 종목 선정 효과가 약한 것입니다.",
            "- `구버전 관심목록`은 사용자가 넣은 후보군 성격이 섞여 있어 추천 기준 검증에서 분리해서 봅니다.",
            "- 표본이 30개 미만이면 결론보다 방향성 확인용으로만 봐야 합니다.",
        ]
    )
    return "\n".join(lines)


def build_selection_review(
    reports_dir: Path,
    horizon: int = 10,
    limit: int = 24,
    price_fetcher: PriceFetcher = fetch_yahoo_daily,
) -> tuple[str, list[str]]:
    evaluations, warnings = collect_signal_evaluations(
        reports_dir=reports_dir,
        horizon=horizon,
        limit=limit,
        price_fetcher=price_fetcher,
    )
    return render_selection_review(evaluations, warnings), warnings
