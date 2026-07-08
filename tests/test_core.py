from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from market_briefing_bot.briefing import _news_price_reaction, _render_report_sections, _sector_driver
from market_briefing_bot.kakao import KakaoClient, KakaoError, _load_tokens, explain_kakao_error, split_message
from market_briefing_bot.market_calendar import (
    early_close_reason,
    holiday_reason,
    is_trading_day,
    previous_trading_day,
)
from market_briefing_bot.market_data import MarketSnapshot, Quote
from market_briefing_bot.news import (
    NewsItem,
    fetch_top_news,
    korean_news_headline,
    korean_news_importance,
    korean_news_label,
    korean_news_related,
    korean_news_sentiment,
    korean_news_summary,
)
from market_briefing_bot.earnings_calendar import build_earnings_calendar
from market_briefing_bot.event_calendar import build_event_calendar
from market_briefing_bot.professional_review import build_professional_review
from market_briefing_bot.sec_filings import build_sec_filing_alert
from market_briefing_bot.watchlist import build_watchlist_review
from market_briefing_bot.investment_plan import (
    build_investment_package,
    build_investment_report,
    build_previous_signal_review,
)
from market_briefing_bot.__main__ import (
    _already_sent,
    _build_github_secrets_text,
    _kakao_delivery_text,
    _mark_send_success,
    _next_setup_step,
)


class MarketCalendarTests(unittest.TestCase):
    def test_major_holiday(self) -> None:
        self.assertFalse(is_trading_day(date(2026, 7, 3)))
        self.assertIn("Independence", holiday_reason(date(2026, 7, 3)) or "")

    def test_early_close_before_2026_independence_observance(self) -> None:
        self.assertTrue(is_trading_day(date(2026, 7, 2)))
        self.assertIn("Independence", early_close_reason(date(2026, 7, 2)) or "")

    def test_previous_trading_day_skips_weekend(self) -> None:
        self.assertEqual(previous_trading_day(date(2026, 6, 29)), date(2026, 6, 26))


class KakaoMessageTests(unittest.TestCase):
    def test_split_message_keeps_chunks_under_limit(self) -> None:
        text = "첫 줄\n" + "a" * 80 + "\n" + "b" * 80
        chunks = split_message(text, 90)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 90 for chunk in chunks))

    def test_kakao_error_explains_redirect_uri(self) -> None:
        message = explain_kakao_error(400, '{"error":"invalid_grant","error_description":"KOE006"}')
        self.assertIn("Redirect URI", message)

    def test_load_tokens_from_environment(self) -> None:
        with patch.dict("os.environ", {"KAKAO_TOKENS_JSON": '{"refresh_token":"abc"}'}):
            self.assertEqual(_load_tokens()["refresh_token"], "abc")

    def test_github_env_token_refresh_uses_new_access_token(self) -> None:
        class ConfigStub:
            kakao_rest_api_key = "rest-key"
            kakao_client_secret = ""
            kakao_link_url = "https://finance.yahoo.com/markets"
            kakao_chunk_size = 180

        calls = []

        def fake_post(url, data, headers=None):
            calls.append((url, headers or {}))
            if "talk/memo" in url and len([call for call in calls if "talk/memo" in call[0]]) == 1:
                raise KakaoError("Kakao API 오류: HTTP 401. 원문: access token does not exist")
            if "oauth/token" in url:
                return {"access_token": "new-access-token", "expires_in": 21599}
            return {}

        with (
            patch.dict(
                "os.environ",
                {"KAKAO_TOKENS_JSON": '{"access_token":"old-access-token","refresh_token":"refresh-token"}'},
            ),
            patch("market_briefing_bot.kakao._post_form", side_effect=fake_post),
            patch("market_briefing_bot.kakao._save_tokens"),
        ):
            self.assertEqual(KakaoClient(ConfigStub()).send_text("hello"), 1)

        send_headers = [headers for url, headers in calls if "talk/memo" in url]
        self.assertEqual(send_headers[-1]["Authorization"], "Bearer new-access-token")


