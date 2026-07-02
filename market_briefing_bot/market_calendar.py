from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from .timezones import get_timezone


@dataclass(frozen=True)
class MarketSession:
    trading_day: date
    is_early_close: bool
    close_time_et: time
    note: str


def _nth_weekday(year: int, month: int, weekday: int, nth: int) -> date:
    current = date(year, month, 1)
    offset = (weekday - current.weekday()) % 7
    return current + timedelta(days=offset + 7 * (nth - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        current = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        current = date(year, month + 1, 1) - timedelta(days=1)
    offset = (current.weekday() - weekday) % 7
    return current - timedelta(days=offset)


def _easter_date(year: int) -> date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _observed_fixed(year: int, month: int, day: int) -> date:
    holiday = date(year, month, day)
    if holiday.weekday() == 5:
        return holiday - timedelta(days=1)
    if holiday.weekday() == 6:
        return holiday + timedelta(days=1)
    return holiday


def holiday_reason(day: date) -> str | None:
    reasons = {
        _observed_fixed(day.year, 1, 1): "New Year's Day",
        _nth_weekday(day.year, 1, 0, 3): "Martin Luther King Jr. Day",
        _nth_weekday(day.year, 2, 0, 3): "Washington's Birthday",
        _easter_date(day.year) - timedelta(days=2): "Good Friday",
        _last_weekday(day.year, 5, 0): "Memorial Day",
        _observed_fixed(day.year, 6, 19): "Juneteenth National Independence Day",
        _observed_fixed(day.year, 7, 4): "Independence Day",
        _nth_weekday(day.year, 9, 0, 1): "Labor Day",
        _nth_weekday(day.year, 11, 3, 4): "Thanksgiving Day",
        _observed_fixed(day.year, 12, 25): "Christmas Day",
    }

    # If next year's New Year's Day is observed on Dec. 31 this year.
    reasons[_observed_fixed(day.year + 1, 1, 1)] = "New Year's Day"
    return reasons.get(day)


def is_trading_day(day: date) -> bool:
    return day.weekday() < 5 and holiday_reason(day) is None


def early_close_reason(day: date) -> str | None:
    thanksgiving_friday = _nth_weekday(day.year, 11, 3, 4) + timedelta(days=1)
    if day == thanksgiving_friday and is_trading_day(day):
        return "Day after Thanksgiving"

    if day.month == 12 and day.day == 24 and is_trading_day(day):
        return "Christmas Eve"

    # NYSE commonly closes early before Independence Day when the market is open.
    if day.month == 7 and day.day == 3 and is_trading_day(day):
        return "Day before Independence Day"

    # In 2026 Independence Day is observed on Friday, July 3, so July 2 is the early close.
    if day == date(2026, 7, 2):
        return "Day before Independence Day observance"

    return None


def get_session(day: date) -> MarketSession | None:
    if not is_trading_day(day):
        return None
    reason = early_close_reason(day)
    return MarketSession(
        trading_day=day,
        is_early_close=reason is not None,
        close_time_et=time(13, 0) if reason else time(16, 0),
        note=reason or "Regular session",
    )


def previous_trading_day(day: date) -> date:
    current = day - timedelta(days=1)
    while not is_trading_day(current):
        current -= timedelta(days=1)
    return current


def last_completed_trading_day(
    now: datetime | None = None, market_timezone: str = "America/New_York"
) -> date:
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    market_tz = get_timezone(market_timezone)
    market_now = now.astimezone(market_tz)
    today = market_now.date()
    session = get_session(today)

    if session:
        close_at = datetime.combine(today, session.close_time_et, market_tz)
        if market_now >= close_at + timedelta(minutes=15):
            return today

    return previous_trading_day(today)


def current_market_note(
    now: datetime | None = None, market_timezone: str = "America/New_York"
) -> str:
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    market_now = now.astimezone(get_timezone(market_timezone))
    today = market_now.date()
    session = get_session(today)
    if session and session.is_early_close:
        return f"미국장은 조기 폐장일({session.note})입니다."
    if session:
        return "미국장은 정규 거래일입니다."

    reason = holiday_reason(today)
    if reason:
        return f"오늘 미국장은 휴장일입니다({reason})."
    return "오늘 미국장은 주말로 휴장입니다."
