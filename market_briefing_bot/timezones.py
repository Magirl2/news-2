from __future__ import annotations

from datetime import datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ZERO = timedelta(0)
HOUR = timedelta(hours=1)


def _nth_weekday_datetime(year: int, month: int, weekday: int, nth: int) -> datetime:
    current = datetime(year, month, 1, 2, 0)
    offset = (weekday - current.weekday()) % 7
    return current + timedelta(days=offset + 7 * (nth - 1))


class FixedOffset(tzinfo):
    def __init__(self, hours: int, name: str):
        self._offset = timedelta(hours=hours)
        self._name = name

    def utcoffset(self, dt: datetime | None) -> timedelta:
        return self._offset

    def dst(self, dt: datetime | None) -> timedelta:
        return ZERO

    def tzname(self, dt: datetime | None) -> str:
        return self._name


class EasternTime(tzinfo):
    """Small US Eastern timezone fallback for Windows installs without tzdata."""

    def _dst_start_local(self, year: int) -> datetime:
        return _nth_weekday_datetime(year, 3, 6, 2)

    def _dst_end_local(self, year: int) -> datetime:
        return _nth_weekday_datetime(year, 11, 6, 1)

    def _dst_start_utc(self, year: int) -> datetime:
        return self._dst_start_local(year) + timedelta(hours=5)

    def _dst_end_utc(self, year: int) -> datetime:
        return self._dst_end_local(year) + timedelta(hours=4)

    def _is_dst_local(self, naive_local: datetime) -> bool:
        return self._dst_start_local(naive_local.year) <= naive_local < self._dst_end_local(
            naive_local.year
        )

    def _is_dst_utc(self, naive_utc: datetime) -> bool:
        return self._dst_start_utc(naive_utc.year) <= naive_utc < self._dst_end_utc(
            naive_utc.year
        )

    def utcoffset(self, dt: datetime | None) -> timedelta:
        if dt is None:
            return timedelta(hours=-5)
        naive = dt.replace(tzinfo=None)
        return timedelta(hours=-4 if self._is_dst_local(naive) else -5)

    def dst(self, dt: datetime | None) -> timedelta:
        if dt is None:
            return ZERO
        naive = dt.replace(tzinfo=None)
        return HOUR if self._is_dst_local(naive) else ZERO

    def tzname(self, dt: datetime | None) -> str:
        return "EDT" if self.dst(dt) else "EST"

    def fromutc(self, dt: datetime) -> datetime:
        naive_utc = dt.replace(tzinfo=None)
        offset = timedelta(hours=-4 if self._is_dst_utc(naive_utc) else -5)
        return (naive_utc + offset).replace(tzinfo=self)


def get_timezone(name: str) -> tzinfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name in {"America/New_York", "US/Eastern"}:
            return EasternTime()
        if name in {"Asia/Seoul", "KST"}:
            return FixedOffset(9, "KST")
        return FixedOffset(0, "UTC")

