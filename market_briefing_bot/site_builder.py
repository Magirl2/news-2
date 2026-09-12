from __future__ import annotations

import argparse
import html
import json
import re
import shutil
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path


REPORT_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})_briefing\.html$")
REPORT_BROWSER = re.compile(
    r"\s*<!-- REPORT_BROWSER_START -->.*?<!-- REPORT_BROWSER_END -->\s*",
    re.DOTALL,
)
WEEKDAYS_KO = ("월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일")


@dataclass(frozen=True)
class ReportEntry:
    report_date: date
    filename: str
    title: str
    market_line: str
    one_line: str

    @property
    def date_label(self) -> str:
        return (
            f"{self.report_date.year}년 {self.report_date.month}월 {self.report_date.day}일"
            f" · {WEEKDAYS_KO[self.report_date.weekday()]}"
        )


def _plain_fragment(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", value)
    return " ".join(html.unescape(without_tags).split())


def _first_fragment(source: str, pattern: str, default: str = "") -> str:
    match = re.search(pattern, source, flags=re.IGNORECASE | re.DOTALL)
    return _plain_fragment(match.group(1)) if match else default


def discover_reports(reports_dir: Path) -> list[ReportEntry]:
    entries: list[ReportEntry] = []
    for path in reports_dir.glob("*_briefing.html"):
        match = REPORT_NAME.match(path.name)
        if not match:
            continue
        try:
            report_date = datetime.strptime(match.group(1), "%Y-%m-%d").date()
            source = path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            continue
        entries.append(
            ReportEntry(
                report_date=report_date,
                filename=path.name,
                title=_first_fragment(source, r"<title>(.*?)</title>", f"미국장 마감 {match.group(1)}"),
                market_line=_first_fragment(source, r'<div[^>]*class="[^"]*market-line[^"]*"[^>]*>(.*?)</div>'),
                one_line=_first_fragment(source, r'<p[^>]*class="[^"]*one-line[^"]*"[^>]*>(.*?)</p>'),
            )
        )
    return sorted(entries, key=lambda item: item.report_date, reverse=True)


def _nav_link(entry: ReportEntry | None, label: str, css_class: str) -> str:
    if entry is None:
        return f'<span class="report-browser__button is-disabled" aria-disabled="true">{label}</span>'
    return (
        f'<a class="report-browser__button {css_class}" href="{html.escape(entry.filename)}">'
        f"{label}</a>"
    )


def report_navigation(entries: list[ReportEntry], current: ReportEntry) -> str:
    index = entries.index(current)
    newer = entries[index - 1] if index > 0 else None
    older = entries[index + 1] if index + 1 < len(entries) else None
    options = "".join(
        f'<option value="{html.escape(entry.filename)}"'
        f"{' selected' if entry == current else ''}>{html.escape(entry.date_label)}</option>"
        for entry in entries
    )
    latest_link = entries[0].filename if entries else current.filename
    return f"""
<!-- REPORT_BROWSER_START -->
<a class="skip-link" href="#quick-summary">본문으로 건너뛰기</a>
<nav class="report-browser" id="page-top" aria-label="일자별 리포트 탐색">
  <a class="report-browser__brand" href="../index.html">
    <span>US Market Briefing</span>
    <strong>리포트 모아보기</strong>
  </a>
  <div class="report-browser__controls">
    {_nav_link(older, '← 이전', 'is-older')}
    <label class="report-browser__select">
      <span>리포트 날짜</span>
      <select aria-label="확인할 리포트 날짜" onchange="location.href=this.value">
        {options}
      </select>
    </label>
    {_nav_link(newer, '다음 →', 'is-newer')}
  </div>
  <a class="report-browser__latest" href="{html.escape(latest_link)}">최신 리포트</a>
</nav>
<!-- REPORT_BROWSER_END -->
""".strip()


def inject_report_navigation(source: str, navigation: str) -> str:
    cleaned = REPORT_BROWSER.sub("\n", source)
    stylesheet = '<link rel="stylesheet" href="../assets/report.css">'
    if "../assets/report.css" not in cleaned and "</head>" in cleaned:
        cleaned = cleaned.replace("</head>", f"  {stylesheet}\n</head>", 1)
    if "<body>" not in cleaned:
        return cleaned
    cleaned = cleaned.replace("<body>", f"<body>\n  {navigation}\n", 1)
    if "class=\"back-to-top\"" not in cleaned and "</main>" in cleaned:
        cleaned = cleaned.replace(
            "</main>",
            '  <a class="back-to-top" href="#page-top" aria-label="페이지 맨 위로">↑ 맨 위</a>\n  </main>',
            1,
        )
    return cleaned


def _report_card(entry: ReportEntry, *, latest: bool = False) -> str:
    badge = '<span class="archive-card__badge">최신</span>' if latest else ""
    market = entry.market_line or "시장 지표는 리포트에서 확인하세요."
    summary = entry.one_line or "당일 시장 흐름과 투자 판단을 정리한 리포트입니다."
    return f"""
    <a class="archive-card{' is-latest' if latest else ''}" href="reports/{html.escape(entry.filename)}">
      <div class="archive-card__top">
        <time datetime="{entry.report_date.isoformat()}">{html.escape(entry.date_label)}</time>
        {badge}
      </div>
      <strong>{html.escape(market)}</strong>
      <p>{html.escape(summary)}</p>
      <span class="archive-card__action">리포트 읽기 <b aria-hidden="true">→</b></span>
    </a>
    """


def archive_index_html(entries: list[ReportEntry]) -> str:
    if not entries:
        content = """
        <section class="archive-empty">
          <strong>아직 발행된 리포트가 없습니다.</strong>
          <p>첫 브리핑이 발행되면 날짜별 목록이 여기에 표시됩니다.</p>
        </section>
        """
        latest_action = ""
        date_select = ""
        period = "대기 중"
        latest_feature = ""
    else:
        latest = entries[0]
        oldest = entries[-1]
        period = f"{oldest.report_date.isoformat()} — {latest.report_date.isoformat()}"
        latest_action = (
            f'<a class="archive-primary" href="reports/{html.escape(latest.filename)}">'
            "최신 리포트 읽기 <span aria-hidden=\"true\">→</span></a>"
        )
        options = "".join(
            f'<option value="reports/{html.escape(entry.filename)}">'
            f"{html.escape(entry.date_label)}</option>"
            for entry in entries
        )
        date_select = f"""
        <label class="archive-date-picker">
          <span>날짜로 바로 찾기</span>
          <select id="archive-date-select" aria-label="확인할 리포트 날짜">
            <option value="">날짜를 선택하세요</option>
            {options}
          </select>
        </label>
        """
        latest_feature = f"""
        <section class="archive-feature" aria-labelledby="latest-title">
          <div>
            <span class="archive-kicker">LATEST REPORT</span>
            <h2 id="latest-title">{html.escape(latest.date_label)}</h2>
            <strong>{html.escape(latest.market_line or latest.title)}</strong>
            <p>{html.escape(latest.one_line or '최신 시장 판단과 종목별 대응을 확인하세요.')}</p>
          </div>
          <a href="reports/{html.escape(latest.filename)}">최신 리포트 열기 <span aria-hidden="true">→</span></a>
        </section>
        """
        month_groups: dict[tuple[int, int], list[ReportEntry]] = {}
        for entry in entries:
            month_groups.setdefault((entry.report_date.year, entry.report_date.month), []).append(entry)
        sections = []
        for (year, month), month_entries in month_groups.items():
            cards = "".join(
                _report_card(entry, latest=entry == latest) for entry in month_entries
            )
            sections.append(
                f'<section class="archive-month" aria-labelledby="month-{year}-{month}">'
                f'<div class="archive-month__head"><h2 id="month-{year}-{month}">{year}년 {month}월</h2>'
                f"<span>{len(month_entries)}개 리포트</span></div>"
                f'<div class="archive-grid">{cards}</div></section>'
            )
        content = "".join(sections)

    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="미국장 마감 브리핑을 일자별로 확인하세요.">
  <title>일자별 미국장 마감 리포트</title>
  <link rel="stylesheet" href="assets/report.css">
</head>
<body class="archive-page">
  <a class="skip-link" href="#archive-list">리포트 목록으로 건너뛰기</a>
  <main class="archive-main">
    <header class="archive-hero">
      <div class="archive-hero__copy">
        <p class="eyebrow">DAILY US MARKET BRIEFING</p>
        <h1>지난 시장을<br>날짜별로 꺼내보세요.</h1>
        <p>거래일마다 쌓인 시장 판단, 추천 후보와 실제 수익률을 한곳에서 이어서 확인합니다.</p>
      </div>
      <div class="archive-hero__tools">
        <div class="archive-stat"><span>보관 리포트</span><strong>{len(entries)}개</strong><small>{html.escape(period)}</small></div>
        {date_select}
        {latest_action}
      </div>
    </header>
    {latest_feature}
    <section class="archive-list" id="archive-list" aria-labelledby="archive-title">
      <div class="archive-list__head">
        <div><p class="eyebrow">REPORT ARCHIVE</p><h2 id="archive-title">전체 리포트</h2></div>
        <p>최신순 · 미국장 거래일 기준</p>
      </div>
      {content}
    </section>
    <footer>Source: Yahoo Finance, RSS feeds. 투자 판단을 돕기 위한 참고 자료입니다.</footer>
  </main>
  <script>
    const picker = document.querySelector('#archive-date-select');
    if (picker) picker.addEventListener('change', () => {{
      if (picker.value) window.location.href = picker.value;
    }});
  </script>
</body>
</html>
"""


def not_found_html() -> str:
    return """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>리포트를 찾을 수 없습니다</title><link rel="stylesheet" href="/news-2/assets/report.css"></head>
<body class="archive-page"><main class="not-found"><span>404</span><h1>이 날짜의 리포트는 없습니다.</h1>
<p>주소가 바뀌었거나 아직 발행되지 않은 날짜입니다.</p><a class="archive-primary" href="/news-2/">일자별 리포트 보기</a></main></body></html>"""


def build_site(source_dir: Path, output_dir: Path, stylesheet: Path | None = None) -> int:
    reports_output = output_dir / "reports"
    assets_output = output_dir / "assets"
    reports_output.mkdir(parents=True, exist_ok=True)
    assets_output.mkdir(parents=True, exist_ok=True)

    for pattern in ("*_briefing.html", "*_briefing.md"):
        for source in source_dir.glob(pattern):
            shutil.copy2(source, reports_output / source.name)

    signals = source_dir / "signals"
    if signals.exists():
        shutil.copytree(signals, reports_output / "signals", dirs_exist_ok=True)

    css_source = stylesheet or Path(__file__).with_name("report.css")
    shutil.copy2(css_source, assets_output / "report.css")

    entries = discover_reports(reports_output)
    for entry in entries:
        path = reports_output / entry.filename
        updated = inject_report_navigation(path.read_text(encoding="utf-8"), report_navigation(entries, entry))
        path.write_text(updated, encoding="utf-8")

    (output_dir / "index.html").write_text(archive_index_html(entries), encoding="utf-8")
    (output_dir / "404.html").write_text(not_found_html(), encoding="utf-8")
    (reports_output / "index.json").write_text(
        json.dumps(
            {
                "reports": [
                    {
                        "date": entry.report_date.isoformat(),
                        "html": entry.filename,
                        "markdown": entry.filename.replace(".html", ".md"),
                    }
                    for entry in entries
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return len(entries)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GitHub Pages용 일자별 리포트 사이트 만들기")
    parser.add_argument("--source", type=Path, default=Path("reports"))
    parser.add_argument("--output", type=Path, default=Path("site"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    count = build_site(args.source, args.output)
    print(f"일자별 리포트 사이트를 만들었습니다: {count}개")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