class NewsSummaryTests(unittest.TestCase):
    def test_headline_gets_korean_summary(self) -> None:
        title = "Inflation fears are overblown as Fed rate debate moves stocks"
        self.assertEqual(korean_news_label(title), "금리/물가")
        self.assertIn("금리", korean_news_summary(title))

    def test_chip_rally_gets_investable_korean_headline(self) -> None:
        item = NewsItem(
            title="Record chip rally adds $2 trillion in combined value to Micron, Intel and AMD",
            description="Wall Street poured into chipmakers not named Nvidia as the AI boom expanded.",
            link="https://example.com",
            source="Example",
            published="",
            score=10,
        )
        self.assertEqual(korean_news_label(item), "AI/반도체")
        self.assertIn("엔비디아 밖", korean_news_headline(item))
        self.assertIn("공급망", korean_news_summary(item))

    def test_etf_flow_gets_flow_label(self) -> None:
        title = "Investors piled into ETFs at a record pace. Here is where their money is flowing."
        self.assertEqual(korean_news_label(title), "ETF/수급")

    def test_mixed_chip_futures_news_gets_mixed_sentiment(self) -> None:
        title = "S&P 500, Nasdaq futures fall as chip stocks surge in Q2 2026"
        sentiment, reason = korean_news_sentiment(title)
        self.assertEqual(sentiment, "혼재")
        self.assertIn("업종별 차별화", reason)

    def test_micron_chip_tumble_gets_specific_headline(self) -> None:
        title = "Stock Market Today: Nasdaq Slips After Strong Quarterly Run; Micron Falls As Chip Firms Tumble"
        sentiment, reason = korean_news_sentiment(title)
        self.assertEqual(sentiment, "혼재")
        self.assertIn("차익실현", reason)
        self.assertIn("마이크론", korean_news_headline(title))

    def test_microsoft_layoffs_ai_gets_mixed_sentiment(self) -> None:
        item = NewsItem(
            title="Microsoft is reportedly planning thousands of layoffs as it spends on AI",
            description="The tech giant is expected to cut less than 2.5% of its workforce.",
            link="https://example.com",
            source="Example",
            published="",
            score=10,
        )
        sentiment, reason = korean_news_sentiment(item)
        self.assertEqual(sentiment, "혼재")
        self.assertIn("AI 투자", reason)

    def test_software_news_does_not_get_chip_label(self) -> None:
        title = "ServiceNow and Salesforce shares now look like buys as AI fears are too extreme"
        self.assertEqual(korean_news_label(title), "소프트웨어")

    def test_cloud_compute_news_gets_cloud_label(self) -> None:
        title = "Meta pops as company makes cloud push to sell excess AI compute power capacity"
        self.assertEqual(korean_news_label(title), "AI/클라우드")

    def test_artificial_intelligence_does_not_match_intel_company(self) -> None:
        item = NewsItem(
            title="Employers who laid off workers citing AI are already starting to regret it",
            description="Companies are realizing artificial intelligence cannot do everything.",
            link="https://example.com",
            source="Example",
            published="",
            score=2,
        )
        self.assertNotIn("인텔", korean_news_related(item))

    def test_duplicate_specific_news_is_selected_once(self) -> None:
        first = NewsItem(
            title="Meta stock jumps on cloud computing plans to rival Amazon, Microsoft",
            description="",
            link="https://example.com/1",
            source="Yahoo",
            published="",
            score=10,
        )
        second = NewsItem(
            title="Meta pops as company makes cloud push to sell excess AI compute power capacity",
            description="The new business is a welcome signal.",
            link="https://example.com/2",
            source="CNBC",
            published="",
            score=9,
        )
        with patch("market_briefing_bot.news.fetch_rss_feed", return_value=[first, second]):
            items, _warnings = fetch_top_news(["https://example.com/rss"], max_items=5)
        self.assertEqual(len(items), 1)

    def test_duplicate_report_headline_is_selected_once(self) -> None:
        first = NewsItem(
            title="Apple and Broadcom shares rise on AI semiconductor supply chain optimism",
            description="Investors are watching AI chip suppliers.",
            link="https://example.com/1",
            source="Yahoo",
            published="",
            score=10,
        )
        second = NewsItem(
            title="Apple taps Broadcom as investors chase AI chip supplier winners",
            description="Semiconductor demand remains strong.",
            link="https://example.com/2",
            source="CNBC",
            published="",
            score=9,
        )
        with patch("market_briefing_bot.news.fetch_rss_feed", return_value=[first, second]):
            items, _warnings = fetch_top_news(["https://example.com/rss"], max_items=5)
        headlines = [korean_news_headline(item) for item in items]
        self.assertEqual(len(headlines), len(set(headlines)))

    def test_top_news_limits_repeated_ai_topics(self) -> None:
        feed_items = [
            NewsItem(
                title=f"AI chip stock rally expands to supplier {index}",
                description="Nvidia cloud compute and semiconductor demand remain strong.",
                link=f"https://example.com/ai-{index}",
                source="Example",
                published="",
                score=20 - index,
            )
            for index in range(5)
        ]
        feed_items.extend(
            [
                NewsItem(
                    title="Private payrolls weaken as labor market slows",
                    description="Jobs data affects growth expectations.",
                    link="https://example.com/jobs",
                    source="Example",
                    published="",
                    score=8,
                ),
                NewsItem(
                    title="Defense budget expands weapons spending",
                    description="Industrial and defense suppliers may benefit.",
                    link="https://example.com/defense",
                    source="Example",
                    published="",
                    score=7,
                ),
            ]
        )
        with patch("market_briefing_bot.news.fetch_rss_feed", return_value=feed_items):
            items, _warnings = fetch_top_news(["https://example.com/rss"], max_items=5)
        labels = [korean_news_label(item) for item in items]
        self.assertLessEqual(labels.count("AI/반도체"), 1)
        self.assertIn("고용", labels)
        self.assertIn("방산", labels)


