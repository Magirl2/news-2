from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .alerts import Alert, should_trigger
from .analyzer import LiveAnalysis, analyze_symbol
from .commands import ParsedCommand, parse_command
from .formatter import (
    format_alert_deleted,
    format_alert_list,
    format_alert_registered,
    format_alert_triggered,
    format_analysis,
    help_text,
)
from .price_feed import get_latest_price
from .storage import AlertStore


@dataclass(frozen=True)
class LiveBotConfig:
    telegram_bot_token: str
    allowed_chat_ids: set[str]
    db_path: Path
    check_interval_seconds: int


def load_live_bot_config() -> LiveBotConfig:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    allowed_raw = os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "").strip()
    allowed_chat_ids = {item.strip() for item in allowed_raw.split(",") if item.strip()}
    db_path = Path(os.environ.get("LIVE_BOT_DB_PATH", "data/live_bot.sqlite"))
    try:
        interval = max(30, int(os.environ.get("LIVE_CHECK_INTERVAL_SECONDS", "60")))
    except ValueError:
        interval = 60
    return LiveBotConfig(
        telegram_bot_token=token,
        allowed_chat_ids=allowed_chat_ids,
        db_path=db_path,
        check_interval_seconds=interval,
    )


class TelegramClient:
    def __init__(self, token: str):
        if not token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN이 필요합니다.")
        self.base_url = f"https://api.telegram.org/bot{token}"

    def request(self, method: str, payload: dict | None = None) -> dict:
        data = None
        if payload is not None:
            data = urllib.parse.urlencode(payload).encode("utf-8")
        request = urllib.request.Request(f"{self.base_url}/{method}", data=data)
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def send_message(self, chat_id: str, text: str) -> None:
        self.request("sendMessage", {"chat_id": chat_id, "text": text})

    def get_updates(self, offset: int | None = None, timeout: int = 25) -> list[dict]:
        payload: dict[str, object] = {"timeout": timeout}
        if offset is not None:
            payload["offset"] = offset
        data = self.request("getUpdates", payload)
        return list(data.get("result", []))


def handle_text(text: str, telegram_user_id: str, chat_id: str, store: AlertStore) -> str:
    command = parse_command(text)
    if command.kind == "help":
        return help_text()
    if command.kind == "list_alerts":
        return format_alert_list(store.list_alerts(telegram_user_id))
    if command.kind == "delete_all_alerts":
        return format_alert_deleted(store.delete_all_alerts(telegram_user_id))
    if command.kind == "delete_alert":
        return format_alert_deleted(store.delete_alerts(telegram_user_id, command.delete_target or ""))
    if command.kind == "analyze" and command.symbol:
        return format_analysis(analyze_symbol(command.symbol))
    if command.kind == "add_alert" and command.symbol and command.condition_type:
        alert, current_price = _build_alert_from_command(command, telegram_user_id, chat_id)
        saved = store.add_alert(alert)
        return format_alert_registered(saved, current_price)
    return "명령을 이해하지 못했습니다.\n\n" + help_text()


def _build_alert_from_command(
    command: ParsedCommand, telegram_user_id: str, chat_id: str
) -> tuple[Alert, float | None]:
    assert command.symbol is not None
    assert command.condition_type is not None
    current_price: float | None = None
    target_price = command.target_price
    if target_price is None:
        analysis = analyze_symbol(command.symbol, include_news=False)
        plan = analysis.plan
        if command.condition_type == "add_entry":
            target_price = plan.add_entry_price
        elif command.condition_type == "confirm_entry":
            target_price = plan.confirm_entry_price
        elif command.condition_type == "invalidation":
            target_price = plan.invalidation_price
        current_price = plan.close
    else:
        try:
            current_price = get_latest_price(command.symbol).price
        except Exception:  # noqa: BLE001 - alert can still be registered with explicit target.
            current_price = None
    if target_price is None:
        raise RuntimeError(f"{command.symbol}의 알림 기준 가격을 계산하지 못했습니다.")
    condition_type = command.condition_type
    if condition_type == "add_entry":
        condition_type = "breakout"
    elif condition_type == "confirm_entry":
        condition_type = "breakout"
    elif condition_type == "invalidation":
        condition_type = "breakdown"
    return (
        Alert(
            id=None,
            telegram_user_id=telegram_user_id,
            chat_id=chat_id,
            symbol=command.symbol,
            condition_type=condition_type,
            target_price=float(target_price),
            note=command.raw_text,
        ),
        current_price,
    )


def check_alerts(store: AlertStore, client: TelegramClient | None = None) -> list[tuple[Alert, float, LiveAnalysis | None]]:
    triggered: list[tuple[Alert, float, LiveAnalysis | None]] = []
    alerts = store.list_alerts(active_only=True)
    prices: dict[str, float] = {}
    for alert in alerts:
        try:
            if alert.symbol not in prices:
                prices[alert.symbol] = get_latest_price(alert.symbol).price
            current_price = prices[alert.symbol]
            store.update_last_checked(alert.id or 0, current_price)
            if not should_trigger(alert, current_price):
                continue
            analysis: LiveAnalysis | None = None
            try:
                analysis = analyze_symbol(alert.symbol, include_news=False)
            except Exception:
                analysis = None
            if client:
                client.send_message(alert.chat_id, format_alert_triggered(alert, current_price, analysis))
            if alert.id is not None:
                store.mark_triggered(alert.id, current_price)
            triggered.append((alert, current_price, analysis))
        except Exception as exc:  # noqa: BLE001 - keep other alerts running.
            if client:
                client.send_message(alert.chat_id, f"{alert.symbol} 알림 확인 중 오류: {exc}")
    return triggered


def run_polling(config: LiveBotConfig | None = None) -> None:
    config = config or load_live_bot_config()
    client = TelegramClient(config.telegram_bot_token)
    store = AlertStore(config.db_path)
    offset: int | None = None
    last_alert_check = 0.0
    while True:
        now = time.time()
        if now - last_alert_check >= config.check_interval_seconds:
            check_alerts(store, client)
            last_alert_check = now
        for update in client.get_updates(offset=offset):
            offset = int(update["update_id"]) + 1
            message = update.get("message") or {}
            text = message.get("text") or ""
            chat = message.get("chat") or {}
            user = message.get("from") or {}
            chat_id = str(chat.get("id", ""))
            user_id = str(user.get("id", chat_id))
            if config.allowed_chat_ids and chat_id not in config.allowed_chat_ids and user_id not in config.allowed_chat_ids:
                client.send_message(chat_id, "이 봇을 사용할 권한이 없습니다.")
                continue
            try:
                reply = handle_text(text, user_id, chat_id, store)
            except Exception as exc:  # noqa: BLE001 - user-facing bot error.
                reply = f"처리 중 문제가 생겼습니다: {exc}"
            client.send_message(chat_id, reply)

