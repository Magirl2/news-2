from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from market_briefing_bot.live_bot.alerts import Alert, should_trigger
from market_briefing_bot.live_bot.commands import parse_command
from market_briefing_bot.live_bot.formatter import format_analysis
from market_briefing_bot.live_bot.storage import AlertStore
from market_briefing_bot.live_bot.telegram_app import handle_text, load_live_bot_config


class LiveBotCommandTests(unittest.TestCase):
    def test_symbol_inputs_parse_as_analysis(self) -> None:
        self.assertEqual(parse_command("AMD").kind, "analyze")
        self.assertEqual(parse_command("AMD 지금 어때").symbol, "AMD")
        self.assertEqual(parse_command("AMD 손익비").kind, "analyze")

    def test_price_alert_inputs_parse(self) -> None:
        breakout = parse_command("AMD 155 돌파 알림")
        self.assertEqual(breakout.kind, "add_alert")
        self.assertEqual(breakout.symbol, "AMD")
        self.assertEqual(breakout.condition_type, "breakout")
        self.assertEqual(breakout.target_price, 155.0)

        breakdown = parse_command("NVDA 150 이탈 알림")
        self.assertEqual(breakdown.condition_type, "breakdown")
        self.assertEqual(breakdown.target_price, 150.0)

        self.assertEqual(parse_command("알림 목록").kind, "list_alerts")
        self.assertEqual(parse_command("알림 삭제 AMD").delete_target, "AMD")
        self.assertEqual(parse_command("알림 전체 삭제").kind, "delete_all_alerts")

    def test_dynamic_alert_inputs_parse(self) -> None:
        self.assertEqual(parse_command("AMD 추가진입 알림").condition_type, "add_entry")
        self.assertEqual(parse_command("AMD 확인진입 알림").condition_type, "confirm_entry")
        self.assertEqual(parse_command("AMD 무효화 알림").condition_type, "invalidation")


class LiveBotAlertTests(unittest.TestCase):
    def test_alert_trigger_rules(self) -> None:
        base = Alert(None, "1", "1", "AMD", "breakout", 155.0)
        self.assertTrue(should_trigger(base, 155.01))
        self.assertFalse(should_trigger(base, 154.99))
        self.assertTrue(should_trigger(Alert(None, "1", "1", "AMD", "breakdown", 150.0), 149.9))
        self.assertTrue(should_trigger(Alert(None, "1", "1", "AMD", "touch", 100.0), 100.1))
        self.assertFalse(should_trigger(Alert(None, "1", "1", "AMD", "breakout", 100.0, active=False), 101.0))

    def test_storage_separates_users_and_prevents_duplicate_active_alerts(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = AlertStore(Path(temp_dir) / "alerts.sqlite")
            first = store.add_alert(Alert(None, "user-1", "chat-1", "AMD", "breakout", 155.0))
            duplicate = store.add_alert(Alert(None, "user-1", "chat-1", "AMD", "breakout", 155.0))
            store.add_alert(Alert(None, "user-2", "chat-2", "AMD", "breakout", 155.0))

            self.assertEqual(first.id, duplicate.id)
            self.assertEqual(len(store.list_alerts("user-1")), 1)
            self.assertEqual(len(store.list_alerts("user-2")), 1)
            self.assertEqual(store.delete_alerts("user-1", "AMD"), 1)
            self.assertEqual(len(store.list_alerts("user-1")), 0)
            self.assertEqual(len(store.list_alerts("user-2")), 1)


class LiveBotMessageTests(unittest.TestCase):
    def test_live_bot_config_reads_env_file_values(self) -> None:
        with patch.dict("os.environ", {}, clear=True), patch(
            "market_briefing_bot.live_bot.telegram_app._read_env_file",
            return_value={
                "TELEGRAM_BOT_TOKEN": "token-from-env-file",
                "TELEGRAM_ALLOWED_CHAT_IDS": "111,222",
                "LIVE_BOT_DB_PATH": "data/custom.sqlite",
                "LIVE_CHECK_INTERVAL_SECONDS": "45",
            },
        ):
            config = load_live_bot_config()

        self.assertEqual(config.telegram_bot_token, "token-from-env-file")
        self.assertEqual(config.allowed_chat_ids, {"111", "222"})
        self.assertEqual(config.db_path.as_posix(), "data/custom.sqlite")
        self.assertEqual(config.check_interval_seconds, 45)

    def test_handle_text_registers_alert_with_explicit_price(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = AlertStore(Path(temp_dir) / "alerts.sqlite")
            with patch("market_briefing_bot.live_bot.telegram_app.get_latest_price") as latest:
                latest.return_value.price = 153.8
                text = handle_text("AMD 155 돌파 알림", "user-1", "chat-1", store)

            self.assertIn("AMD 알림 등록 완료", text)
            self.assertEqual(len(store.list_alerts("user-1")), 1)

    def test_dynamic_alert_keeps_condition_name(self) -> None:
        class Plan:
            close = 153.8
            add_entry_price = 155.0
            confirm_entry_price = 158.0
            invalidation_price = 150.0

        class Analysis:
            plan = Plan()

        with TemporaryDirectory() as temp_dir:
            store = AlertStore(Path(temp_dir) / "alerts.sqlite")
            with patch("market_briefing_bot.live_bot.telegram_app.analyze_symbol", return_value=Analysis()):
                text = handle_text("AMD 추가진입 알림", "user-1", "chat-1", store)

            alerts = store.list_alerts("user-1")
            self.assertIn("AMD 알림 등록 완료", text)
            self.assertEqual(alerts[0].condition_type, "add_entry")
            self.assertEqual(alerts[0].target_price, 155.0)

    def test_analysis_message_avoids_forbidden_phrases(self) -> None:
        class Plan:
            close = 100.0
            change_percent = 1.2
            entry_action = "지금 소량 가능"
            position_mode = "손익비 우수"
            start_weight_percent = 25
            max_start_weight_percent = 30
            risk_reward_ratio = 2.2
            risk_reward_grade = "우수"
            start_entry_price = 100.0
            add_entry_price = 101.0
            confirm_entry_price = 102.0
            invalidation_price = 97.0
            first_target_price = 106.0
            can_enter_reason = "20일선 근처입니다."
            entry_risk = "장 초반 흔들림이 있을 수 있습니다."
            size_up_condition = "거래량 유지 시 추가 확인"
            small_only_reason = "거래량이 줄면 작게 봅니다."
            add_condition = "전일 고가 회복"

        class Analysis:
            symbol = "AMD"
            plan = Plan()
            related_news = []
            comparison = "아침 판단과 큰 변화 없음"
            data_time = "2026-07-10 23:15 KST"
            warnings = []

        text = format_analysis(Analysis())
        self.assertIn("손익비 우수", text)
        self.assertNotIn("매수 추천", text)
        self.assertNotIn("풀매수 가능", text)


if __name__ == "__main__":
    unittest.main()
