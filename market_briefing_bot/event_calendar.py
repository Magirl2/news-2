from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import date, timedelta


FRED_RELEASE_DATES_URL = "https://api.stlouisfed.org/fred/releases/dates"


FRED_RELEASES = {
    "고용보고서": "50",
    "CPI": "10",
    "PCE 물가": "54",
    "FOMC": "101",
    "GDP": "53",
}


def _download_json(url: str, timeout: int = 20) -> dict:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "market-briefing-bot/0.1"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def _event_impact(name: str) -> str:
    if name == "FOMC":
        return "금리와 성장주 밸류에이션에 직접 영향"
    if name in {"CPI", "PCE 물가"}:
        return "물가 부담과 금리 기대를 바꿀 수 있음"
    if name == "고용보고서":
        return "경기 둔화/과열 판단과 금리 기대에 영향"
    if name == "GDP":
        return "경기민감주와 경기 침체 우려에 영향"
    return "시장 변동성 확대 가능"


def _fred_release_dates(api_key: str, start: date, end: date) -> list[dict]:
    query = urllib.parse.urlencode(
        {
            "api_key": api_key,
            "file_type": "json",
            "realtime_start": start.isoformat(),
            "realtime_end": end.isoformat(),
            "include_release_dates_with_no_data": "true",
        }
    )
    payload = _download_json(f"{FRED_RELEASE_DATES_URL}?{query}")
    return payload.get("release_dates", [])


def build_event_calendar(api_key: str, target_date: date, days: int = 7) -> tuple[str, list[str]]:
    warnings: list[str] = []
    start = target_date + timedelta(days=1)
    end = target_date + timedelta(days=days)

    if not api_key:
        return (
            "이번 주 이벤트 캘린더\n"
            "- FRED_API_KEY가 없어 자동 경제지표 일정을 가져오지 못했습니다.\n"
            "- 체크 대상: 고용보고서, CPI, PCE 물가, FOMC, GDP\n"
            "- GitHub Secrets에 FRED_API_KEY를 넣으면 발표일을 자동 표시합니다.",
            warnings,
        )

    try:
        dates = _fred_release_dates(api_key, start, end)
    except Exception as exc:  # noqa: BLE001
        return (
            "이번 주 이벤트 캘린더\n"
            "- 경제지표 일정을 가져오지 못했습니다.\n"
            "- 체크 대상: 고용보고서, CPI, PCE 물가, FOMC, GDP",
            [f"FRED 이벤트 캘린더 조회 실패: {exc}"],
        )

    release_ids = {int(value): name for name, value in FRED_RELEASES.items()}
    lines = [f"이번 주 이벤트 캘린더\n기간: {start.isoformat()} ~ {end.isoformat()}"]
    found = False
    for item in dates:
        release_id = item.get("release_id")
        try:
            release_id_int = int(release_id)
        except (TypeError, ValueError):
            continue
        if release_id_int not in release_ids:
            continue
        name = release_ids[release_id_int]
        event_date = item.get("date", "")
        lines.append(f"- {event_date} {name}: {_event_impact(name)}")
        found = True

    if not found:
        lines.append("- 이번 7일 안에 주요 체크 이벤트가 확인되지 않았습니다.")
    return "\n".join(lines), warnings
