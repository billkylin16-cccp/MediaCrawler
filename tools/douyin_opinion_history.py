# -*- coding: utf-8 -*-
"""Small local SQLite store for the desktop opinion-monitor workflow."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable


class OpinionHistory:
    """Persist runs and normalized report items without requiring a server."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    target_date TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    state TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 1,
                    output_path TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS items (
                    item_key TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    aweme_id TEXT NOT NULL DEFAULT '',
                    account TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    report_date TEXT NOT NULL,
                    keywords TEXT NOT NULL DEFAULT '[]',
                    source TEXT NOT NULL DEFAULT '',
                    risk_level TEXT NOT NULL DEFAULT '低',
                    risk_score INTEGER NOT NULL DEFAULT 0,
                    event_id TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL DEFAULT '',
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    seen_count INTEGER NOT NULL DEFAULT 1,
                    payload TEXT NOT NULL DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS idx_items_report_date
                    ON items(report_date);
                CREATE INDEX IF NOT EXISTS idx_items_event_id
                    ON items(event_id);
                CREATE INDEX IF NOT EXISTS idx_runs_target_date
                    ON runs(target_date, state);
                """
            )

    def start_run(
        self,
        run_id: str,
        target_date: str,
        output_path: str,
        attempt: int = 1,
    ) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runs(
                    run_id, target_date, started_at, state, attempt, output_path, error
                ) VALUES (?, ?, ?, 'running', ?, ?, '')
                ON CONFLICT(run_id) DO UPDATE SET
                    started_at=excluded.started_at,
                    finished_at=NULL,
                    state='running',
                    attempt=excluded.attempt,
                    output_path=excluded.output_path,
                    error=''
                """,
                (run_id, target_date, now, attempt, output_path),
            )

    def finish_run(self, run_id: str, state: str, error: str = "") -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE runs
                SET finished_at=?, state=?, error=?
                WHERE run_id=?
                """,
                (
                    datetime.now().isoformat(timespec="seconds"),
                    state,
                    error[:8000],
                    run_id,
                ),
            )

    def seen_keys(self) -> set[str]:
        with self._connect() as connection:
            return {str(row[0]) for row in connection.execute("SELECT item_key FROM items")}

    def save_items(self, items: Iterable[dict[str, Any]]) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        rows = []
        for item in items:
            rows.append(
                (
                    str(item["item_key"]),
                    str(item["kind"]),
                    str(item["item_id"]),
                    str(item.get("aweme_id") or ""),
                    str(item.get("account") or ""),
                    str(item.get("content") or ""),
                    str(item["created_at"]),
                    str(item["report_date"]),
                    json.dumps(item.get("keywords", []), ensure_ascii=False),
                    str(item.get("source") or ""),
                    str(item.get("risk_level") or "低"),
                    int(item.get("risk_score") or 0),
                    str(item.get("event_id") or ""),
                    str(item.get("summary") or ""),
                    str(item.get("url") or ""),
                    now,
                    now,
                    json.dumps(item.get("payload", {}), ensure_ascii=False),
                )
            )
        if not rows:
            return
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO items(
                    item_key, kind, item_id, aweme_id, account, content,
                    created_at, report_date, keywords, source, risk_level,
                    risk_score, event_id, summary, url, first_seen, last_seen, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_key) DO UPDATE SET
                    last_seen=excluded.last_seen,
                    seen_count=items.seen_count + 1,
                    risk_level=excluded.risk_level,
                    risk_score=excluded.risk_score,
                    event_id=excluded.event_id,
                    summary=excluded.summary,
                    payload=excluded.payload
                """,
                rows,
            )

    def successful_dates(self, start: date, end: date) -> set[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT target_date FROM runs
                WHERE state='completed' AND target_date BETWEEN ? AND ?
                """,
                (start.isoformat(), end.isoformat()),
            )
            return {str(row[0]) for row in rows}

    def missing_dates(self, end: date, days: int) -> list[date]:
        days = max(1, min(int(days), 31))
        start = end - timedelta(days=days - 1)
        completed = self.successful_dates(start, end)
        return [
            start + timedelta(days=offset)
            for offset in range(days)
            if (start + timedelta(days=offset)).isoformat() not in completed
        ]

    def dashboard_payload(self, days: int = 30) -> dict[str, Any]:
        cutoff = (date.today() - timedelta(days=max(1, days) - 1)).isoformat()
        with self._connect() as connection:
            daily = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT report_date,
                           SUM(CASE WHEN kind='video' THEN 1 ELSE 0 END) AS videos,
                           SUM(CASE WHEN kind='comment' THEN 1 ELSE 0 END) AS comments,
                           MAX(risk_score) AS max_risk
                    FROM items WHERE report_date >= ?
                    GROUP BY report_date ORDER BY report_date
                    """,
                    (cutoff,),
                )
            ]
            risk = {
                str(row[0]): int(row[1])
                for row in connection.execute(
                    "SELECT risk_level, COUNT(*) FROM items GROUP BY risk_level"
                )
            }
            recent = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT report_date, kind, account, content, risk_level,
                           risk_score, event_id, summary, url, payload
                    FROM items ORDER BY created_at DESC LIMIT 80
                    """
                )
            ]
            for row in recent:
                try:
                    item_payload = json.loads(str(row.pop("payload", "{}") or "{}"))
                except json.JSONDecodeError:
                    item_payload = {}
                row["location"] = "、".join(item_payload.get("locations", []))
            runs = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT target_date, state, attempt, started_at, finished_at, error
                    FROM runs ORDER BY started_at DESC LIMIT 20
                    """
                )
            ]
        return {"daily": daily, "risk": risk, "recent": recent, "runs": runs}
