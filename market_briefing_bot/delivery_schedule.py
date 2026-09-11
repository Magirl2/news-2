from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, time as clock_time, timezone

from .timezones import get_timezone


def parse_clock(value: str) -> clock_time:
    """Parse a 24-hour HH:MM clock value."""
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except ValueError as exc:
        raise ValueError("시각은 HH:MM 형식이어야 합니다. 예: 09:00") from exc
    return parsed.time()


def seconds_until_local_time(
    target: str,
    timezone_name: str,
    *,
    now: datetime | None = None,
) -> float:
    """Return seconds until today's target time, or zero when it has passed."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now에는 시간대 정보가 있어야 합니다.")

    local_now = current.astimezone(get_timezone(timezone_name))
    target_clock = parse_clock(target)
    target_at = local_now.replace(
        hour=target_clock.hour,
        minute=target_clock.minute,
        second=0,
        microsecond=0,
    )
    return max(0.0, (target_at - local_now).total_seconds())


def wait_until_local_time(
    target: str,
    timezone_name: str,
    *,
    max_wait_seconds: int,
    now: datetime | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> float:
    """Wait for today's target without accidentally holding a runner for hours."""
    delay = seconds_until_local_time(target, timezone_name, now=now)
    if delay > max_wait_seconds:
        raise RuntimeError(
            f"{timezone_name} {target}까지 {delay / 60:.1f}분 남아 "
            f"허용 대기 {max_wait_seconds / 60:.1f}분을 초과했습니다."
        )
    if delay:
        sleep_fn(delay)
    return delay