class SectorReasonTests(unittest.TestCase):
    def test_technology_sector_driver_uses_ai_news(self) -> None:
        snapshot = MarketSnapshot(
            target_date=date(2026, 6, 30),
            index_quotes={},
            sector_quotes={},
            risk_quotes={},
            warnings=[],
        )
        news = [
            NewsItem(
                title="Meta expands AI cloud compute business",
                description="AI infrastructure demand remains strong.",
                link="https://example.com",
                source="Example",
                published="",
                score=10,
            )
        ]
        self.assertIn("AI/반도체", _sector_driver("Technology", 2.0, snapshot, news))

    def test_weak_technology_sector_driver_respects_price_action(self) -> None:
        snapshot = MarketSnapshot(
            target_date=date(2026, 6, 30),
            index_quotes={},
            sector_quotes={},
            risk_quotes={},
            warnings=[],
        )
        news = [
            NewsItem(
                title="Meta expands AI cloud compute business",
                description="AI infrastructure demand remains strong.",
                link="https://example.com",
                source="Example",
                published="",
                score=10,
            )
        ]
        reason = _sector_driver("Technology", -2.0, snapshot, news)
        self.assertIn("크게 밀려", reason)
        self.assertIn("차익실현", reason)

    def test_defensive_sector_driver_uses_vix_fall(self) -> None:
        snapshot = MarketSnapshot(
            target_date=date(2026, 6, 30),
            index_quotes={},
            sector_quotes={},
            risk_quotes={
                "VIX": Quote(
                    name="VIX",
                    symbol="^VIX",
                    trading_date=date(2026, 6, 30),
                    close=16.0,
                    previous_close=18.0,
                    change_percent=-8.0,
                    source="Yahoo",
                )
            },
            warnings=[],
        )
        self.assertIn("방어주 선호", _sector_driver("Utilities", -1.0, snapshot, []))


