from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from market_briefing_bot.site_builder import (
    build_site,
    discover_reports,
    inject_report_navigation,
    report_navigation,
)


def _report_html(report_date: str, market: str, summary: str) -> str:
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><title>미국장 마감 {report_date}</title></head>
<body><main><header class="hero"><div class="market-line">{market}</div>
<p class="one-line">{summary}</p></header><section id="quick-summary">본문</section></main></body></html>"""


class SiteBuilderTests(unittest.TestCase):
    def test_builds_archive_and_adds_date_navigation_to_reports(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "reports"
            output = root / "site"
            source.mkdir()
            css = root / "report.css"
            css.write_text("body { color: #123; }", encoding="utf-8")
            for report_date, market in (
                ("2026-09-10", "S&P 500 +1.00%"),
                ("2026-09-11", "S&P 500 +0.50%"),
            ):
                (source / f"{report_date}_briefing.html").write_text(
                    _report_html(report_date, market, f"한줄: {report_date} 요약"),
                    encoding="utf-8",
                )
                (source / f"{report_date}_briefing.md").write_text("보고서", encoding="utf-8")

            count = build_site(source, output, stylesheet=css)

            self.assertEqual(count, 2)
            index = (output / "index.html").read_text(encoding="utf-8")
            latest = (output / "reports/2026-09-11_briefing.html").read_text(encoding="utf-8")
            older = (output / "reports/2026-09-10_briefing.html").read_text(encoding="utf-8")
            self.assertIn("일자별 미국장 마감 리포트", index)
            self.assertIn('id="archive-date-select"', index)
            self.assertIn("2026-09-11_briefing.html", index)
            self.assertIn("2026-09-10_briefing.html", index)
            self.assertIn("리포트 모아보기", latest)
            self.assertIn('href="2026-09-10_briefing.html"', latest)
            self.assertIn("다음 →", older)
            self.assertEqual(latest.count("REPORT_BROWSER_START"), 1)
            self.assertEqual(latest.count("../assets/report.css"), 1)
            self.assertTrue((output / "404.html").exists())
            manifest = json.loads((output / "reports/index.json").read_text(encoding="utf-8"))
            self.assertEqual(
                [item["date"] for item in manifest["reports"]],
                ["2026-09-11", "2026-09-10"],
            )

    def test_navigation_rebuild_replaces_old_navigation(self) -> None:
        with TemporaryDirectory() as temp_dir:
            reports = Path(temp_dir)
            path = reports / "2026-09-11_briefing.html"
            path.write_text(_report_html("2026-09-11", "S&P +1%", "요약"), encoding="utf-8")
            entries = discover_reports(reports)
            navigation = report_navigation(entries, entries[0])

            once = inject_report_navigation(path.read_text(encoding="utf-8"), navigation)
            twice = inject_report_navigation(once, navigation)

            self.assertEqual(twice.count("REPORT_BROWSER_START"), 1)
            self.assertEqual(twice.count("back-to-top"), 1)
            self.assertIn("aria-disabled=\"true\"", twice)


if __name__ == "__main__":
    unittest.main()
