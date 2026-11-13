"""Mac control primitives Jarvis can run from a phone call or chat.

Everything here is stdlib + macOS built-ins. Nothing types passwords or touches
the lock screen: waking the display is the most this module does on that front.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from .audit import AuditLog

HUD_URL = "http://127.0.0.1:8650/"

_URL_RX = re.compile(r"^(https?://|mailto:)", re.I)
_SAFE_APP_RX = re.compile(r"^[\w .&'+-]{1,64}$")


HUD_SCRIPT = Path.home() / ".hermes" / "jarvis-hud.sh"
CHROME_BIN = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def wake_screen_cmds(open_hud: bool = True) -> list[list[str]]:
    """Turn the display on (declares user activity for 5s) and optionally raise the HUD (via its own launcher, never
    `open <url>`: that would land the HUD as a tab in whichever Chrome process answers first)."""
    cmds = [["caffeinate", "-u", "-t", "5"]]
    if open_hud:
        cmds.append(["/bin/bash", str(HUD_SCRIPT), "--no-post"])
    return cmds


# ---- Ben's main Chrome ------------------------------------------------------------------------------------------
# Three Chrome processes run on this Mac: Ben's own (the one with his tabs), the HUD (--user-data-dir jarvis-hud-chrome)
# and Jarvis's hidden browser (--headless, jarvis-browser-chrome). AppleScript's `tell application "Google Chrome"` and
# `open <url>` pick one of them unpredictably; that is how "check my TradingView tab" once saw only the HUD. JavaScript
# for Automation can address an app by process id, so everything here targets Ben's Chrome by its pid.

def main_chrome_pid(ps_output: str | None = None) -> int | None:
    """Pid of Ben's own Chrome: the Chrome binary with no --user-data-dir / --headless / --type (renderer) flags."""
    if ps_output is None:
        ps_output = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True).stdout
    for line in ps_output.splitlines():
        line = line.strip()
        m = re.match(r"(\d+)\s+(.*)$", line)
        if not m or not m.group(2).startswith(CHROME_BIN):
            continue
        args = m.group(2)[len(CHROME_BIN):]
        if "--user-data-dir" in args or "--headless" in args or "--type=" in args:
            continue
        return int(m.group(1))
    return None


def _jxa(script: str) -> list[str]:
    return ["osascript", "-l", "JavaScript", "-e", script]


def _js_str(s: str) -> str:
    import json as _json
    return _json.dumps(s or "")


def chrome_tabs_cmd(pid: int) -> list[str]:
    """JSON list of Ben's tabs: [{"window": n, "index": i, "title", "url", "active": bool}] (read-only)."""
    return _jxa(f"""const c = Application({int(pid)}); const out = [];
c.windows().forEach((w, wi) => {{ const a = w.activeTabIndex(); w.tabs().forEach((t, ti) => out.push({{window: wi + 1, index: ti + 1, title: t.title().slice(0, 120), url: t.url().slice(0, 300), active: ti + 1 === a}})); }});
JSON.stringify(out)""")


def chrome_open_cmd(pid: int, url: str) -> list[str]:
    """New tab with `url` in Ben's front Chrome window (a window is created only if he has none), then raise Chrome."""
    u = (url or "").strip()
    if not _URL_RX.match(u):
        raise ValueError(f"not a URL: {u!r}")
    return _jxa(f"""const c = Application({int(pid)}); let w = c.windows.length ? c.windows[0] : null;
if (!w) {{ w = c.Window(); c.windows.push(w); }}
const t = c.Tab({{url: {_js_str(u)}}}); w.tabs.push(t); w.activeTabIndex = w.tabs.length; c.activate();
JSON.stringify({{window: 1, index: w.tabs.length, url: {_js_str(u)}}})""")


def chrome_focus_cmd(pid: int, match: str) -> list[str]:
    """Bring the first tab whose URL or title contains `match` (case-insensitive) to the front. Prints the tab or 'not found'."""
    m = (match or "").strip()
    if not m or len(m) > 200:
        raise ValueError("give part of the tab's URL or title")
    return _jxa(f"""const c = Application({int(pid)}); const q = {_js_str(m)}.toLowerCase(); let hit = null;
c.windows().forEach((w, wi) => {{ if (hit) return; w.tabs().forEach((t, ti) => {{ if (hit) return;
  if (t.url().toLowerCase().includes(q) || t.title().toLowerCase().includes(q)) {{ w.activeTabIndex = ti + 1; w.index = 1; hit = {{window: wi + 1, index: ti + 1, title: t.title().slice(0, 120), url: t.url().slice(0, 300)}}; }} }}); }});
if (hit) c.activate();
JSON.stringify(hit || {{error: "not found", match: q}})""")


