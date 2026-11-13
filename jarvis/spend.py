"""Daily model-spend meter.

Records token usage per API request (from Hermes's post_api_request hook) and
estimates USD from the price table in jarvis.yaml. Used to alert at 80% of the
daily cap and to switch Jarvis to read-only at 100%.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import audit_db_path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS spend (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT NOT NULL,
    day      TEXT NOT NULL,                -- local date YYYY-MM-DD
    model    TEXT,
    input_tokens  INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read    INTEGER DEFAULT 0,
    cache_write   INTEGER DEFAULT 0,
    usd      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS spend_day ON spend(day);
"""

_lock = threading.Lock()


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0


def parse_usage(usage: dict[str, Any] | None) -> Usage:
    """Accept OpenAI-style and Anthropic-style usage dicts."""
    u = usage or {}
    details = u.get("prompt_tokens_details") or {}
    return Usage(
        input_tokens=int(u.get("prompt_tokens") or u.get("input_tokens") or 0),
        output_tokens=int(u.get("completion_tokens") or u.get("output_tokens") or 0),
        cache_read=int(u.get("cache_read_input_tokens") or details.get("cached_tokens") or 0),
        cache_write=int(u.get("cache_creation_input_tokens") or 0),
    )


def _price_for(model: str, prices: dict[str, Any]) -> dict[str, float]:
    """Look up by exact id, then by substring (so 'anthropic/claude-sonnet-5-5' matches)."""
    m = (model or "").lower()
    for key, p in prices.items():
        if key.lower() == m:
            return p
    for key, p in prices.items():
        if key.lower() in m:
            return p
    return {"input": 0.0, "output": 0.0}


def estimate_usd(model: str, usage: Usage, prices: dict[str, Any]) -> float:
    p = _price_for(model, prices)
    inp, out = float(p.get("input", 0)), float(p.get("output", 0))
    # Cache reads are ~10% of input price; cache writes ~125% (5-min) on Anthropic.
    cost = (usage.input_tokens * inp + usage.output_tokens * out
            + usage.cache_read * inp * 0.1 + usage.cache_write * inp * 1.25) / 1_000_000
    return round(cost, 6)


class SpendMeter:
    def __init__(self, cfg: dict[str, Any], path: Path | None = None):
        self.cfg = cfg
        self.spend_cfg = cfg.get("spend") or {}
        self.tz = ZoneInfo(cfg.get("timezone", "America/New_York"))
        self.path = path or audit_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        c.execute("PRAGMA journal_mode=WAL")
        return c

    def _day(self, now: datetime | None = None) -> str:
        return (now or datetime.now(timezone.utc)).astimezone(self.tz).strftime("%Y-%m-%d")

    def record(self, model: str, usage: dict[str, Any] | None, now: datetime | None = None) -> float:
        u = parse_usage(usage)
        usd = estimate_usd(model, u, self.spend_cfg.get("prices") or {})
        ts = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
        with _lock, self._conn() as c:
            c.execute(
                "INSERT INTO spend (ts, day, model, input_tokens, output_tokens, cache_read, cache_write, usd) VALUES (?,?,?,?,?,?,?,?)",
                (ts, self._day(now), model, u.input_tokens, u.output_tokens, u.cache_read, u.cache_write, usd),
            )
        return usd

    def today_usd(self, now: datetime | None = None) -> float:
        with self._conn() as c:
            row = c.execute("SELECT COALESCE(SUM(usd),0) FROM spend WHERE day=?", (self._day(now),)).fetchone()
            return float(row[0])

    def cap(self) -> float:
        return float(self.spend_cfg.get("daily_cap_usd", 0) or 0)

    def status(self, now: datetime | None = None) -> dict[str, Any]:
        """{'usd', 'cap', 'fraction', 'level'} where level is ok | warn | over."""
        usd, cap = self.today_usd(now), self.cap()
        frac = (usd / cap) if cap > 0 else 0.0
        alert = float(self.spend_cfg.get("alert_fraction", 0.8))
        level = "over" if cap > 0 and frac >= 1.0 else "warn" if cap > 0 and frac >= alert else "ok"
        return {"usd": round(usd, 4), "cap": cap, "fraction": round(frac, 3), "level": level}
