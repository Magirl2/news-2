from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Alert:
    id: int | None
    telegram_user_id: str
    chat_id: str
    symbol: str
    condition_type: str
    target_price: float
    note: str = ""
    created_at: str = ""
    triggered_at: str | None = None
    active: bool = True
    last_checked_price: float | None = None


def should_trigger(alert: Alert, current_price: float, *, touch_tolerance: float = 0.0015) -> bool:
    if not alert.active:
        return False
    if alert.condition_type in {"breakout", "add_entry", "confirm_entry"}:
        return current_price >= alert.target_price
    if alert.condition_type in {"breakdown", "invalidation"}:
        return current_price <= alert.target_price
    if alert.condition_type == "touch":
        return abs(current_price - alert.target_price) <= alert.target_price * touch_tolerance
    return False

