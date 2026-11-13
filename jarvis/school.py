"""HuskyCT (UConn's Blackboard Learn Ultra) for Jarvis.

Two sources, no password ever stored:
  1. The user's calendar feed (ICS). Blackboard gives every user a private feed URL; it holds every due date
     across courses. Fetched with plain HTTPS, cached, parsed here (stdlib). -> HUD "Due" panel, brief, voice.
  2. The Jarvis Browser session (jarvis/cdp.py): Ben logs in to HuskyCT once in that Chrome; Jarvis can then
     call Blackboard's internal JSON API (/learn/api/v1/...) with his cookies for anything richer.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .calls import load_env

BASE = (load_env().get("HUSKYCT_BASE_URL") or os.environ.get("HUSKYCT_BASE_URL") or "https://lms.uconn.edu").rstrip("/")
STATE_DIR = Path(os.environ.get("JARVIS_STATE_DIR") or Path.home() / ".hermes" / "jarvis-state")
CACHE = STATE_DIR / "huskyct_feed.ics"
TZ = ZoneInfo("America/New_York")
_COURSE = re.compile(r"\b([A-Z]{2,4})\s?-?(\d{4}[A-Z]?)\b")


def feed_url() -> str:
    return load_env().get("HUSKYCT_ICS_URL") or os.environ.get("HUSKYCT_ICS_URL", "")


# ---- ICS parsing (RFC 5545 subset: folding, escaping, DTSTART with TZID / UTC / date) ----------------

def _unfold(text: str) -> list[str]:
    out: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def _unescape(v: str) -> str:
    return v.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\;", ";").replace("\\\\", "\\")


def _parse_dt(value: str, params: dict[str, str]) -> datetime | None:
    v = value.strip()
    try:
        if len(v) == 8:                                   # all-day: 20261015
            return datetime.strptime(v, "%Y%m%d").replace(tzinfo=TZ)
        if v.endswith("Z"):
            return datetime.strptime(v, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        tz = ZoneInfo(params["TZID"]) if params.get("TZID") else TZ
        return datetime.strptime(v, "%Y%m%dT%H%M%S").replace(tzinfo=tz)
    except Exception:
        return None


def parse_ics(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT":
            if cur is not None and cur.get("start"):
                events.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            head, value = line.split(":", 1)
            name, *plist = head.split(";")
            params = dict(p.split("=", 1) for p in plist if "=" in p)
            name = name.upper()
            if name == "DTSTART":
                cur["start"] = _parse_dt(value, params)
            elif name == "DTEND":
                cur["end"] = _parse_dt(value, params)
            elif name in ("SUMMARY", "DESCRIPTION", "LOCATION", "UID", "URL", "CATEGORIES"):
                cur[name.lower()] = _unescape(value).strip()
    return events


def course_of(ev: dict[str, Any]) -> str:
    for field in ("summary", "description", "location", "categories"):
        m = _COURSE.search(ev.get(field) or "")
        if m:
            return f"{m.group(1)} {m.group(2)}"
    return "HuskyCT"


def upcoming(events: list[dict[str, Any]], now: datetime | None = None, days: int = 14, limit: int = 12) -> list[dict[str, Any]]:
    """Items due from now to `days` ahead, soonest first, with hours-left and urgency."""
    now = now or datetime.now(TZ)
    horizon = now + timedelta(days=days)
    out = []
    for ev in events:
        due = ev["end"] or ev["start"] if ev.get("end") and (ev["end"] - ev["start"]) < timedelta(hours=1) else ev["start"]
        due = due.astimezone(TZ)
        if due < now - timedelta(hours=6) or due > horizon:
            continue
        hours = (due - now).total_seconds() / 3600
        out.append({"title": (ev.get("summary") or "(untitled)")[:90], "course": course_of(ev), "due": due.isoformat(),
                    "due_h": round(hours, 1), "urgency": "overdue" if hours < 0 else "today" if hours < 24 else "soon" if hours < 72 else "later",
                    "url": ev.get("url", ""), "uid": ev.get("uid", "")})
    out.sort(key=lambda x: x["due"])
    return out[:limit]


def load_feed(max_age_s: int = 900) -> str:
    """Fetch the ICS (cached), or raise with a clear reason."""
    url = feed_url()
    if not url:
        raise RuntimeError("HUSKYCT_ICS_URL not set: run `jarvis school setup` after logging in to HuskyCT in the Jarvis Browser")
    if CACHE.exists() and time.time() - CACHE.stat().st_mtime < max_age_s:
        return CACHE.read_text()
    req = urllib.request.Request(url, headers={"User-Agent": "Jarvis/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        text = r.read().decode("utf-8", "replace")
    if "BEGIN:VCALENDAR" not in text:
        raise RuntimeError("feed did not return a calendar (link expired? re-run `jarvis school setup`)")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(text)
    return text


def due(days: int = 14, limit: int = 12) -> dict[str, Any]:
    try:
        items = upcoming(parse_ics(load_feed()), days=days, limit=limit)
        return {"ok": True, "items": items, "count": len(items), "source": "huskyct-ics"}
    except Exception as e:
        return {"ok": False, "error": str(e), "items": []}


# ---- Browser-session helpers (Blackboard internal API, cookies from the Jarvis Browser) --------------

def whoami() -> dict[str, Any]:
    from . import cdp
    if not cdp.ensure_browser(f"{BASE}/"):
        return {"logged_in": False, "error": "Jarvis Browser did not start"}
    r = cdp.fetch_json(BASE, "/learn/api/v1/users/me")
    if r["status"] == 200 and isinstance(r["body"], dict):
        b = r["body"]
        return {"logged_in": True, "user": b.get("userName") or b.get("emailAddress"), "name": f"{b.get('givenName', '')} {b.get('familyName', '')}".strip()}
    return {"logged_in": False, "status": r["status"]}


def api_get(path: str) -> dict[str, Any]:
    """GET a Blackboard internal/public API path (must start with /learn/api/ or /webapps/) via the logged-in browser."""
    if not (path.startswith("/learn/api/") or path.startswith("/webapps/")):
        raise ValueError("path must start with /learn/api/ or /webapps/")
    from . import cdp
    cdp.ensure_browser(f"{BASE}/")
    return cdp.fetch_json(BASE, path)


def setup(env_path: Path | None = None) -> dict[str, Any]:
    """Find Ben's private calendar-feed URL through the logged-in browser and store it in .env (never printed)."""
    from . import cdp
    from .config import ROOT
    who = whoami()
    if not who.get("logged_in"):
        return {"ok": False, "why": "not logged in: open the Jarvis Browser window and sign in to HuskyCT (NetID + Duo), then run setup again"}
    txt = cdp.evaluate(f"{BASE}/webapps/calendar/calendarFeed/url", "document.body.innerText") or ""
    url = txt.strip().split()[0] if txt.strip() else ""
    if not url.startswith("http") or "/guest/" in url or not url.endswith(".ics"):
        return {"ok": False, "why": f"unexpected feed response: {txt[:80]!r}"}
    p = env_path or ROOT / ".env"
    lines = p.read_text().splitlines() if p.exists() else []
    lines = [l for l in lines if not l.startswith("HUSKYCT_ICS_URL=")]
    lines.append(f"HUSKYCT_ICS_URL={url}")
    p.write_text("\n".join(lines) + "\n")
    if CACHE.exists():
        CACHE.unlink()
    cdp.hide()   # sign-in done: the Jarvis Browser goes back to hidden (Ben's rule: no extra windows)
    return {"ok": True, "user": who.get("user"), "stored": "HUSKYCT_ICS_URL in .env", "browser": "hidden again"}
