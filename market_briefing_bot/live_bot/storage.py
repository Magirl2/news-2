from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .alerts import Alert


SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    condition_type TEXT NOT NULL,
    target_price REAL NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    triggered_at TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    last_checked_price REAL
);
CREATE INDEX IF NOT EXISTS idx_alerts_active ON alerts(active, symbol);
CREATE UNIQUE INDEX IF NOT EXISTS idx_alerts_unique_active
ON alerts(telegram_user_id, symbol, condition_type, target_price)
WHERE active = 1;
"""


def utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AlertStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with closing(self._connect()) as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    def add_alert(self, alert: Alert) -> Alert:
        with closing(self._connect()) as conn:
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO alerts
                    (telegram_user_id, chat_id, symbol, condition_type, target_price, note, created_at, active)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        alert.telegram_user_id,
                        alert.chat_id,
                        alert.symbol.upper(),
                        alert.condition_type,
                        alert.target_price,
                        alert.note,
                        alert.created_at or utc_now_text(),
                    ),
                )
            except sqlite3.IntegrityError:
                existing = self.find_active_duplicate(
                    alert.telegram_user_id, alert.symbol, alert.condition_type, alert.target_price
                )
                if existing:
                    return existing
                raise
            conn.commit()
            return Alert(
                id=int(cursor.lastrowid),
                telegram_user_id=alert.telegram_user_id,
                chat_id=alert.chat_id,
                symbol=alert.symbol.upper(),
                condition_type=alert.condition_type,
                target_price=alert.target_price,
                note=alert.note,
                created_at=alert.created_at or utc_now_text(),
            )

    def find_active_duplicate(
        self, telegram_user_id: str, symbol: str, condition_type: str, target_price: float
    ) -> Alert | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT * FROM alerts
                WHERE telegram_user_id = ? AND symbol = ? AND condition_type = ?
                  AND target_price = ? AND active = 1
                """,
                (telegram_user_id, symbol.upper(), condition_type, target_price),
            ).fetchone()
        return _row_to_alert(row) if row else None

    def list_alerts(self, telegram_user_id: str | None = None, *, active_only: bool = True) -> list[Alert]:
        sql = "SELECT * FROM alerts WHERE 1=1"
        params: list[object] = []
        if telegram_user_id is not None:
            sql += " AND telegram_user_id = ?"
            params.append(telegram_user_id)
        if active_only:
            sql += " AND active = 1"
        sql += " ORDER BY id ASC"
        with closing(self._connect()) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_alert(row) for row in rows]

    def delete_alerts(self, telegram_user_id: str, target: str) -> int:
        target = target.strip().upper()
        if not target:
            return 0
        with closing(self._connect()) as conn:
            if target.isdigit():
                cursor = conn.execute(
                    "UPDATE alerts SET active = 0 WHERE telegram_user_id = ? AND id = ? AND active = 1",
                    (telegram_user_id, int(target)),
                )
            else:
                cursor = conn.execute(
                    "UPDATE alerts SET active = 0 WHERE telegram_user_id = ? AND symbol = ? AND active = 1",
                    (telegram_user_id, target),
                )
            count = cursor.rowcount
            conn.commit()
            return count

    def delete_all_alerts(self, telegram_user_id: str) -> int:
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                "UPDATE alerts SET active = 0 WHERE telegram_user_id = ? AND active = 1",
                (telegram_user_id,),
            )
            count = cursor.rowcount
            conn.commit()
            return count

    def mark_triggered(self, alert_id: int, current_price: float) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """
                UPDATE alerts
                SET active = 0, triggered_at = ?, last_checked_price = ?
                WHERE id = ?
                """,
                (utc_now_text(), current_price, alert_id),
            )
            conn.commit()

    def update_last_checked(self, alert_id: int, current_price: float) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE alerts SET last_checked_price = ? WHERE id = ?",
                (current_price, alert_id),
            )
            conn.commit()


def _row_to_alert(row: sqlite3.Row) -> Alert:
    return Alert(
        id=int(row["id"]),
        telegram_user_id=str(row["telegram_user_id"]),
        chat_id=str(row["chat_id"]),
        symbol=str(row["symbol"]),
        condition_type=str(row["condition_type"]),
        target_price=float(row["target_price"]),
        note=str(row["note"] or ""),
        created_at=str(row["created_at"] or ""),
        triggered_at=row["triggered_at"],
        active=bool(row["active"]),
        last_checked_price=(
            float(row["last_checked_price"]) if row["last_checked_price"] is not None else None
        ),
    )