def open_cmds(target: str, pid: int | None = None) -> list[list[str]]:
    """What `jarvis mac open` runs: URLs go to Ben's main Chrome as a new tab (never `open <url>`, never a new window);
    apps use `open -a`. Falls back to `open <url>` only when Ben's Chrome isn't running."""
    cmd = open_cmd(target)
    if cmd[0] == "open" and cmd[1] != "-a":
        if pid is None:
            pid = main_chrome_pid()
        if pid:
            return [chrome_open_cmd(pid, cmd[1])]
    return [cmd]


def open_cmd(target: str) -> list[str]:
    """`open` for a URL or an app name. Anything else is refused (no arbitrary files)."""
    t = (target or "").strip()
    if _URL_RX.match(t):
        return ["open", t]
    if t.lower().endswith((".com", ".org", ".net", ".io", ".ai", ".edu", ".gov")) and " " not in t:
        return ["open", f"https://{t}"]
    if _SAFE_APP_RX.match(t):
        return ["open", "-a", t]
    raise ValueError(f"refusing to open {t!r}: give an app name or a URL")


def volume_cmd(level: int) -> list[str]:
    level = max(0, min(100, int(level)))
    return ["osascript", "-e", f"set volume output volume {level}"]


def _as_text(s: str) -> str:
    """AppleScript string literal: double-quoted, with backslash and quote escaped."""
    return '"' + (s or "").replace("\\", "\\\\").replace('"', '\\"') + '"'


def say_cmd(text: str) -> list[str]:
    return ["osascript", "-e", f"display notification {_as_text(text[:200])} with title \"Jarvis\""]


def brief_cmd() -> list[str]:
    """Open/raise the HUD with the brief overlay (no model call; the dashboard assembles it from cached data)."""
    return ["/bin/bash", str(Path.home() / ".hermes" / "jarvis-hud.sh"), "--brief"]


_RECIPIENT = re.compile(r"^(\+?[0-9][0-9 ()\-]{6,}|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})$")


def text_cmd(to: str, message: str, sms: bool = False) -> list[str]:
    """Send an iMessage (or SMS via the iPhone's text forwarding) through the Mac's Messages app. Tier 2: Ben approves."""
    to = (to or "").strip()
    if not _RECIPIENT.match(to):
        raise ValueError("recipient must be a phone number or email address (look it up with `jarvis mac contact <name>` first)")
    if not (message or "").strip():
        raise ValueError("empty message")
    service = "SMS" if sms else "iMessage"
    script = (f"tell application \"Messages\" to send {_as_text(message.strip()[:2000])} to participant {_as_text(to)} "
              f"of (1st account whose service type is {service})")
    return ["osascript", "-e", script]


def contact_cmd(name: str) -> list[str]:
    """Look up phones and emails in the Mac's Contacts app (read-only, Tier 1)."""
    name = (name or "").strip()
    if not name or len(name) > 60:
        raise ValueError("give a name to look up")
    script = (
        'set AppleScript\'s text item delimiters to ", "\n'
        'tell application "Contacts"\n'
        '  set out to ""\n'
        f'  repeat with p in (every person whose name contains {_as_text(name)})\n'
        '    set out to out & (name of p) & " | phones: " & ((value of phones of p) as text) & " | emails: " & ((value of emails of p) as text) & linefeed\n'
        '  end repeat\n'
        '  return out\n'
        'end tell'
    )
    return ["osascript", "-e", script]


def lock_cmd() -> list[str]:
    return ["pmset", "displaysleepnow"]


def run(cmds: list[list[str]], *, event: str, detail: str, runner=subprocess.run, max_out: int = 2000) -> dict[str, Any]:
    out = []
    for c in cmds:
        r = runner(c, capture_output=True, text=True, timeout=15)
        step = {"cmd": " ".join(c), "rc": r.returncode, "err": (r.stderr or "")[:200]}
        if (getattr(r, "stdout", "") or "").strip():
            step["out"] = r.stdout.strip()[:max_out]   # e.g. the Contacts lookup result or a tab list
        out.append(step)
        if r.returncode != 0:
            AuditLog().event(f"{event}_failed", f"{detail}: {' '.join(c)} rc={r.returncode}")
            return {"status": "error", "steps": out}
    AuditLog().event(event, detail)
    return {"status": "ok", "steps": out}
