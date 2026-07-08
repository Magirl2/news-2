from __future__ import annotations

import csv
import io
import urllib.parse
import urllib.request
from datetime import date, timedelta


ALPHA_VANTAGE_EARNINGS_URL = "https://www.alphavantage.co/query"


def _download_text(url: str, timeout: int = 20) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; market-briefing-bot/0.1)"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _earnings_rows(symbol: str, api_key: str) -> list[dict[str, str]]:
    query = urllib.parse.urlencode(
        {
            "function": "EARNINGS_CALENDAR",
            "symbol": symbol,
            "horizon": "3month",
            "apikey": api_key,
        }
    )
    text = _download_text(f"{ALPHA_VANTAGE_EARNINGS_URL}?{query}")
    if "Error Message" in text or "Invalid API call" in text:
        raise RuntimeError("Alpha Vantage 응답이 올바르지 않습니다.")
    return list(csv.DictReader(io.StringIO(text)))


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value.strip())
    except (ValueError, AttributeError):
        return None


def _risk_text(days_left: int) -> str:
    if days_left < 0:
        return "이미 발표가 지난 일정입니다. 발표 후 가격 반응을 확인합니다."
    if days_left <= 3:
        return "실적 임박입니다. 신규 진입은 평소보다 보수적으로 봅니다."
    if days_left <= 10:
        return "실적 전 변동성이 커질 수 있어 타점과 손절 기준을 좁게 봅니다."
    return "실적 전 기대감이 먼저 반영되는지 가격 반응을 확인합니다."


def build_earnings_calendar(
    symbols: list[str],
    api_key: str,
    target_date: date,
    days: int = 30,
) -> tuple[str, list[str]]:
    clean_symbols = [symbol.strip().upper() for symbol in symbols if symbol.strip()]
    if not clean_symbols:
        return (
            "실적 발표 캘린더\n"
            "- WATCHLIST_SYMBOLS가 비어 있어 관심종목 실적 일정을 확인하지 않았습니다.\n"
            "- 관심종목을 넣으면 실적 전 변동성 위험을 같이 표시합니다.",
            [],
        )

    if not api_key:
        return (
            "실적 발표 캘린더\n"
            "- ALPHA_VANTAGE_API_KEY가 없어 실적 발표 일정을 자동으로 가져오지 못했습니다.\n"
            "- Alpha Vantage에서 무료 API 키를 받은 뒤 GitHub Secrets에 ALPHA_VANTAGE_API_KEY로 넣어 주세요.\n"
            "- 키를 넣으면 WATCHLIST_SYMBOLS 종목의 다음 실적 발표일을 자동 표시합니다.",
            [],
        )

    end_date = target_date + timedelta(days=days)
    warnings: list[str] = []
    lines = [f"실적 발표 캘린더\n기간: {target_date.isoformat()} ~ {end_date.isoformat()}"]
    found = False

    for symbol in dict.fromkeys(clean_symbols):
        try:
            rows = _earnings_rows(symbol, api_key)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{symbol} 실적 일정 조회 실패: {exc}")
            continue

        upcoming = []
        for row in rows:
            report_date = _parse_date(row.get("reportDate", ""))
            if not report_date or report_date < target_date or report_date > end_date:
                continue
            upcoming.append((report_date, row))

        for report_date, row in sorted(upcoming, key=lambda item: item[0])[:2]:
            found = True
            days_left = (report_date - target_date).days
            estimate = (row.get("estimate") or "").strip()
            estimate_text = f" / 예상 EPS {estimate}" if estimate else ""
            lines.append(
                f"- {symbol}: {report_date.isoformat()} D-{days_left}{estimate_text} / {_risk_text(days_left)}"
            )

    if not found:
        lines.append("- 관심종목의 30일 이내 실적 발표 일정이 확인되지 않았습니다.")
    return "\n".join(lines), warnings
