"""Backup call when Claude Code has waited on Ben for too long.

scripts/cc_hook.py (Claude Code hooks, every session on this Mac) keeps one file per waiting session in
~/.hermes/jarvis-state/cc-pending/. This check runs every 5 minutes (no-agent Hermes cron job,
scripts/cron/jarvis-cc-backup.py) and, for a prompt older than cc_backup.delay_minutes, calls Ben once:
"Claude Code has been waiting on you for 30 minutes in DailyBuilt. It's asking: allow npm install?"

Rules: one call per waiting prompt, max_calls_per_day calls (then Telegram), quiet hours respected (a prompt that
comes due overnight is called at 7:00), and a call Ben doesn't pick up becomes a Telegram message. Alert only:
Ben approves in the Claude app.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .audit import AuditLog
from .config import cached_config
from .quiet_hours import is_quiet

STALE_AFTER_S = 12 * 3600        # a "pending" item this old is a session that died without SessionEnd
TRANSCRIPT_GRACE_S = 60          # transcript written this long after the prompt = the session moved on


def pending_dir() -> Path:
    from .routines import STATE_DIR
    return STATE_DIR / "cc-pending"


def cc_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    return {"enabled": True, "delay_minutes": 30, "max_calls_per_day": 3, **(cfg.get("cc_backup") or {})}


def load_pending(d: Path | None = None) -> list[dict[str, Any]]:
    d = d or pending_dir()
    out = []
    for p in sorted(d.glob("*.json")) if d.exists() else []:
        if p.name.endswith(".request.json"):
            continue
        try:
            rec = json.loads(p.read_text())
            rec["_path"] = str(p)
            out.append(rec)
        except Exception:
            continue
    return out


def _project(cwd: str) -> str:
    name = Path(cwd or "").name
    return name if name and Path(cwd) != Path.home() else "your home folder"


def _age_words(minutes: float) -> str:
    if minutes < 90:
        return f"{int(round(minutes))} minutes"
    return f"about {int(round(minutes / 60))} hours"


def reason_for(items: list[dict[str, Any]], now_ts: float) -> str:
    """The spoken reason. One item: project + what it asks. Several: how many and where."""
    first = items[0]
    mins = (now_ts - float(first["ts"])) / 60
    if len(items) == 1:
        ask = (first.get("ask") or "").strip().rstrip("?.")
        return (f"Claude Code has been waiting on you for {_age_words(mins)} in {_project(first.get('cwd', ''))}."
                + (f" It's asking: {ask}?" if ask else ""))
    where = ", ".join(dict.fromkeys(_project(i.get("cwd", "")) for i in items))
    return f"{len(items)} Claude Code sessions have been waiting on you, the oldest for {_age_words(mins)}: {where}."


def moved_on(rec: dict[str, Any]) -> bool:
    """Safety net for a missed clear hook: the session's transcript kept growing after the prompt."""
    tp = rec.get("transcript_path") or ""
    try:
        return bool(tp) and Path(tp).stat().st_mtime > float(rec["ts"]) + TRANSCRIPT_GRACE_S
    except OSError:
        return False


def plan(pending: list[dict[str, Any]], state: dict[str, Any], cfg: dict[str, Any], now: datetime,
         is_moved_on=moved_on) -> dict[str, Any]:
    """Pure decision. Returns {'action': 'none'|'wait_quiet'|'call'|'telegram', 'items': [...], 'drop': [...], 'reason': str}."""
    c = cc_cfg(cfg)
    now_ts = now.timestamp()
    handled = state.get("handled") or {}
    drop, due = [], []
    for rec in pending:
        age = now_ts - float(rec.get("ts", now_ts))
        if age > STALE_AFTER_S or is_moved_on(rec):
            drop.append(rec)
            continue
        key = f"{rec.get('session_id')}:{rec.get('ts')}"
        if key in handled or age < float(c["delay_minutes"]) * 60:
            continue
        due.append({**rec, "_key": key})
    if not c["enabled"] or not due:
        return {"action": "none", "items": [], "drop": drop, "reason": ""}
    if is_quiet(now, cfg):
        return {"action": "wait_quiet", "items": due, "drop": drop, "reason": ""}
    due.sort(key=lambda r: float(r["ts"]))
    day = now.astimezone(ZoneInfo(cfg.get("timezone", "America/New_York"))).date().isoformat()
    over_cap = int((state.get("calls") or {}).get(day, 0)) >= int(c["max_calls_per_day"])
    return {"action": "telegram" if over_cap else "call", "items": due, "drop": drop, "reason": reason_for(due, now_ts),
            "day": day, "over_cap": over_cap}


