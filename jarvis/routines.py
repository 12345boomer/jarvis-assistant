"""Pre-run gates for the proactive cron jobs (Phase 6).

Each gate is cheap (one Google API call, no model) and prints the Hermes cron
protocol on its last stdout line: {"wakeAgent": false} to skip the model, or
{"wakeAgent": true, "context": {...}} with what the agent should act on.
State lives in ~/.hermes/jarvis-state/ so a restart never re-alerts.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .config import cached_config
from .quiet_hours import email_is_urgent, is_quiet, meeting_is_urgent

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
STATE_DIR = Path(os.environ.get("JARVIS_STATE_DIR") or HERMES_HOME / "jarvis-state")
GAPI = HERMES_HOME / "skills" / "jarvis-google" / "scripts" / "gapi"


# ---- state -----------------------------------------------------------------

def load_state(name: str) -> dict[str, Any]:
    p = STATE_DIR / f"{name}.json"
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def save_state(name: str, state: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / f"{name}.json").write_text(json.dumps(state, indent=2))


def _prune(seen: dict[str, str], keep_days: int, now: datetime) -> dict[str, str]:
    cutoff = (now - timedelta(days=keep_days)).isoformat()
    return {k: v for k, v in seen.items() if v >= cutoff}


# ---- google calls (thin, replaceable in tests) -----------------------------

def gapi(*args: str, timeout: int = 60) -> Any:
    out = subprocess.run([str(GAPI), *args], capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip()[-300:] or f"gapi exit {out.returncode}")
    return json.loads(out.stdout or "[]")


def gmail_search(query: str, max_results: int = 20) -> list[dict[str, Any]]:
    return gapi("gmail", "search", query, "--max", str(max_results))


def calendar_list(start: datetime, end: datetime) -> list[dict[str, Any]]:
    return gapi("calendar", "list", "--start", start.isoformat(), "--end", end.isoformat())


# ---- inbox watch -----------------------------------------------------------

def _vip_query(vips: list[str]) -> str:
    parts = []
    for v in vips:
        v = v.strip()
        if not v:
            continue
        parts.append(f"from:{v.lstrip('@')}")  # "@domain.com" -> from:domain.com matches the domain
    return "newer_than:1d (" + " OR ".join(parts) + ")" if parts else ""


def inbox_check(now: datetime, cfg: dict[str, Any], state: dict[str, Any],
                search: Callable[[str, int], list[dict[str, Any]]] = gmail_search) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (cron_result, new_state). Wakes the agent only for unseen VIP mail."""
    vips = (cfg.get("urgent") or {}).get("vip_senders") or []
    query = _vip_query(vips)
    seen = _prune(state.get("seen") or {}, keep_days=3, now=now)
    if not query:
        return {"wakeAgent": False, "note": "no vip_senders configured"}, {"seen": seen, "last_run": now.isoformat()}
    new_items = []
    for m in search(query, 20):
        mid = m.get("id")
        if not mid or mid in seen:
            continue
        seen[mid] = now.isoformat()
        urgent = email_is_urgent(m.get("from", ""), m.get("subject", ""), m.get("snippet", ""), cfg)
        new_items.append({"id": mid, "from": m.get("from", ""), "subject": m.get("subject", ""),
                          "snippet": (m.get("snippet") or "")[:200], "date": m.get("date", ""), "urgent": urgent})
    new_state = {"seen": seen, "last_run": now.isoformat()}
    if not new_items:
        return {"wakeAgent": False}, new_state
    quiet = is_quiet(now, cfg)
    any_urgent = any(i["urgent"] for i in new_items)
    if quiet and not any_urgent:
        # Not urgent, Ben is asleep: hold it for the morning brief (it stays "seen" so we don't re-alert).
        held = state.get("held") or []
        held.extend(new_items)
        new_state["held"] = held[-50:]
        return {"wakeAgent": False, "note": "held for morning brief (quiet hours)"}, new_state
    ctx = {"new_vip_mail": new_items, "any_urgent": any_urgent, "quiet_hours": quiet,
           "may_call": any_urgent, "held_from_overnight": state.get("held") or []}
    new_state["held"] = []
    return {"wakeAgent": True, "context": ctx}, new_state


# ---- meeting prep ----------------------------------------------------------

def _parse_dt(s: str, tz: ZoneInfo) -> datetime | None:
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=tz)
    except Exception:
        return None


def meeting_check(now: datetime, cfg: dict[str, Any], state: dict[str, Any],
                  list_events: Callable[[datetime, datetime], list[dict[str, Any]]] = calendar_list,
                  lead_min: int = 20, lead_max: int = 45) -> tuple[dict[str, Any], dict[str, Any]]:
    """Wake the agent for events starting in [lead_min, lead_max] minutes that haven't been prepped."""
    tz = ZoneInfo(cfg.get("timezone", "America/New_York"))
    now = now.astimezone(tz)
    prepped = _prune(state.get("prepped") or {}, keep_days=2, now=now)
    events = list_events(now, now + timedelta(minutes=lead_max + 5))
    due, conflicts = [], []
    starts = []
    for e in events:
        st = _parse_dt(e.get("start", ""), tz)
        if not st:
            continue
        starts.append((st, e))
    for st, e in starts:
        eid = e.get("id", "")
        mins = (st - now).total_seconds() / 60
        if eid in prepped or not (lead_min <= mins <= lead_max):
            continue
        en = _parse_dt(e.get("end", ""), tz) or st
        overlap = [o for so, o in starts if o.get("id") != eid and so < en and (_parse_dt(o.get("end", ""), tz) or so) > st]
        prepped[eid] = now.isoformat()
        item = {"id": eid, "summary": e.get("summary", ""), "start": st.isoformat(), "end": en.isoformat(),
                "location": e.get("location", ""), "attendees": e.get("attendees", []), "description": (e.get("description") or "")[:500],
                "minutes_until": round(mins), "conflicts_with": [o.get("summary", "") for o in overlap]}
        due.append(item)
        if overlap and meeting_is_urgent(st, True, now, cfg):
            conflicts.append(item)
    new_state = {"prepped": prepped, "last_run": now.isoformat()}
    if not due:
        return {"wakeAgent": False}, new_state
    return {"wakeAgent": True, "context": {"meetings": due, "urgent_conflicts": conflicts, "quiet_hours": is_quiet(now, cfg)}}, new_state


# ---- entry point used by the cron stub scripts ------------------------------

def run(kind: str, now: datetime | None = None) -> int:
    cfg = cached_config()
    now = now or datetime.now(timezone.utc)
    try:
        # Pass the Google functions explicitly (looked up at call time, so tests can monkeypatch them).
        if kind == "inbox":
            result, new_state = inbox_check(now, cfg, load_state("inbox"), search=gmail_search)
        elif kind == "meetings":
            result, new_state = meeting_check(now, cfg, load_state("meetings"), list_events=calendar_list)
        else:
            print(json.dumps({"wakeAgent": False, "error": f"unknown routine {kind}"})); return 2
        save_state(kind, new_state)
    except Exception as e:  # never wake the model on our own bug; log it instead
        print(json.dumps({"wakeAgent": False, "error": str(e)[:300]}))
        return 0
    print(json.dumps(result))
    return 0