class InvestmentPlanTests(unittest.TestCase):
    def test_investment_report_contains_entry_stop_and_rationale(self) -> None:
        snapshot = MarketSnapshot(
            target_date=date(2026, 7, 2),
            index_quotes={},
            sector_quotes={},
            risk_quotes={},
            warnings=[],
        )
        sectors = [
            Quote("Technology", "XLK", date(2026, 7, 2), 100, 98, 2.0, "test"),
            Quote("Utilities", "XLU", date(2026, 7, 2), 50, 51, -2.0, "test"),
        ]
        rows = [
            {"date": date(2026, 6, 26), "close": 90.0},
            {"date": date(2026, 6, 29), "close": 92.0},
            {"date": date(2026, 6, 30), "close": 94.0},
            {"date": date(2026, 7, 1), "close": 96.0},
            {"date": date(2026, 7, 2), "close": 100.0},
        ]
        news = [
            NewsItem(
                title="AI chip demand remains strong for Nvidia",
                description="Semiconductor demand supports technology shares.",
                link="https://example.com",
                source="Example",
                published="",
                score=10,
            )
        ]
        with patch("market_briefing_bot.investment_plan.fetch_yahoo_daily", return_value=rows):
            report, warnings = build_investment_report(snapshot, sectors, news)
        self.assertFalse(warnings)
        self.assertIn("유의 섹터", report)
        self.assertIn("관심 후보", report)
        self.assertIn("비선호 후보", report)
        self.assertIn("매수 타점", report)
        self.assertIn("손절 타점", report)
        self.assertIn("매수 근거", report)
        self.assertIn("손절 근거", report)
        self.assertIn("점수", report)

    def test_investment_package_exposes_signals_for_next_day_tracking(self) -> None:
        snapshot = MarketSnapshot(
            target_date=date(2026, 7, 2),
            index_quotes={},
            sector_quotes={},
            risk_quotes={},
            warnings=[],
        )
        sectors = [
            Quote("Technology", "XLK", date(2026, 7, 2), 100, 98, 2.0, "test"),
            Quote("Utilities", "XLU", date(2026, 7, 2), 50, 51, -2.0, "test"),
        ]
        rows = [
            {"date": date(2026, 6, 26), "close": 90.0},
            {"date": date(2026, 6, 29), "close": 92.0},
            {"date": date(2026, 6, 30), "close": 94.0},
            {"date": date(2026, 7, 1), "close": 96.0},
            {"date": date(2026, 7, 2), "close": 100.0},
        ]
        with patch("market_briefing_bot.investment_plan.fetch_yahoo_daily", return_value=rows):
            package = build_investment_package(snapshot, sectors, [])
        self.assertEqual(package.signals["target_date"], "2026-07-02")
        self.assertTrue(package.signals["interest"])
        self.assertIn("score", package.signals["interest"][0])

    def test_previous_signal_review_marks_entry_hit(self) -> None:
        snapshot = MarketSnapshot(
            target_date=date(2026, 7, 3),
            index_quotes={},
            sector_quotes={},
            risk_quotes={},
            warnings=[],
        )
        previous = {
            "target_date": "2026-07-02",
            "interest": [
                {
                    "stance": "관심 후보",
                    "symbol": "NVDA",
                    "name": "엔비디아",
                    "close": 100.0,
                    "score": 80,
                    "entry_price": 105.0,
                    "stop_price": 95.0,
                }
            ],
            "avoid": [],
        }
        rows = [{"date": date(2026, 7, 3), "close": 106.0}]
        with patch("market_briefing_bot.investment_plan.fetch_yahoo_daily", return_value=rows):
            text, warnings = build_previous_signal_review(snapshot, previous)
        self.assertFalse(warnings)
        self.assertIn("전일 후보 추적", text)
        self.assertIn("매수 타점 도달", text)


