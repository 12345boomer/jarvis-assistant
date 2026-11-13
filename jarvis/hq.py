"""Ask Jessica (Ben's agent team in ~/Documents/HQ) a question, read-only, through headless Claude Code.

`jarvis hq ask "how are sales going?"` runs `claude -p` inside the HQ folder, so Jessica's CLAUDE.md, agents and
skills load as in Ben's own chat. Three guarantees:
  - Billing: every ANTHROPIC_* variable (and the Bedrock/Vertex/Foundry switches) is removed from the subprocess, so
    Claude Code runs on Ben's Claude subscription login, never on Jarvis's API key.
  - Read-only: only Read/Grep/Glob/Agent/Skill/Bash exist; Bash is limited to Harvey's read commands; permission mode
    dontAsk auto-denies everything else; user-level settings (Ben's broad allow list, hooks) and MCP connectors are
    not loaded.
  - Bounded: ~3-minute timeout; any failure comes back as one plain sentence.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

HQ_DIR = Path.home() / "Documents" / "HQ"
HARVEY_DIR = Path.home() / "Documents" / "harvey"
TIMEOUT_S = 170   # under Hermes's 180 s terminal cap, so our clean message wins the race
TOOLS = ["Read", "Grep", "Glob", "Agent", "Skill", "Bash"]

# Removed from the child env. CLAUDE_CODE_OAUTH_TOKEN (a subscription token) is deliberately kept.
_SCRUB_PREFIXES = ("ANTHROPIC_",)
_SCRUB_EXACT = {"CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
                "CLAUDE_CODE_USE_ANTHROPIC_AWS", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"}

SYSTEM_PROMPT = """This is a read-only question from Ben, relayed by Jarvis (his voice and Telegram assistant). Answer it
directly, short and plain: a few sentences, no tables, no markdown, no em dashes, because Jarvis may read it aloud on
a phone call. Delegate to an agent only if the question needs one. Skip board/ updates for this question (you can't
write files here). If Ben asks for an action (approve or reject drafts, pause or resume Harvey, send anything, change
anything), don't attempt it: say he should do that in his Jessica chat.

In this session Bash only runs these exact commands (anything else is denied), so use them for Harvey and sales data:
  cd /Users/you/Documents/harvey && /Users/you/Documents/harvey/.venv/bin/harvey status
  cd /Users/you/Documents/harvey && /Users/you/Documents/harvey/.venv/bin/harvey outbox
  cd /Users/you/Documents/harvey && /Users/you/Documents/harvey/.venv/bin/harvey usage
  cd /Users/you/Documents/harvey && /Users/you/Documents/harvey/.venv/bin/harvey export --out /dev/stdout
  cd /Users/you/Documents/harvey && sqlite3 -readonly data/harvey.db "SELECT ..."
(sqlite3 needs -readonly; one command per call, no ';' chains.) Draft text: SELECT id, to_email, subject, body, send_at,
error FROM outbox WHERE status='pending_review' ORDER BY send_at."""


def harvey_bin() -> str:
    return str(HARVEY_DIR / ".venv" / "bin" / "harvey")


def allowed_rules() -> list[str]:
    """Exact read-only commands, written the way Harvey's agent file tells agents to run them (cd + full path)."""
    h, db = harvey_bin(), HARVEY_DIR / "data" / "harvey.db"
    rules = [f"Bash(cd {HARVEY_DIR})"]
    for cmd in ("status", "outbox", "usage", "export --out /dev/stdout"):   # outbox: no flags (they approve/reject)
        rules += [f"Bash({h} {cmd})", f"Bash(.venv/bin/harvey {cmd})"]
    rules += [f"Bash({h} usage --days *)", f"Bash(.venv/bin/harvey usage --days *)"]
    rules += [f"Bash(sqlite3 -readonly {db} *)", "Bash(sqlite3 -readonly data/harvey.db *)"]
    return rules


def denied_rules() -> list[str]:
    return ["Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch",
            f"Read({HARVEY_DIR}/.env)", f"Read({HQ_DIR}/.env)", "Read(**/.env)",
            # Every outbox flag approves or rejects; export without --out /dev/stdout writes a file.
            "Bash(* outbox --*)", "Bash(* sending *)", "Bash(* --approve*)", "Bash(* --reject*)"]


def child_env(base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    for k in list(env):
        if k.startswith(_SCRUB_PREFIXES) or k in _SCRUB_EXACT:
            del env[k]
    env["PATH"] = f"{Path.home() / '.local' / 'bin'}:/opt/homebrew/bin:/usr/bin:/bin:" + env.get("PATH", "")
    return env


def command(question: str, claude: str | None = None) -> list[str]:
    settings = {"permissions": {"allow": allowed_rules(), "deny": denied_rules(), "defaultMode": "dontAsk"}}
    return [claude or shutil.which("claude", path=child_env()["PATH"]) or "claude",
            "-p", question,
            "--output-format", "text",
            "--tools", ",".join(TOOLS),
            "--permission-mode", "dontAsk",
            "--setting-sources", "project",          # HQ's own settings only; not Ben's user allow list or hooks
            "--settings", json.dumps(settings),
            "--strict-mcp-config",                    # no MCP servers / claude.ai connectors (no Gmail send etc.)
            "--add-dir", str(HARVEY_DIR),
            "--append-system-prompt", SYSTEM_PROMPT]


def ask(question: str, *, timeout: float = TIMEOUT_S, run=subprocess.run) -> dict[str, Any]:
    """{'ok': bool, 'answer': str}. Never raises."""
    q = (question or "").strip()
    if not q:
        return {"ok": False, "answer": "Jessica couldn't answer: no question was given."}
    if not HQ_DIR.is_dir():
        return {"ok": False, "answer": f"Jessica couldn't answer: the HQ folder isn't at {HQ_DIR}."}
    try:
        r = run(command(q), cwd=str(HQ_DIR), env=child_env(), capture_output=True, text=True, timeout=timeout,
                stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return {"ok": False, "answer": f"Jessica couldn't answer: she took longer than {int(timeout // 60)} minutes, so I stopped waiting."}
    except FileNotFoundError:
        return {"ok": False, "answer": "Jessica couldn't answer: Claude Code isn't installed where Jarvis can find it."}
    except Exception as e:
        return {"ok": False, "answer": f"Jessica couldn't answer: {type(e).__name__}."}
    out = (r.stdout or "").strip()
    if r.returncode != 0 or not out:
        err = " ".join(((r.stderr or "") + " " + out).split())[:160]
        why = "Claude Code isn't logged in to Ben's subscription" if "login" in err.lower() or "auth" in err.lower() else (err or f"exit {r.returncode}")
        return {"ok": False, "answer": f"Jessica couldn't answer: {why}."}
    return {"ok": True, "answer": out}
