#!/usr/bin/env python3
"""Claude Code hook: tracks prompts that are waiting on Ben, for the backup call (jarvis/cc_backup.py).

Wired into ~/.claude/settings.json by `make cc-backup-install`, so it runs for every Claude Code session on this
Mac, including Remote Control sessions started from the phone. Copied to ~/.hermes/jarvis-cc/ (outside ~/Documents)
by `make link`. Stdlib only, Python 3.9+ (/usr/bin/python3). It reads the hook JSON on stdin, writes or deletes one
small file, and exits 0 within milliseconds. It never prints, never blocks, and never decides anything for Claude.

  Notification (permission_prompt | elicitation_dialog | agent_needs_input)  -> record pending
  PermissionRequest                                     -> remember what the prompt asks (tool + command)
  PreToolUse AskUserQuestion                            -> record pending (Claude asked Ben a question)
  PostToolUse / PostToolUseFailure (same tool)          -> clear
  PostToolBatch (whole batch resolved, denials too)     -> clear
  UserPromptSubmit / Stop / SessionEnd                  -> clear
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

PENDING = Path(os.environ.get("JARVIS_CC_PENDING") or Path.home() / ".hermes" / "jarvis-state" / "cc-pending")
WAITING_TYPES = {"permission_prompt", "elicitation_dialog", "elicitation_url_dialog", "agent_needs_input"}
REQUEST_FRESH_S = 300   # a PermissionRequest this recent describes the prompt the Notification is about


def _safe_id(sid: str) -> str:
    return "".join(ch for ch in (sid or "unknown") if ch.isalnum() or ch in "-_")[:80] or "unknown"


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _short(text: str, words: int = 10) -> str:
    w = " ".join(str(text or "").split()).split(" ")
    return " ".join(w[:words]) + ("..." if len(w) > words else "")


def describe(tool: str, tool_input: dict) -> str:
    """Plain words for what Claude wants to do, e.g. 'allow running npm install'."""
    ti = tool_input or {}
    if tool == "Bash":
        return "allow " + (_short(ti.get("description"), 10).rstrip(".").lower() if ti.get("description") else "running " + _short(ti.get("command"), 8))
    if tool in ("Edit", "Write", "NotebookEdit", "MultiEdit"):
        return f"allow editing {Path(str(ti.get('file_path') or ti.get('notebook_path') or 'a file')).name}"
    if tool in ("WebFetch", "WebSearch"):
        target = str(ti.get("url") or ti.get("query") or "")
        return f"allow {'fetching' if tool == 'WebFetch' else 'searching'} {_short(target.split('//')[-1].split('/')[0] if tool == 'WebFetch' else target, 6)}"
    if tool == "AskUserQuestion":
        qs = ti.get("questions") or []
        return _short((qs[0] or {}).get("question") if qs else "a question for you", 16)
    if tool == "ExitPlanMode":
        return "approve its plan"
    if tool.startswith("mcp__"):
        parts = tool.split("__")
        return f"allow {parts[1].replace('claude_ai_', '').replace('_', ' ')} {parts[-1].replace('_', ' ')}"
    return f"allow {tool}"


def handle(ev: dict, now: float | None = None) -> str:
    """Returns what it did (for tests): recorded | noted | cleared | kept | ignored."""
    now = now or time.time()
    name = ev.get("hook_event_name", "")
    sid = _safe_id(ev.get("session_id", ""))
    pending, request = PENDING / f"{sid}.json", PENDING / f"{sid}.request.json"
    if name == "PermissionRequest":
        _write(request, {"tool": ev.get("tool_name", ""), "ask": describe(ev.get("tool_name", ""), ev.get("tool_input") or {}), "ts": now})
        return "noted"
    if name == "Notification" and ev.get("notification_type") in WAITING_TYPES:
        cur, req = _read(pending), _read(request)
        fresh = req if req and now - float(req.get("ts", 0)) < REQUEST_FRESH_S else {}
        rec = {"session_id": ev.get("session_id", ""), "cwd": ev.get("cwd", ""), "transcript_path": ev.get("transcript_path", ""),
               "type": ev.get("notification_type"), "message": str(ev.get("message") or "")[:300], "title": str(ev.get("title") or "")[:120],
               "tool": fresh.get("tool", ""), "ask": fresh.get("ask") or _short(ev.get("message"), 16),
               "ts": float(cur.get("ts") or now)}   # repeated notifications for the same wait keep the original start
        _write(pending, rec)
        return "recorded"
    if name == "PreToolUse" and ev.get("tool_name") == "AskUserQuestion":
        if not pending.exists():
            _write(pending, {"session_id": ev.get("session_id", ""), "cwd": ev.get("cwd", ""), "transcript_path": ev.get("transcript_path", ""),
                             "type": "question", "message": "", "title": "", "tool": "AskUserQuestion",
                             "ask": describe("AskUserQuestion", ev.get("tool_input") or {}), "ts": now})
        return "recorded"
    if name in ("PostToolUse", "PostToolUseFailure"):
        cur = _read(pending)
        if not cur:
            return "ignored"
        # A parallel tool finishing doesn't answer the prompt; only the tool that was waiting (or any tool, when the
        # wait wasn't about a tool) clears it.
        if cur.get("tool") and cur.get("tool") != ev.get("tool_name"):
            return "kept"
        pending.unlink(missing_ok=True)
        request.unlink(missing_ok=True)
        return "cleared"
    if name in ("PostToolBatch", "UserPromptSubmit", "Stop", "SessionEnd"):
        # PostToolBatch = every call in the batch resolved, including a prompt Ben denied (a manual denial fires
        # neither PostToolUse nor PostToolUseFailure), so nothing in this session is waiting any more.
        pending.unlink(missing_ok=True)
        request.unlink(missing_ok=True)
        return "cleared"
    return "ignored"


def main() -> int:
    try:
        handle(json.loads(sys.stdin.read() or "{}"))
    except Exception:
        pass   # never break or slow a Claude Code session over a backup alert
    return 0


if __name__ == "__main__":
    sys.exit(main())