class KakaoDeliveryTextTests(unittest.TestCase):
    def test_link_mode_sends_short_report_url_message(self) -> None:
        class ConfigStub:
            kakao_send_mode = "link"
            report_public_base_url = "https://example.github.io/news-2/reports"
            kakao_chunk_size = 200

        class BriefingStub:
            text = (
                "미국장 마감 2026-07-07\n"
                "S&P 500 -0.45%, Nasdaq -1.16%, Dow -0.25%\n"
                "한줄: 방어적인 해석이 필요합니다.\n"
                "긴 본문"
            )

            class HtmlPath:
                name = "2026-07-07_briefing.html"

            html_path = HtmlPath()

        text = _kakao_delivery_text(ConfigStub(), BriefingStub())
        self.assertIn("전체 보고서", text)
        self.assertIn("https://example.github.io/news-2/reports/2026-07-07_briefing.html", text)
        self.assertLessEqual(len(text), 200)


class HtmlReportTests(unittest.TestCase):
    def test_report_sections_are_not_rendered_as_raw_message_pre(self) -> None:
        rendered = _render_report_sections(
            "Market summary\nLine one\n\n뉴스 1/5 [Market]\nSkipped duplicate\n\nAction report\n- point one"
        )
        self.assertIn('class="report-section"', rendered)
        self.assertIn("Action report", rendered)
        self.assertNotIn("뉴스 1/5", rendered)
        self.assertNotIn("<pre", rendered)


class WatchlistTests(unittest.TestCase):
    def test_watchlist_review_connects_symbol_to_sector(self) -> None:
        snapshot = MarketSnapshot(
            target_date=date(2026, 7, 2),
            index_quotes={},
            sector_quotes={
                "Technology": Quote("Technology", "XLK", date(2026, 7, 2), 100, 98, 2.0, "test")
            },
            risk_quotes={},
            warnings=[],
        )
        rows = [
            {"date": date(2026, 7, 1), "close": 100.0},
            {"date": date(2026, 7, 2), "close": 103.0},
        ]
        with patch("market_briefing_bot.watchlist.fetch_yahoo_daily", return_value=rows):
            text, warnings = build_watchlist_review(["NVDA"], snapshot)
        self.assertFalse(warnings)
        self.assertIn("보유/관심종목 영향", text)
        self.assertIn("NVDA", text)
        self.assertIn("섹터", text)
        self.assertIn("상대강도", text)

    def test_watchlist_review_reports_portfolio_concentration(self) -> None:
        snapshot = MarketSnapshot(
            target_date=date(2026, 7, 2),
            index_quotes={},
            sector_quotes={
                "Technology": Quote("Technology", "XLK", date(2026, 7, 2), 100, 99, 1.0, "test")
            },
            risk_quotes={},
            warnings=[],
        )
        rows = [
            {"date": date(2026, 7, 1), "close": 100.0},
            {"date": date(2026, 7, 2), "close": 103.0},
        ]
        with patch("market_briefing_bot.watchlist.fetch_yahoo_daily", return_value=rows):
            text, warnings = build_watchlist_review(["NVDA", "MSFT"], snapshot)

        self.assertFalse(warnings)
        self.assertIn("포트폴리오 리스크 요약", text)
        self.assertIn("쏠림", text)


