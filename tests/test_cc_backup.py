import importlib.util
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from jarvis import cc_backup as cc

ET = ZoneInfo("America/New_York")
REPO = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 26, 14, 0, tzinfo=ET)


@pytest.fixture
def cfg(cfg):
    """Pin the rules the tests assume, whatever config/jarvis.yaml says today (e.g. a 1-minute delay during a live test)."""
    return {**cfg, "cc_backup": {**(cfg.get("cc_backup") or {}), "enabled": True, "delay_minutes": 30, "max_calls_per_day": 3}}


@pytest.fixture
def hook(tmp_path, monkeypatch):
    """The real hook script (scripts/cc_hook.py), pointed at a temp pending dir."""
    pending = tmp_path / "state" / "cc-pending"
    monkeypatch.setenv("JARVIS_CC_PENDING", str(pending))
    spec = importlib.util.spec_from_file_location("cc_hook", REPO / "scripts" / "cc_hook.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr("jarvis.routines.STATE_DIR", tmp_path / "state")
    return mod


def ev(name, **kw):
    return {"hook_event_name": name, "session_id": "s1", "cwd": "/Users/ben/Projects/DailyBuilt", "transcript_path": "", **kw}


# ---- hook: pending / cleared ---------------------------------------------------------------------------------

def test_permission_prompt_records_with_the_actual_ask(hook):
    t = NOW.timestamp()
    hook.handle(ev("PermissionRequest", tool_name="Bash", tool_input={"command": "npm install", "description": "Install npm packages"}), now=t)
    assert hook.handle(ev("Notification", notification_type="permission_prompt", message="Claude needs your permission"), now=t + 6) == "recorded"
    rec = cc.load_pending()[0]
    assert rec["ask"] == "allow install npm packages" and rec["tool"] == "Bash" and rec["ts"] == t + 6


def test_repeat_notification_keeps_original_start(hook):
    t = NOW.timestamp()
    hook.handle(ev("Notification", notification_type="permission_prompt", message="m"), now=t)
    hook.handle(ev("Notification", notification_type="permission_prompt", message="m"), now=t + 600)
    assert cc.load_pending()[0]["ts"] == t


def test_idle_prompt_is_not_pending(hook):
    assert hook.handle(ev("Notification", notification_type="idle_prompt", message="waiting")) == "ignored"
    assert cc.load_pending() == []


@pytest.mark.parametrize("clear", ["PostToolBatch", "UserPromptSubmit", "Stop", "SessionEnd"])
def test_session_moving_on_clears(hook, clear):
    hook.handle(ev("Notification", notification_type="elicitation_dialog", message="MCP needs input"))
    assert hook.handle(ev(clear)) == "cleared" and cc.load_pending() == []


def test_post_tool_use_clears_only_the_waiting_tool(hook):
    hook.handle(ev("PermissionRequest", tool_name="Bash", tool_input={"command": "rm -rf build"}))
    hook.handle(ev("Notification", notification_type="permission_prompt", message="m"))
    assert hook.handle(ev("PostToolUse", tool_name="Read")) == "kept"          # a parallel tool finishing
    assert len(cc.load_pending()) == 1
    assert hook.handle(ev("PostToolUse", tool_name="Bash")) == "cleared"
    assert cc.load_pending() == []


def test_ask_user_question_records_the_question(hook):
    hook.handle(ev("PreToolUse", tool_name="AskUserQuestion", tool_input={"questions": [{"question": "Which account should I push to?"}]}))
    assert cc.load_pending()[0]["ask"] == "Which account should I push to?"
    hook.handle(ev("PostToolUse", tool_name="AskUserQuestion"))
    assert cc.load_pending() == []


def test_hook_never_raises_on_garbage(hook, monkeypatch):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert hook.main() == 0


# ---- checker: 30-minute trigger, no double calls, cap, quiet hours ----------------------------------------------

def rec(age_min, sid="s1", ask="allow running npm install", cwd="/Users/ben/Projects/DailyBuilt", now=NOW):
    return {"session_id": sid, "cwd": cwd, "ask": ask, "ts": (now - timedelta(minutes=age_min)).timestamp(), "transcript_path": ""}


def test_not_due_before_delay(cfg):
    assert cc.plan([rec(29)], {}, cfg, NOW)["action"] == "none"


def test_due_after_delay_with_plain_reason(cfg):
    p = cc.plan([rec(31)], {}, cfg, NOW)
    assert p["action"] == "call"
    assert p["reason"] == "Claude Code has been waiting on you for 31 minutes in DailyBuilt. It's asking: allow running npm install?"


def test_handled_prompt_is_never_called_twice(cfg):
    r = rec(31)
    state = {"handled": {f"{r['session_id']}:{r['ts']}": NOW.isoformat()}}
    assert cc.plan([r], state, cfg, NOW)["action"] == "none"


def test_daily_cap_switches_to_telegram(cfg):
    p = cc.plan([rec(40)], {"calls": {"2026-10-26": 3}}, cfg, NOW)
    assert p["action"] == "telegram" and p["over_cap"]


def test_quiet_hours_wait_then_call_at_seven(cfg):
    night = datetime(2026, 10, 27, 2, 0, tzinfo=ET)
    r = rec(60, now=night)
    assert cc.plan([r], {}, cfg, night)["action"] == "wait_quiet"
    seven = datetime(2026, 10, 27, 7, 0, tzinfo=ET)
    p = cc.plan([r], {}, cfg, seven)
    assert p["action"] == "call" and "about 6 hours" in p["reason"]


def test_disabled_does_nothing(cfg):
    assert cc.plan([rec(60)], {}, {**cfg, "cc_backup": {"enabled": False}}, NOW)["action"] == "none"


def test_stale_and_moved_on_items_are_dropped(cfg):
    p = cc.plan([rec(13 * 60, sid="dead"), rec(45, sid="moved")], {}, cfg, NOW, is_moved_on=lambda r: r["session_id"] == "moved")
    assert {r["session_id"] for r in p["drop"]} == {"dead", "moved"} and p["action"] == "none"


def test_moved_on_reads_transcript_mtime(tmp_path):
    tp = tmp_path / "t.jsonl"; tp.write_text("x")
    t = tp.stat().st_mtime
    assert cc.moved_on({"transcript_path": str(tp), "ts": t - 120})
    assert not cc.moved_on({"transcript_path": str(tp), "ts": t - 10})


def test_several_waiting_sessions_make_one_call(cfg):
    p = cc.plan([rec(50, sid="a"), rec(35, sid="b", cwd="/Users/ben/Documents")], {}, cfg, NOW)
    assert p["action"] == "call" and len(p["items"]) == 2
    assert p["reason"].startswith("2 Claude Code sessions have been waiting on you, the oldest for 50 minutes")


# ---- run(): end to end with fakes -----------------------------------------------------------------------------------

def write_pending(tmp_path, r):
    d = tmp_path / "state" / "cc-pending"; d.mkdir(parents=True, exist_ok=True)
    (d / f"{r['session_id']}.json").write_text(json.dumps(r))


def test_run_calls_once_and_counts(cfg, hook, tmp_path):
    write_pending(tmp_path, rec(31))
    dials, sent = [], []
    kw = dict(env={"BEN_PHONE_NUMBER": "+18605550100"}, cfg=cfg, send=lambda t, s: sent.append(t) or True,
              dial=lambda req, **k: dials.append(req) or {"status": "placed", "response": {"conversation_id": "c"}},
              wait=lambda p, e: {"answered": True, "why": "1 turn"})
    assert cc.run(NOW, **kw)["status"] == "answered"
    assert cc.run(NOW + timedelta(minutes=5), **kw)["status"] == "none"        # same prompt: no second call
    assert len(dials) == 1 and not sent
    assert dials[0].reason.startswith("Claude Code has been waiting on you") and dials[0].variables == {"call_type": "cc_backup"}
    from jarvis.routines import load_state
    assert load_state("cc_backup")["calls"] == {"2026-10-26": 1}


def test_run_missed_call_goes_to_telegram(cfg, hook, tmp_path):
    write_pending(tmp_path, rec(31))
    sent = []
    r = cc.run(NOW, env={"BEN_PHONE_NUMBER": "+1860"}, cfg=cfg, send=lambda t, s: sent.append(s) or True,
               dial=lambda req, **k: {"status": "placed", "response": {"conversation_id": "c"}},
               wait=lambda p, e: {"answered": False, "why": "no-answer"})
    assert r["status"] == "telegram" and "no-answer" in sent[0]


def test_run_respects_cap(cfg, hook, tmp_path):
    from jarvis.routines import save_state
    save_state("cc_backup", {"calls": {"2026-10-26": 3}})
    write_pending(tmp_path, rec(31))
    dials, sent = [], []
    r = cc.run(NOW, env={"BEN_PHONE_NUMBER": "+1860"}, cfg=cfg, send=lambda t, s: sent.append(t) or True,
               dial=lambda req, **k: dials.append(1) or {"status": "placed"})
    assert r["status"] == "telegram" and not dials and sent


# ---- installer ---------------------------------------------------------------------------------------------------

def test_install_merge_keeps_existing_settings_and_is_idempotent():
    spec = importlib.util.spec_from_file_location("inst", REPO / "scripts" / "install_cc_hooks.py")
    inst = importlib.util.module_from_spec(spec); spec.loader.exec_module(inst)
    mine = {"type": "command", "command": "/Users/ben/bin/lint.sh"}
    settings = {"model": "opus", "permissions": {"allow": ["Read"]}, "hooks": {"PostToolUse": [{"matcher": "Edit", "hooks": [mine]}]}}
    once = inst.merge(json.loads(json.dumps(settings)))
    twice = inst.merge(json.loads(json.dumps(once)))
    assert once == twice and inst.installed(once)
    assert once["model"] == "opus" and once["permissions"] == {"allow": ["Read"]}
    assert {"matcher": "Edit", "hooks": [mine]} in once["hooks"]["PostToolUse"]
    assert all(h.get("async") for g in once["hooks"]["Notification"] for h in g["hooks"])
    removed = inst.strip(json.loads(json.dumps(once)))
    assert removed["hooks"] == {"PostToolUse": [{"matcher": "Edit", "hooks": [mine]}]}

