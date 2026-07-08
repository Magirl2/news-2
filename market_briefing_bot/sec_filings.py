from __future__ import annotations

import json
import urllib.request
from datetime import date, timedelta
from typing import Any


COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
IMPORTANT_FORMS = {"8-K", "10-Q", "10-K", "6-K", "20-F"}


def _download_json(url: str, user_agent: str, timeout: int = 20) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def _ticker_map(user_agent: str) -> dict[str, dict[str, Any]]:
    payload = _download_json(COMPANY_TICKERS_URL, user_agent)
    mapping: dict[str, dict[str, Any]] = {}
    for item in payload.values():
        ticker = str(item.get("ticker", "")).upper()
        cik = item.get("cik_str")
        title = item.get("title", ticker)
        if ticker and cik:
            mapping[ticker] = {
                "cik": str(cik).zfill(10),
                "title": title,
            }
    return mapping


def _filing_url(cik: str, accession: str, primary_doc: str) -> str:
    clean_accession = accession.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{clean_accession}/{primary_doc}"


def _recent_filings(symbol: str, company: dict[str, Any], user_agent: str, since: date) -> list[str]:
    cik = company["cik"]
    payload = _download_json(SUBMISSIONS_URL.format(cik=cik), user_agent)
    recent = payload.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    filing_dates = recent.get("filingDate", [])
    accession_numbers = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])
    descriptions = recent.get("primaryDocDescription", [])

    lines: list[str] = []
    for form, filing_date, accession, primary_doc, description in zip(
        forms,
        filing_dates,
        accession_numbers,
        primary_docs,
        descriptions,
    ):
        if form not in IMPORTANT_FORMS:
            continue
        try:
            parsed_date = date.fromisoformat(filing_date)
        except ValueError:
            continue
        if parsed_date < since:
            continue
        desc = description or form
        link = _filing_url(cik, accession, primary_doc)
        lines.append(f"- {symbol} {filing_date} {form}: {desc} / {link}")
    return lines


def build_sec_filing_alert(symbols: list[str], target_date: date, user_agent: str, lookback_days: int = 10) -> tuple[str, list[str]]:
    warnings: list[str] = []
    clean_symbols = [symbol.strip().upper() for symbol in symbols if symbol.strip()]
    if not clean_symbols:
        return "", warnings

    since = target_date - timedelta(days=lookback_days)
    try:
        mapping = _ticker_map(user_agent)
    except Exception as exc:  # noqa: BLE001
        return (
            "관심종목 SEC 공시\n"
            "- SEC 티커 목록을 가져오지 못해 공시 확인을 건너뜁니다.",
            [f"SEC 티커 목록 조회 실패: {exc}"],
        )

    lines = [f"관심종목 SEC 공시\n기간: {since.isoformat()} ~ {target_date.isoformat()}"]
    found = False
    for symbol in dict.fromkeys(clean_symbols):
        company = mapping.get(symbol)
        if not company:
            warnings.append(f"{symbol} SEC CIK를 찾지 못했습니다.")
            continue
        try:
            filings = _recent_filings(symbol, company, user_agent, since)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{symbol} SEC 공시 조회 실패: {exc}")
            continue
        if filings:
            lines.extend(filings)
            found = True

    if not found:
        lines.append("- 최근 주요 공시(8-K, 10-Q, 10-K 등)가 확인되지 않았습니다.")
    return "\n".join(lines), warnings
