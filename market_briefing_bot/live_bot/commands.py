from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedCommand:
    kind: str
    symbol: str | None = None
    condition_type: str | None = None
    target_price: float | None = None
    delete_target: str | None = None
    raw_text: str = ""


SYMBOL_RE = re.compile(r"\b[A-Za-z]{1,6}\b")
PRICE_RE = re.compile(r"(?<!\w)(\d+(?:\.\d+)?)(?!\w)")


def _first_symbol(text: str) -> str | None:
    for match in SYMBOL_RE.finditer(text):
        value = match.group(0).upper()
        if value not in {"ETF", "CEO", "CPI", "FOMC", "SEC"}:
            return value
    return None


def _first_price(text: str) -> float | None:
    match = PRICE_RE.search(text)
    return float(match.group(1)) if match else None


def parse_command(text: str) -> ParsedCommand:
    raw = " ".join(text.strip().split())
    lowered = raw.lower()
    if not raw or lowered in {"/start", "/help", "help", "도움말"}:
        return ParsedCommand(kind="help", raw_text=raw)

    if raw == "알림 목록":
        return ParsedCommand(kind="list_alerts", raw_text=raw)

    if raw == "알림 전체 삭제":
        return ParsedCommand(kind="delete_all_alerts", raw_text=raw)

    if raw.startswith("알림 삭제"):
        delete_target = raw.replace("알림 삭제", "", 1).strip()
        return ParsedCommand(kind="delete_alert", delete_target=delete_target or None, raw_text=raw)

    symbol = _first_symbol(raw)
    if not symbol:
        return ParsedCommand(kind="unknown", raw_text=raw)

    if "알림" in raw:
        condition_type = None
        if "돌파" in raw or " 위" in raw:
            condition_type = "breakout"
        elif "이탈" in raw or " 아래" in raw:
            condition_type = "breakdown"
        elif "도달" in raw:
            condition_type = "touch"
        elif "추가진입" in raw or "추가 진입" in raw:
            condition_type = "add_entry"
        elif "확인진입" in raw or "확인 진입" in raw:
            condition_type = "confirm_entry"
        elif "무효화" in raw:
            condition_type = "invalidation"
        if condition_type:
            return ParsedCommand(
                kind="add_alert",
                symbol=symbol,
                condition_type=condition_type,
                target_price=_first_price(raw),
                raw_text=raw,
            )

    return ParsedCommand(kind="analyze", symbol=symbol, raw_text=raw)


def condition_label(condition_type: str) -> str:
    labels = {
        "breakout": "돌파",
        "breakdown": "이탈",
        "touch": "도달",
        "add_entry": "추가진입",
        "confirm_entry": "확인진입",
        "invalidation": "무효화",
    }
    return labels.get(condition_type, condition_type)