class ProfessionalReviewTests(unittest.TestCase):
    def test_professional_review_sets_action_and_invalidation(self) -> None:
        snapshot = MarketSnapshot(
            target_date=date(2026, 7, 7),
            index_quotes={
                "S&P 500": Quote("S&P 500", "SPY", date(2026, 7, 7), 100, 99, 1.0, "test"),
                "Nasdaq": Quote("Nasdaq", "QQQ", date(2026, 7, 7), 100, 99, 1.0, "test"),
            },
            sector_quotes={},
            risk_quotes={
                "VIX": Quote("VIX", "^VIX", date(2026, 7, 7), 15, 16, -6.0, "test"),
                "10Y Yield": Quote("10Y Yield", "^TNX", date(2026, 7, 7), 4.2, 4.2, -1.2, "test"),
            },
            warnings=[],
        )
        sectors = [
            Quote("Technology", "XLK", date(2026, 7, 7), 100, 98, 2.0, "test"),
            Quote("Communication Services", "XLC", date(2026, 7, 7), 100, 99, 1.0, "test"),
            Quote("Industrials", "XLI", date(2026, 7, 7), 100, 99, 1.0, "test"),
            Quote("Financials", "XLF", date(2026, 7, 7), 100, 99, 1.0, "test"),
            Quote("Materials", "XLB", date(2026, 7, 7), 100, 99, 1.0, "test"),
            Quote("Energy", "XLE", date(2026, 7, 7), 100, 99, 1.0, "test"),
            Quote("Health Care", "XLV", date(2026, 7, 7), 100, 101, -1.0, "test"),
        ]

        text = build_professional_review(snapshot, sectors, [])

        self.assertIn("전문 투자자 체크", text)
        self.assertIn("매매 강도", text)
        self.assertIn("무효화 조건", text)
        self.assertIn("섹터 로테이션 판정", text)

    def test_professional_review_contains_trading_score_and_warning(self) -> None:
        snapshot = MarketSnapshot(
            target_date=date(2026, 7, 7),
            index_quotes={
                "S&P 500": Quote("S&P 500", "SPY", date(2026, 7, 7), 100, 101, -1.0, "test"),
                "Nasdaq": Quote("Nasdaq", "QQQ", date(2026, 7, 7), 100, 102, -2.0, "test"),
            },
            sector_quotes={},
            risk_quotes={
                "VIX": Quote("VIX", "^VIX", date(2026, 7, 7), 20, 18, 11.0, "test"),
                "10Y Yield": Quote("10Y Yield", "^TNX", date(2026, 7, 7), 4.5, 4.4, 2.0, "test"),
            },
            warnings=[],
        )
        sectors = [
            Quote("Utilities", "XLU", date(2026, 7, 7), 100, 99, 1.0, "test"),
            Quote("Technology", "XLK", date(2026, 7, 7), 100, 103, -3.0, "test"),
        ]

        text = build_professional_review(snapshot, sectors, [])

        self.assertIn("오늘 매매 가능 점수", text)
        self.assertIn("오늘의 경고", text)
        self.assertIn("방어", text)


class EarningsCalendarTests(unittest.TestCase):
    def test_earnings_calendar_without_key_explains_secret(self) -> None:
        text, warnings = build_earnings_calendar(["NVDA"], "", date(2026, 7, 7))
        self.assertFalse(warnings)
        self.assertIn("ALPHA_VANTAGE_API_KEY", text)

    def test_earnings_calendar_reports_upcoming_watchlist_event(self) -> None:
        csv_text = (
            "symbol,name,reportDate,fiscalDateEnding,estimate,currency\n"
            "NVDA,NVIDIA Corp,2026-07-20,2026-06-30,1.23,USD\n"
        )
        with patch("market_briefing_bot.earnings_calendar._download_text", return_value=csv_text):
            text, warnings = build_earnings_calendar(["NVDA"], "key", date(2026, 7, 7))

        self.assertFalse(warnings)
        self.assertIn("NVDA", text)
        self.assertIn("2026-07-20", text)


class NewsDecisionQualityTests(unittest.TestCase):
    def test_news_importance_marks_macro_as_high_priority(self) -> None:
        item = NewsItem(
            title="Fed rate path remains uncertain as inflation data looms",
            description="Treasury yields move higher.",
            link="https://example.com",
            source="Example",
            published="",
            score=5,
        )
        importance, _reason = korean_news_importance(item)
        self.assertEqual(importance, "A급")

    def test_price_reaction_flags_good_news_with_weak_sector(self) -> None:
        item = NewsItem(
            title="Nvidia chip demand remains strong as AI semiconductor spending grows",
            description="AI chip suppliers see demand.",
            link="https://example.com",
            source="Example",
            published="",
            score=5,
        )
        snapshot = MarketSnapshot(
            target_date=date(2026, 7, 7),
            index_quotes={},
            sector_quotes={
                "Technology": Quote("Technology", "XLK", date(2026, 7, 7), 100, 102, -2.0, "test")
            },
            risk_quotes={},
            warnings=[],
        )

        self.assertIn("가격은 약", _news_price_reaction(item, snapshot))


