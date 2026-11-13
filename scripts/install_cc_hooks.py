#!/usr/bin/env python3
"""Merge the Jarvis backup-call hooks into ~/.claude/settings.json (user level = every Claude Code session on this
Mac, including Remote Control sessions). Keeps everything already there; re-running replaces only our entries
(recognized by the hook path). Backs the file up first. Stdlib only.

  python3 scripts/install_cc_hooks.py            install / update
  python3 scripts/install_cc_hooks.py --remove   take our hooks out again
  python3 scripts/install_cc_hooks.py --check    exit 0 if installed
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

SETTINGS = Path.home() / ".claude" / "settings.json"
HOOK = Path.home() / ".hermes" / "jarvis-cc" / "cc_hook.py"
MARK = "jarvis-cc/cc_hook.py"


def our_hooks() -> dict[str, list[dict]]:
    cmd = f"/usr/bin/python3 {HOOK}"
    bg = {"type": "command", "command": cmd, "async": True, "timeout": 10}
    # SessionEnd runs as the process exits; a background hook could be cut off, and this one takes ~30 ms.
    fg = {"type": "command", "command": cmd, "timeout": 5}
    return {
        "Notification": [{"matcher": "permission_prompt|elicitation_dialog|elicitation_url_dialog|agent_needs_input", "hooks": [bg]}],
        "PermissionRequest": [{"matcher": "*", "hooks": [bg]}],
        "PreToolUse": [{"matcher": "AskUserQuestion", "hooks": [bg]}],
        "PostToolUse": [{"matcher": "*", "hooks": [bg]}],
        "PostToolUseFailure": [{"matcher": "*", "hooks": [bg]}],
        "PostToolBatch": [{"hooks": [bg]}],
        "UserPromptSubmit": [{"hooks": [bg]}],
        "Stop": [{"hooks": [bg]}],
        "SessionEnd": [{"hooks": [fg]}],
    }


def _is_ours(group: dict) -> bool:
    return any(MARK in str(h.get("command", "")) for h in group.get("hooks") or [])


def strip(settings: dict) -> dict:
    hooks = settings.get("hooks") or {}
    for event in list(hooks):
        hooks[event] = [g for g in hooks[event] if not _is_ours(g)]
        if not hooks[event]:
            del hooks[event]
    if hooks:
        settings["hooks"] = hooks
    else:
        settings.pop("hooks", None)
    return settings


def merge(settings: dict) -> dict:
    settings = strip(settings)
    hooks = settings.setdefault("hooks", {})
    for event, groups in our_hooks().items():
        hooks.setdefault(event, []).extend(groups)
    return settings


def installed(settings: dict) -> bool:
    hooks = settings.get("hooks") or {}
    return all(any(_is_ours(g) for g in hooks.get(e) or []) for e in our_hooks())


def main() -> int:
    settings = json.loads(SETTINGS.read_text()) if SETTINGS.exists() else {}
    if "--check" in sys.argv:
        ok = installed(settings) and HOOK.exists()
        print("installed" if ok else "not installed")
        return 0 if ok else 1
    if SETTINGS.exists():
        backup = SETTINGS.with_name(f"settings.json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(SETTINGS, backup)
        print(f"  backup  {backup}")
    new = strip(settings) if "--remove" in sys.argv else merge(settings)
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS.with_suffix(".tmp")
    tmp.write_text(json.dumps(new, indent=2) + "\n")
    tmp.replace(SETTINGS)
    print(f"  {'removed' if '--remove' in sys.argv else 'merged'}  Jarvis hooks in {SETTINGS}")
    if not HOOK.exists() and "--remove" not in sys.argv:
        print(f"  WARN    {HOOK} missing: run make link")
    return 0


if __name__ == "__main__":
    sys.exit(main())
