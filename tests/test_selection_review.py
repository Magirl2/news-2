from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from market_briefing_bot.selection_review import (
    collect_signal_evaluations,
    evaluate_signal,
    render_selection_review,
)


def _row(day: int, close: float, high: float | None = None, low: float | None = None) -> dict:
    return {
        "date": date(2026, 7, day),
        "close": close,
        "high": high if high is not None else close,
        "low": low if low is not None else close,
    }


class SelectionReviewTests(unittest.TestCase):
    def test_evaluate_signal_marks_target_first(self) -> None:
        signal = {
            "date": "2026-07-01",
            "symbol": "AMD",
            "entry_action": "지금은 1차 진입만 가능",
            "position_mode": "손익비 우수",
            "close": 100,
            "start_entry_price": 100,
            "invalidation_price": 95,
            "first_target_price": 110,
        }
        rows = [
            _row(1, 100),
            _row(2, 104, high=105, low=99),
            _row(3, 111, high=111, low=103),
            _row(4, 112),
            _row(5, 113),
            _row(6, 114),
        ]

        result = evaluate_signal(signal, "interest", rows, horizon=5)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.outcome, "TARGET_FIRST")
        self.assertAlmostEqual(result.r_result or 0, 2.0)
        self.assertEqual(result.bucket, "지금은 1차 진입만 가능 / 손익비 우수")

    def test_evaluate_signal_marks_stop_first(self) -> None:
        signal = {
            "date": "2026-07-01",
            "symbol": "NVDA",
            "close": 100,
            "start_entry_price": 100,
            "invalidation_price": 95,
            "first_target_price": 110,
        }
        rows = [
            _row(1, 100),
            _row(2, 96, high=101, low=94),
            _row(3, 99),
        ]

        result = evaluate_signal(signal, "interest", rows, horizon=5)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.outcome, "STOP_FIRST")
        self.assertEqual(result.r_result, -1.0)

    def test_evaluate_signal_uses_close_when_start_entry_is_missing(self) -> None:
        signal = {
            "date": "2026-07-01",
            "symbol": "AAPL",
            "close": 100,
            "check_price": 120,
            "invalidation_price": 95,
            "first_target_price": 110,
        }
        rows = [
            _row(1, 100),
            _row(2, 102, high=103, low=99),
            _row(3, 108, high=111, low=107),
        ]

        result = evaluate_signal(signal, "avoid", rows, horizon=5)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.reference_price, 100)
        self.assertEqual(result.outcome, "TARGET_FIRST")

    def test_evaluate_signal_ignores_invalid_backward_target(self) -> None:
        signal = {
            "date": "2026-07-01",
            "symbol": "MSFT",
            "close": 100,
            "invalidation_price": 95,
            "first_target_price": 98,
        }
        rows = [_row(1, 100), _row(2, 99, high=101, low=98), _row(3, 102)]

        result = evaluate_signal(signal, "avoid", rows, horizon=5)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.outcome, "OPEN")
        self.assertAlmostEqual(result.r_result or 0, 0.4)

    def test_collect_signal_evaluations_reads_signal_files(self) -> None:
        with TemporaryDirectory() as raw_dir:
            reports_dir = Path(raw_dir)
            signal_dir = reports_dir / "signals"
            signal_dir.mkdir()
            (signal_dir / "2026-07-01_signals.json").write_text(
                """
                {
                  "target_date": "2026-07-01",
                  "interest": [
                    {
                      "date": "2026-07-01",
                      "symbol": "AMD",
                      "close": 100,
                      "start_entry_price": 100,
                      "invalidation_price": 95,
                      "first_target_price": 110,
                      "entry_action": "지금은 1차 진입만 가능",
                      "position_mode": "손익비 우수"
                    }
                  ],
                  "avoid": []
                }
                """,
                encoding="utf-8",
            )

            def fake_fetch(symbol: str) -> list[dict]:
                if symbol == "SPY":
                    return [_row(1, 500), _row(2, 502), _row(3, 503), _row(4, 504), _row(5, 505), _row(6, 506)]
                return [_row(1, 100), _row(2, 105), _row(3, 111), _row(4, 112), _row(5, 113), _row(6, 114)]

            results, warnings = collect_signal_evaluations(
                reports_dir,
                horizon=5,
                limit=10,
                price_fetcher=fake_fetch,
            )

        self.assertEqual(warnings, [])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].symbol, "AMD")
        self.assertEqual(results[0].outcome, "TARGET_FIRST")

    def test_render_selection_review_includes_summary_and_detail(self) -> None:
        signal = {
            "date": "2026-07-01",
            "symbol": "AMD",
            "close": 100,
            "start_entry_price": 100,
            "invalidation_price": 95,
            "first_target_price": 110,
            "recommendation_state": "ENTRY_READY",
        }
        rows = [_row(1, 100), _row(2, 105), _row(3, 111), _row(4, 112), _row(5, 113), _row(6, 114)]
        result = evaluate_signal(signal, "interest", rows, horizon=5)
        assert result is not None

        text = render_selection_review([result], [])

        self.assertIn("전체 요약", text)
        self.assertIn("ENTRY_READY", text)
        self.assertIn("목표 먼저", text)
        self.assertIn("AMD", text)


if __name__ == "__main__":
    unittest.main()