def run(now: datetime | None = None, *, env: dict[str, str] | None = None, cfg: dict[str, Any] | None = None,
        dial=None, wait=None, send=None, max_wait_s: float = 240) -> dict[str, Any]:
    from .calls import CallRequest, load_env, place_call, wait_for_outcome
    from .notify import telegram
    from .routines import load_state, save_state
    cfg = cfg or cached_config()
    now = now or datetime.now(timezone.utc)
    env = env if env is not None else load_env()
    dial, send = dial or place_call, send or (lambda text, subject: telegram(text, subject, env=env))
    wait = wait or (lambda placed, e: wait_for_outcome(placed, e, max_wait_s=max_wait_s, poll_s=15))
    state = load_state("cc_backup")
    p = plan(load_pending(), state, cfg, now)
    for rec in p["drop"]:
        Path(rec["_path"]).unlink(missing_ok=True)
        Path(rec["_path"].replace(".json", ".request.json")).unlink(missing_ok=True)
    if p["action"] in ("none", "wait_quiet"):
        return {"status": p["action"], "pending": len(p["items"]), "dropped": len(p["drop"])}
    # Mark handled BEFORE dialing: a crash or a slow call can never lead to a second call for the same prompt.
    handled = {k: v for k, v in (state.get("handled") or {}).items() if v > (now - timedelta(days=2)).isoformat()}
    for rec in p["items"]:
        handled[rec["_key"]] = now.isoformat()
    calls = {k: v for k, v in (state.get("calls") or {}).items() if k >= (now - timedelta(days=7)).date().isoformat()}
    audit = AuditLog()
    if p["action"] == "telegram":
        save_state("cc_backup", {**state, "handled": handled, "calls": calls})
        ok = send(p["reason"], "Claude Code is waiting on you (daily call cap reached)")
        audit.event("cc_backup_telegram", f"cap reached: {p['reason'][:200]}")
        return {"status": "telegram", "reason": p["reason"], "sent": ok}
    calls[p["day"]] = int(calls.get(p["day"], 0)) + 1
    save_state("cc_backup", {**state, "handled": handled, "calls": calls})
    to = env.get("BEN_PHONE_NUMBER", "")
    placed = dial(CallRequest(to_number=to, reason=p["reason"], variables={"call_type": "cc_backup"}), env=env, cfg=cfg, now=now) \
        if to else {"status": "error", "why": "BEN_PHONE_NUMBER not set"}
    outcome = wait(placed, env) if placed.get("status") == "placed" else {"answered": False, "why": placed.get("why", "not placed")}
    audit.event("cc_backup_call", f"{'answered' if outcome.get('answered') else 'missed'} ({outcome.get('why')}): {p['reason'][:200]}")
    if outcome.get("answered"):
        return {"status": "answered", "reason": p["reason"], "outcome": outcome}
    ok = send(p["reason"], f"Claude Code is waiting on you (call didn't connect: {outcome.get('why')})")
    return {"status": "telegram", "reason": p["reason"], "outcome": outcome, "sent": ok}


def status() -> dict[str, Any]:
    from .routines import load_state
    now = time.time()
    items = [{"project": _project(r.get("cwd", "")), "ask": r.get("ask"), "type": r.get("type"),
              "waiting_min": round((now - float(r.get("ts", now))) / 60, 1)} for r in load_pending()]
    return {"pending": items, "state": load_state("cc_backup"), "config": cc_cfg(cached_config())}