class EventCalendarTests(unittest.TestCase):
    def test_event_calendar_without_key_explains_setup(self) -> None:
        text, warnings = build_event_calendar("", date(2026, 7, 2))
        self.assertFalse(warnings)
        self.assertIn("이번 주 이벤트 캘린더", text)
        self.assertIn("FRED_API_KEY", text)

    def test_event_calendar_filters_major_releases(self) -> None:
        payload = {
            "release_dates": [
                {"release_id": 10, "date": "2026-07-05"},
                {"release_id": 999, "date": "2026-07-05"},
            ]
        }
        with patch("market_briefing_bot.event_calendar._download_json", return_value=payload):
            text, warnings = build_event_calendar("key", date(2026, 7, 2))
        self.assertFalse(warnings)
        self.assertIn("CPI", text)
        self.assertNotIn("999", text)


class SecFilingTests(unittest.TestCase):
    def test_sec_filing_alert_reports_recent_important_forms(self) -> None:
        ticker_payload = {
            "0": {"ticker": "AAPL", "cik_str": 320193, "title": "Apple Inc."}
        }
        submissions_payload = {
            "filings": {
                "recent": {
                    "form": ["8-K", "4"],
                    "filingDate": ["2026-07-01", "2026-07-01"],
                    "accessionNumber": ["0000320193-26-000001", "0000320193-26-000002"],
                    "primaryDocument": ["aapl-20260701.htm", "xslF345X05/doc.xml"],
                    "primaryDocDescription": ["Current report", "Insider transaction"],
                }
            }
        }

        def fake_download(url, user_agent):
            if "company_tickers" in url:
                return ticker_payload
            return submissions_payload

        with patch("market_briefing_bot.sec_filings._download_json", side_effect=fake_download):
            text, warnings = build_sec_filing_alert(["AAPL"], date(2026, 7, 7), "agent@example.com")
        self.assertFalse(warnings)
        self.assertIn("관심종목 SEC 공시", text)
        self.assertIn("AAPL 2026-07-01 8-K", text)
        self.assertNotIn("Insider transaction", text)


class CloudSecretsTests(unittest.TestCase):
    def test_build_github_secrets_text_contains_expected_names(self) -> None:
        text = _build_github_secrets_text("rest-key", {"refresh_token": "refresh"})
        self.assertIn("KAKAO_REST_API_KEY", text)
        self.assertIn("KAKAO_TOKENS_JSON", text)
        self.assertIn("WATCHLIST_SYMBOLS", text)
        self.assertIn("ALPHA_VANTAGE_API_KEY", text)
        self.assertIn('"refresh_token":"refresh"', text)

    def test_next_setup_accepts_environment_config(self) -> None:
        class ConfigStub:
            kakao_rest_api_key = "rest-key"

        with patch.dict("os.environ", {"KAKAO_REST_API_KEY": "rest-key", "KAKAO_TOKENS_JSON": '{"refresh_token":"abc"}'}):
            self.assertIn("send-test", _next_setup_step(ConfigStub()))


class SendStateTests(unittest.TestCase):
    def test_send_state_tracks_target_date(self) -> None:
        with TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "send_state.json"
            with patch("market_briefing_bot.__main__.SEND_STATE_FILE", state_file):
                self.assertFalse(_already_sent("2026-06-30"))
                _mark_send_success("2026-06-30", 12, "report.md", "report.html")
                self.assertTrue(_already_sent("2026-06-30"))
                self.assertFalse(_already_sent("2026-07-01"))


if __name__ == "__main__":
    unittest.main()
