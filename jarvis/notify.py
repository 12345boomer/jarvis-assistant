"""Telegram delivery to Ben's home channel. Stdlib only (cron gates and launchd jobs run it).

Bot API first; `hermes send -t telegram` as the fallback, the same order as the voice bridge.
"""

from __future__ import annotations

import json
import subprocess
import urllib.request
from pathlib import Path

from .audit import AuditLog
from .calls import load_env

HERMES_BIN = str(Path.home() / ".local" / "bin" / "hermes")


def telegram(text: str, subject: str = "", env: dict[str, str] | None = None, opener=None, run=subprocess.run) -> bool:
    """Send text to Ben's Telegram. Never raises; returns True when a path accepted it."""
    env = env if env is not None else load_env()
    body = ((f"{subject}\n\n" if subject else "") + (text or "").strip())[:4000]
    token, chat = env.get("TELEGRAM_BOT_TOKEN", ""), env.get("TELEGRAM_HOME_CHANNEL", "")
    if token and chat:
        payload: dict[str, object] = {"chat_id": chat, "text": body, "disable_web_page_preview": True}
        if env.get("TELEGRAM_HOME_CHANNEL_THREAD_ID"):
            payload["message_thread_id"] = env["TELEGRAM_HOME_CHANNEL_THREAD_ID"]
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with (opener or urllib.request.urlopen)(req, timeout=15) as r:
                if json.loads(r.read().decode() or "{}").get("ok"):
                    return True
        except Exception as e:
            AuditLog().event("telegram_failed", f"bot api: {str(e)[:200]}")
    try:
        args = [HERMES_BIN, "send", "-t", "telegram", "-q", *(["-s", subject] if subject else []), (text or "").strip()[:4000]]
        return run(args, capture_output=True, timeout=60).returncode == 0
    except Exception as e:
        AuditLog().event("telegram_failed", f"hermes send: {str(e)[:200]}")
        return False
