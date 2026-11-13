"""Append-only audit log in SQLite: every tool call, its tier, the decision, and who approved.

One row per tool call. `record_call` inserts when the pre-hook fires; `finish_call`
fills in duration/outcome from the post-hook; `record_approval` logs the human's answer.
Secret-looking argument values are redacted before they are stored.
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import audit_db_path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    session_id    TEXT,
    task_id       TEXT,
    tool_call_id  TEXT,
    platform      TEXT,
    tool          TEXT NOT NULL,
    args_summary  TEXT,
    tier          INTEGER NOT NULL,
    decision      TEXT NOT NULL,          -- allow | ask | block
    rule          TEXT,
    reason        TEXT,
    approver      TEXT,                   -- ben:telegram | ben:dashboard | ben:voice | none
    approval      TEXT,                   -- once | session | always | deny | timeout | ...
    duration_ms   INTEGER,
    ok            INTEGER                 -- 1 tool returned without error, 0 error, NULL unknown
);
CREATE INDEX IF NOT EXISTS audit_ts ON audit(ts);
CREATE INDEX IF NOT EXISTS audit_session ON audit(session_id);

CREATE TABLE IF NOT EXISTS events (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT NOT NULL,
    kind     TEXT NOT NULL,               -- spend_alert | quiet_hours_suppressed | ...
    detail   TEXT
);
"""

_SECRET_KEY_RX = re.compile(r"(token|secret|password|passwd|api_?key|authorization|cookie|credential)", re.I)
_SECRET_VALUE_RX = re.compile(r"\b(sk-ant-[A-Za-z0-9_-]{8,}|sk-[A-Za-z0-9]{20,}|\d{8,10}:[A-Za-z0-9_-]{30,}|xox[baprs]-[A-Za-z0-9-]{10,})\b")

_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def redact(obj: Any) -> Any:
    """Replace secret-looking keys/values so they never land in the log."""
    if isinstance(obj, dict):
        return {k: ("[redacted]" if _SECRET_KEY_RX.search(str(k)) else redact(v)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact(v) for v in obj]
    if isinstance(obj, str):
        return _SECRET_VALUE_RX.sub("[redacted]", obj)
    return obj


def summarize_args(args: dict[str, Any] | None, limit: int = 500) -> str:
    try:
        s = json.dumps(redact(args or {}), ensure_ascii=False, default=str)
    except Exception:
        s = str(args)
    return s if len(s) <= limit else s[: limit - 1] + "…"


class AuditLog:
    def __init__(self, path: Path | None = None):
        self.path = path or audit_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._conn()) as c, c:
            c.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        c.execute("PRAGMA journal_mode=WAL")
        c.row_factory = sqlite3.Row
        return c

    def record_call(self, *, tool: str, args: dict[str, Any] | None, tier: int, decision: str,
                    rule: str = "", reason: str = "", session_id: str = "", task_id: str = "",
                    tool_call_id: str = "", platform: str = "") -> int:
        with _lock, closing(self._conn()) as c, c:
            cur = c.execute(
                "INSERT INTO audit (ts, session_id, task_id, tool_call_id, platform, tool, args_summary, tier, decision, rule, reason)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (_now(), session_id, task_id, tool_call_id, platform, tool, summarize_args(args), tier, decision, rule, reason),
            )
            return int(cur.lastrowid)

    def finish_call(self, *, session_id: str, tool_call_id: str, tool: str, duration_ms: int | None, ok: bool | None) -> None:
        """Attach outcome to the most recent matching open row."""
        with _lock, closing(self._conn()) as c, c:
            row = c.execute(
                "SELECT id FROM audit WHERE session_id=? AND tool=? AND duration_ms IS NULL"
                " AND (tool_call_id=? OR tool_call_id='' OR ?='') ORDER BY id DESC LIMIT 1",
                (session_id, tool, tool_call_id, tool_call_id),
            ).fetchone()
            if row:
                c.execute("UPDATE audit SET duration_ms=?, ok=? WHERE id=?",
                          (duration_ms, None if ok is None else int(ok), row["id"]))

    def record_approval(self, *, session_id: str, choice: str, approver: str) -> None:
        """Log the human's answer against the latest 'ask' row for that session."""
        with _lock, closing(self._conn()) as c, c:
            row = c.execute(
                "SELECT id FROM audit WHERE session_id=? AND decision='ask' AND approval IS NULL ORDER BY id DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            if row:
                c.execute("UPDATE audit SET approval=?, approver=? WHERE id=?", (choice, approver, row["id"]))

    def event(self, kind: str, detail: str = "") -> None:
        with _lock, closing(self._conn()) as c, c:
            c.execute("INSERT INTO events (ts, kind, detail) VALUES (?,?,?)", (_now(), kind, detail))

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with closing(self._conn()) as c, c:
            rows = c.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]

    def recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        with closing(self._conn()) as c, c:
            rows = c.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]
