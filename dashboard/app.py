"""Jarvis HUD: FastAPI serving one static page plus JSON/SSE endpoints.

Content endpoints (today, inbox, markets, weather, news, tasks, "now") are what Ben
looks at. Ops endpoints (status, feed, approvals, audit) back the hidden drawer.
Binds 127.0.0.1; DASHBOARD_SECRET (token once -> cookie) gates everything but /health.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from jarvis import tasks as taskstore
from jarvis.audit import AuditLog
from jarvis.calls import load_env
from jarvis.config import ROOT, cached_config
from jarvis.quiet_hours import email_is_urgent, is_quiet
from jarvis.spend import SpendMeter

HERE = Path(__file__).parent
HERMES_HOME = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
ENV = load_env(Path(os.environ.get("JARVIS_ENV_FILE") or ROOT / ".env"))
SECRET = ENV.get("DASHBOARD_SECRET", "")
GAPI = HERMES_HOME / "skills" / "jarvis-google" / "scripts" / "gapi"
UA = {"User-Agent": "Mozilla/5.0 (JarvisHUD)"}

def _raise_fd_limit() -> None:
    """launchd gives services a 256 open-file soft limit; SSE streams + subprocess pipes + sqlite can hit it. Lift it to the hard cap."""
    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        want = min(hard if hard != resource.RLIM_INFINITY else 65536, 65536)
        if soft < want:
            resource.setrlimit(resource.RLIMIT_NOFILE, (want, hard))
    except Exception:
        pass


_raise_fd_limit()
app = FastAPI(title="Jarvis HUD", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


# ---- auth (token -> cookie) -----------------------------------------------

@app.middleware("http")
async def require_token(request: Request, call_next):
    if SECRET and request.url.path not in ("/login", "/health"):
        if request.cookies.get("jarvis") != SECRET:
            tok = request.query_params.get("token")
            if tok == SECRET:
                rest = "&".join(f"{k}={v}" for k, v in request.query_params.items() if k != "token")
                resp = RedirectResponse(request.url.path + (f"?{rest}" if rest else ""))
                resp.set_cookie("jarvis", SECRET, httponly=True, samesite="strict", max_age=60 * 60 * 24 * 30)
                return resp
            return JSONResponse({"error": "unauthorized; open /?token=<DASHBOARD_SECRET> once"}, status_code=401)
    return await call_next(request)


# ---- small cache -----------------------------------------------------------

_cache: dict[str, tuple[float, Any]] = {}
_locks: dict[str, asyncio.Lock] = {}


async def cached(key: str, ttl: float, fn: Callable[[], Awaitable[Any]]) -> Any:
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        try:
            val = await fn()
        except Exception as e:  # keep the last good value, surface the error
            val = {"error": str(e)[:200], **(hit[1] if hit and isinstance(hit[1], dict) else {})}
        _cache[key] = (time.time(), val)
        return val


def _hud_cfg() -> dict[str, Any]:
    return cached_config().get("hud") or {}


def _tz() -> ZoneInfo:
    return ZoneInfo(cached_config().get("timezone", "America/New_York"))


# ---- helpers ---------------------------------------------------------------

def _launchd(label: str) -> dict[str, Any]:
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return {"registered": False, "pid": None}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[2] == label:
            return {"registered": True, "pid": int(parts[0]) if parts[0].isdigit() else None, "last_exit": parts[1]}
    return {"registered": False, "pid": None}


async def _health(url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            return (await c.get(url)).status_code in (200, 401)
    except Exception:
        return False


async def _gapi(*args: str) -> Any:
    """Run the Google skill script off the event loop."""
    def run():
        r = subprocess.run([str(GAPI), *args], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or "gapi failed").strip()[-200:])
        return json.loads(r.stdout or "[]")
    return await asyncio.to_thread(run)


def _cron_jobs() -> list[dict[str, Any]]:
    try:
        d = json.loads((HERMES_HOME / "cron" / "jobs.json").read_text())
    except Exception:
        return []
    jobs = d if isinstance(d, list) else d.get("jobs", list(d.values()) if isinstance(d, dict) else [])
    out = []
    for j in jobs:
        if isinstance(j, dict):
            s = j.get("schedule") or {}
            out.append({"id": j.get("id"), "name": j.get("name"), "schedule": s.get("display") or s.get("expr") or str(s),
                        "next_run_at": j.get("next_run_at"), "last_run_at": j.get("last_run_at"), "last_status": j.get("last_status"),
                        "enabled": j.get("enabled", True), "state": j.get("state"), "gated": bool(j.get("script"))})
    return out


def _feed(limit: int = 60) -> list[dict[str, Any]]:
    log = AuditLog()
    rows = [{**r, "kind": "tool"} for r in log.recent(limit)]
    events = [{**e, "event_kind": e.get("kind"), "kind": "event"} for e in log.recent_events(limit)]
    return sorted(rows + events, key=lambda x: x["ts"], reverse=True)[:limit]


def _approvals(window_s: int = 900) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc).timestamp() - window_s
    pending, decided = [], []
    for r in AuditLog().recent(200):
        if r["decision"] != "ask":
            continue
        try:
            ts = datetime.fromisoformat(r["ts"]).timestamp()
        except Exception:
            ts = 0
        (decided if r.get("approval") else pending if ts >= cutoff else decided).append(r)
    return {"pending": pending[:10], "decided": decided[:20]}


# ---- content: today / inbox ------------------------------------------------

_TOOL_VERBS = {"terminal": "running a command", "computer_use": "working on the Mac", "web_search": "searching the web",
               "web_extract": "reading a page", "browser_exec": "using the browser", "skill_view": "loading a skill",
               "honcho_search": "recalling memory", "memory": "taking a note", "read_file": "reading a file", "write_file": "writing a file"}


def _now_doing() -> dict[str, Any]:
    rows = AuditLog().recent(1)
    if not rows:
        return {"state": "idle", "text": "standing by"}
    r = rows[0]
    try:
        age = datetime.now(timezone.utc).timestamp() - datetime.fromisoformat(r["ts"]).timestamp()
    except Exception:
        age = 1e9
    if age > 90:
        return {"state": "idle", "text": "standing by", "last": r["ts"]}
    verb = _TOOL_VERBS.get(r["tool"], f"using {r['tool']}")
    hint = ""
    try:
        a = json.loads(r["args_summary"] or "{}")
        hint = str(a.get("command") or a.get("query") or a.get("q") or a.get("url") or a.get("name") or "")[:70]
    except Exception:
        pass
    return {"state": "busy", "text": verb + (f": {hint}" if hint else ""), "since": r["ts"], "decision": r["decision"]}


async def _today() -> dict[str, Any]:
    tz = _tz(); now = datetime.now(tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    events = await _gapi("calendar", "list", "--start", start.isoformat(), "--end", (start + timedelta(days=1)).isoformat())
    out, nxt = [], None
    for e in events:
        try:
            s = datetime.fromisoformat(e["start"].replace("Z", "+00:00")).astimezone(tz)
            en = datetime.fromisoformat(e["end"].replace("Z", "+00:00")).astimezone(tz)
        except Exception:
            continue
        item = {"id": e.get("id"), "summary": e.get("summary") or "(untitled)", "start": s.isoformat(), "end": en.isoformat(),
                "location": e.get("location") or "", "attendees": e.get("attendees") or [], "past": en < now, "live": s <= now < en}
        out.append(item)
        if s > now and (nxt is None or s < datetime.fromisoformat(nxt["start"])):
            nxt = item
    return {"date": start.date().isoformat(), "events": out, "next": nxt}


async def _inbox() -> dict[str, Any]:
    cfg = cached_config()
    msgs = await _gapi("gmail", "search", "is:unread newer_than:2d -category:promotions -category:social -category:updates", "--max", "12")
    items = []
    for m in msgs:
        sender = m.get("from", "")
        name = sender.split("<")[0].strip().strip('"') or sender
        vip = email_is_urgent(sender, m.get("subject", ""), m.get("snippet", ""), cfg) or any(
            v.lower().lstrip("@") in sender.lower() for v in (cfg.get("urgent") or {}).get("vip_senders") or [])
        items.append({"id": m.get("id"), "from": name[:40], "subject": (m.get("subject") or "(no subject)")[:90],
                      "snippet": (m.get("snippet") or "")[:140], "date": m.get("date", ""), "vip": bool(vip),
                      "link": f"https://mail.google.com/mail/u/0/#inbox/{m.get('id')}"})
    items.sort(key=lambda x: (not x["vip"]))
    return {"unread": len(items), "vip": sum(1 for i in items if i["vip"]), "items": items}


# ---- content: markets / weather / news ------------------------------------

async def _quote(client: httpx.AsyncClient, sym: str) -> dict[str, Any]:
    r = await client.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}", params={"range": "1d", "interval": "5m"}, headers=UA)
    d = r.json()["chart"]["result"][0]
    meta = d["meta"]
    closes = [c for c in (d.get("indicators", {}).get("quote", [{}])[0].get("close") or []) if c is not None]
    price = meta.get("regularMarketPrice"); prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    chg = (price - prev) / prev * 100 if price and prev else None
    step = max(1, len(closes) // 40)
    return {"symbol": sym, "name": meta.get("shortName") or sym, "price": price, "prev": prev,
            "change_pct": round(chg, 2) if chg is not None else None, "spark": [round(c, 2) for c in closes[::step]][-40:],
            "currency": meta.get("currency", "USD"), "market_state": meta.get("marketState", "")}


async def _markets() -> dict[str, Any]:
    syms = [str(s) for s in (_hud_cfg().get("watchlist") or ["SPY", "QQQ", "BTC-USD"])]
    async with httpx.AsyncClient(timeout=10) as c:
        results = await asyncio.gather(*[_quote(c, s) for s in syms], return_exceptions=True)
    quotes = [r if isinstance(r, dict) else {"symbol": s, "error": str(r)[:80]} for s, r in zip(syms, results)]
    return {"quotes": quotes, "asof": datetime.now(timezone.utc).isoformat(timespec="seconds")}


_WMO = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "fog", 51: "drizzle", 53: "drizzle", 55: "drizzle",
        61: "light rain", 63: "rain", 65: "heavy rain", 71: "light snow", 73: "snow", 75: "heavy snow", 80: "showers", 81: "showers", 82: "heavy showers",
        95: "thunderstorm", 96: "thunderstorm", 99: "thunderstorm"}


async def _weather() -> dict[str, Any]:
    loc = _hud_cfg().get("location") or {"name": "New York, NY", "lat": 40.71, "lon": -74.01}
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude": loc["lat"], "longitude": loc["lon"], "current": "temperature_2m,weather_code,wind_speed_10m,apparent_temperature",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max", "temperature_unit": "fahrenheit",
            "wind_speed_unit": "mph", "timezone": str(_tz()), "forecast_days": 1})
    d = r.json(); cur, day = d["current"], d["daily"]
    return {"place": loc.get("name", ""), "temp": round(cur["temperature_2m"]), "feels": round(cur.get("apparent_temperature", cur["temperature_2m"])),
            "desc": _WMO.get(cur.get("weather_code"), "—"), "wind": round(cur.get("wind_speed_10m", 0)),
            "hi": round(day["temperature_2m_max"][0]), "lo": round(day["temperature_2m_min"][0]), "rain": day["precipitation_probability_max"][0]}


async def _news() -> dict[str, Any]:
    feeds = _hud_cfg().get("news_feeds") or []
    items = []
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
        for url in feeds:
            try:
                r = await c.get(url, headers=UA)
                root = ET.fromstring(r.text)
                src = "CNBC" if "cnbc.com" in url else (root.findtext("./channel/title") or "").split(" - ")[0][:24]
                for it in root.findall(".//item")[:8]:
                    items.append({"title": (it.findtext("title") or "").strip()[:120], "link": it.findtext("link") or "",
                                  "pub": it.findtext("pubDate") or "", "source": src})
            except Exception as e:
                items.append({"title": f"feed error: {str(e)[:60]}", "link": url, "pub": "", "source": "hud"})
    seen, out = set(), []
    for i in items:
        if i["title"] and i["title"] not in seen:
            seen.add(i["title"]); out.append(i)
    return {"items": out[:12]}


# ---- school (HuskyCT due dates from the calendar feed) ----------------------

async def _school() -> dict[str, Any]:
    from jarvis import school
    return await asyncio.to_thread(school.due, 14, 12)


# ---- brief (on demand only; no scheduled delivery) -------------------------

def _held_mail() -> list[dict[str, Any]]:
    """VIP mail the inbox routine held during quiet hours (~/.hermes/jarvis-state/inbox.json -> held)."""
    try:
        return (json.loads((HERMES_HOME / "jarvis-state" / "inbox.json").read_text()).get("held") or [])[-10:]
    except Exception:
        return []


def _hm(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%-I:%M%p").lower().replace(":00", "")
    except Exception:
        return iso


def _due_words(i: dict) -> str:
    h = i.get("due_h", 0)
    if h < 0:
        return "overdue"
    if h < 24:
        return f"in {int(h)}h" if h >= 1 else "within the hour"
    return f"in {int(h // 24)}d {int(h % 24)}h"


def compose_brief(today: dict, inbox: dict, weather: dict, markets: dict, tasks: list, news: dict, held: list, now: datetime, school: dict | None = None) -> dict[str, Any]:
    """Pure: HUD data -> {'sections': [{title, lines}], 'spoken': str}. No model call, so it's instant and free."""
    sections: list[dict[str, Any]] = []
    spoken: list[str] = []
    hour = now.hour
    greet = "Morning" if hour < 12 else "Afternoon" if hour < 18 else "Evening"
    if weather and weather.get("temp") is not None:
        wx = f"{weather['temp']}° and {weather.get('desc', '')}, high {weather.get('hi')}, low {weather.get('lo')}" + (f", {weather['rain']}% chance of rain" if weather.get("rain") else "")
        sections.append({"title": "Weather", "lines": [wx]})
        spoken.append(f"{greet}, Ben. {wx}.")
    else:
        spoken.append(f"{greet}, Ben.")

    events = [e for e in (today or {}).get("events") or [] if not e.get("past")]
    if events:
        lines = [f"{_hm(e['start'])}–{_hm(e['end'])}  {e['summary']}" + (f"  · {e['location']}" if e.get("location") else "") for e in events]
        sections.append({"title": "Today", "lines": lines})
        first = events[0]
        spoken.append(f"{len(events)} on the calendar" + (f", first is {first['summary']} at {_hm(first['start'])}" if len(events) > 1 else f": {first['summary']} at {_hm(first['start'])}") + ".")
    else:
        sections.append({"title": "Today", "lines": ["Nothing left on the calendar."]})
        spoken.append("Nothing on the calendar.")

    school_items = [i for i in (school or {}).get("items") or [] if i.get("urgency") in ("overdue", "today", "soon")]
    if school_items:
        sections.append({"title": "Due", "lines": [f"{i['course']}: {i['title']}  · {_due_words(i)}" for i in school_items[:5]]})
        first = school_items[0]
        spoken.append(f"School: {first['course']} {first['title']} is due {_due_words(first)}" + (f", plus {len(school_items) - 1} more this week" if len(school_items) > 1 else "") + ".")

    needs = [i for i in (inbox or {}).get("items") or [] if i.get("vip")]
    other = [i for i in (inbox or {}).get("items") or [] if not i.get("vip")]
    held_lines = [f"{(h.get('from') or '').split('<')[0].strip()}: {h.get('subject', '')}" for h in held]
    lines = [f"{i['from']}: {i['subject']}" for i in needs[:5]]
    if held_lines:
        lines += [f"(overnight) {x}" for x in held_lines[:5]]
    if other:
        lines.append(f"{len(other)} other unread")
    sections.append({"title": "Needs you", "lines": lines or ["Inbox is quiet."]})
    if needs or held_lines:
        who = ", ".join(dict.fromkeys([i["from"] for i in needs[:3]] + [x.split(":")[0] for x in held_lines[:2]]))
        spoken.append(f"Mail that needs you from {who}.")
    else:
        spoken.append("Inbox is quiet.")

    open_tasks = [t.get("text", "") for t in (tasks or []) if not t.get("done")]
    if open_tasks:
        sections.append({"title": "Tasks", "lines": open_tasks[:6]})
        spoken.append(f"{len(open_tasks)} open task{'s' if len(open_tasks) != 1 else ''}: " + "; ".join(open_tasks[:3]) + ".")

    quotes = (markets or {}).get("quotes") or []
    if quotes:
        for q in quotes:
            q["pct"] = q.get("pct") if q.get("pct") is not None else q.get("change_pct")
        movers = sorted([q for q in quotes if q.get("pct") is not None], key=lambda q: -abs(q["pct"]))[:4]
        if movers:
            sections.append({"title": "Markets", "lines": [f"{q['symbol']}  {q.get('price', '')}  {q['pct']:+.1f}%" for q in movers]})
            spoken.append("Markets: " + ", ".join(f"{q['symbol']} {q['pct']:+.1f} percent" for q in movers[:3]) + ".")

    heads = [n["title"] for n in (news or {}).get("items") or [] if not n["title"].startswith("feed error")][:4]
    if heads:
        sections.append({"title": "Headlines", "lines": heads})
    return {"generated": now.isoformat(), "sections": sections, "spoken": " ".join(spoken)}


async def _brief() -> dict[str, Any]:
    today, inbox, weather, markets, news, school = await asyncio.gather(
        cached("today", 120, _today), cached("inbox", 120, _inbox), cached("weather", 600, _weather),
        cached("markets", 60, _markets), cached("news", 600, _news), cached("school", 600, _school), return_exceptions=True)
    safe = lambda x: x if isinstance(x, dict) else {}
    return compose_brief(safe(today), safe(inbox), safe(weather), safe(markets), taskstore.open_tasks(), safe(news), _held_mail(), datetime.now(_tz()), safe(school))


_brief_requested = 0.0
# Phone-call mirror: the voice bridge posts call start / each line / end; the HUD renders them (no mic on the Mac).
_call: dict[str, Any] = {"active": False, "last": 0.0, "lines": []}
_call_events: list[tuple[int, dict[str, Any]]] = []
_call_seq = 0
CALL_IDLE_END_S = 150.0


def _call_emit(payload: dict[str, Any]) -> None:
    global _call_seq
    _call_seq += 1
    _call_events.append((_call_seq, payload))
    del _call_events[:-50]


def _call_touch() -> None:
    _call["last"] = time.time()
    _set_voice_active(True, "phone")        # pauses the wake-word listener while the phone line is live


def _call_begin(reason: str) -> None:
    # Lines spoken before the HUD opened (Ben's rule: nothing on the Mac until he asks for real work) are kept, so the
    # transcript on screen starts from the beginning of the call.
    _call.update({"active": True})
    _call_touch()
    AuditLog().event("call_start", reason)
    # Light up a dark display (no password needed with the lid open), then open/raise the HUD in call mode.
    subprocess.Popen(["caffeinate", "-u", "-t", "5"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    script = HERMES_HOME / "jarvis-hud.sh"
    if script.exists():
        subprocess.Popen(["/bin/bash", str(script), "--call", "--no-post"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _call_emit({"state": "start"})


def _call_end(reason: str = "ended") -> None:
    was = _call["active"]
    _call.update({"active": False, "lines": []})
    _set_voice_active(False, "phone")        # the line is gone whether or not the HUD ever opened
    if was:
        AuditLog().event("call_end", reason)
        _call_emit({"state": "end"})
_sse_clients = 0   # open HUD pages (SSE connections); 0 with a HUD Chrome process alive = window closed, process lingering

# ---- routes ----------------------------------------------------------------

@app.get("/")
async def index():
    return FileResponse(HERE / "static" / "index.html")


@app.get("/api/today")
async def api_today():
    return await cached("today", 120, _today)


@app.get("/api/inbox")
async def api_inbox():
    return await cached("inbox", 120, _inbox)


@app.get("/api/markets")
async def api_markets():
    return await cached("markets", 60, _markets)


@app.get("/api/weather")
async def api_weather():
    return await cached("weather", 600, _weather)


@app.get("/api/news")
async def api_news():
    return await cached("news", 600, _news)


@app.get("/api/now")
async def api_now():
    return _now_doing()


@app.get("/api/tasks")
async def api_tasks(all: bool = False):
    return {"tasks": taskstore.load() if all else taskstore.open_tasks()}


@app.post("/api/tasks")
async def api_tasks_add(request: Request):
    body = await request.json()
    try:
        return taskstore.add(str(body.get("text", "")), due=body.get("due"), source="hud")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/tasks/{task_id}/done")
async def api_tasks_done(task_id: str, request: Request):
    body = await request.json() if request.headers.get("content-length") not in (None, "0") else {}
    t = taskstore.complete(task_id, bool(body.get("done", True)))
    if not t:
        raise HTTPException(404, "no such task")
    return t


@app.delete("/api/tasks/{task_id}")
async def api_tasks_remove(task_id: str):
    return {"removed": taskstore.remove(task_id)}


@app.get("/api/status")
async def status():
    cfg = cached_config()
    hermes_port = ENV.get("API_SERVER_PORT", "8644")
    gateway, bridge = _launchd("ai.hermes.gateway"), _launchd("ai.jarvis.bridge")
    hermes_api, bridge_api = await asyncio.gather(_health(f"http://127.0.0.1:{hermes_port}/health"), _health("http://127.0.0.1:8642/health"))
    return {"now": datetime.now(timezone.utc).isoformat(timespec="seconds"), "quiet_hours": is_quiet(None, cfg),
            "spend": SpendMeter(cfg).status(),
            "services": {"gateway": {**gateway, "api_ok": hermes_api}, "bridge": {**bridge, "api_ok": bridge_api},
                         "funnel_url": ENV.get("JARVIS_PUBLIC_URL", ""), "telegram": bool(ENV.get("TELEGRAM_BOT_TOKEN")),
                         "honcho": bool(ENV.get("HONCHO_API_KEY")), "google": (HERMES_HOME / "google_token.json").exists(),
                         "elevenlabs": bool(ENV.get("ELEVENLABS_AGENT_ID")), "phone": ENV.get("TWILIO_FROM_NUMBER", "")},
            "cron": _cron_jobs(), "now": _now_doing()}


@app.get("/api/feed")
async def feed(limit: int = Query(60, le=300)):
    return {"items": _feed(limit)}


@app.get("/api/approvals")
async def approvals():
    return _approvals()


@app.get("/api/audit")
async def audit(limit: int = Query(100, le=1000)):
    return {"rows": AuditLog().recent(limit)}


_wake_requested: float = 0.0


@app.get("/api/school")
async def api_school():
    return await cached("school", 600, _school)


@app.get("/api/call")
async def api_call():
    return {"active": _call["active"], "lines": _call["lines"][-12:], "last": _call["last"]}


@app.post("/api/call/start")
async def api_call_start():
    """A phone call began: open/raise the HUD in call mode (display only; the phone is the microphone)."""
    _call_begin("phone call mirrored to HUD")
    return {"ok": True}


@app.post("/api/call/event")
async def api_call_event(request: Request):
    body = await request.json()
    role, text = str(body.get("role", "assistant")), str(body.get("text", "")).strip()
    if not text:
        return {"ok": False}
    # A line never opens the HUD by itself (only /api/call/start does, when Jarvis starts real work); it is buffered so
    # the transcript is complete if the HUD opens later in the call.
    _call_touch()
    line = {"role": "user" if role == "user" else "assistant", "text": text[:800], "ts": time.time()}
    _call["lines"].append(line); del _call["lines"][:-40]
    if _call["active"]:
        _call_emit({"state": "line", **line})
    return {"ok": True}


@app.post("/api/call/end")
async def api_call_end():
    _call_end("hung up")
    return {"ok": True}


@app.post("/api/hud/call")
async def hud_call():
    """Tell an open HUD page to enter call mode (used by jarvis-hud.sh --call when a page is already open)."""
    _call_emit({"state": "start"} if _call["active"] else {"state": "end"})
    return {"ok": True}


@app.get("/api/brief")
async def api_brief():
    return await _brief()


@app.post("/api/hud/brief")
async def hud_brief(request: Request):
    """Show the brief on the HUD. {"open": true} also opens/raises the HUD window (via ~/.hermes/jarvis-hud.sh --brief)."""
    global _brief_requested
    _brief_requested = time.time()
    try:
        body = await request.json()
    except Exception:
        body = {}
    opened = False
    script = HERMES_HOME / "jarvis-hud.sh"
    if (body or {}).get("open") and script.exists():
        subprocess.Popen(["/bin/bash", str(script), "--brief", "--no-post"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        opened = True
    AuditLog().event("brief", "brief requested" + (" (open HUD)" if opened else ""))
    return {"ok": True, "opened": opened}


@app.get("/api/hud/clients")
async def hud_clients():
    """How many HUD pages are open right now (used by jarvis-hud.sh to tell a live window from a windowless Chrome)."""
    return {"clients": _sse_clients}


@app.post("/api/hud/wake")
async def hud_wake():
    global _wake_requested
    _wake_requested = time.time()
    AuditLog().event("wake_word", "hud wake requested")
    return {"ok": True}


@app.get("/api/stream")
async def stream():
    """SSE: 'feed' when the audit log changes, 'status' every 15s, 'now' every 3s, 'wake' on request."""
    async def gen():
        global _wake_requested, _brief_requested, _sse_clients
        _sse_clients += 1
        try:
            async for item in _gen_inner():
                yield item
        finally:
            _sse_clients -= 1

    async def _gen_inner():
        global _wake_requested, _brief_requested
        last_id, last_evt, last_tick, last_now = -1, -1, 0.0, ""
        call_seq = _call_seq
        while True:
            if _wake_requested and time.time() - _wake_requested < 30:
                _wake_requested = 0.0
                yield "event: wake\ndata: {}\n\n"
            while call_seq < _call_seq:
                for seq, payload in _call_events:
                    if seq > call_seq:
                        call_seq = seq
                        yield f"event: call\ndata: {json.dumps(payload)}\n\n"
            if _call["active"]:
                if time.time() - _call["last"] > CALL_IDLE_END_S:
                    _call_end("idle timeout")
                elif time.time() - _call["last"] > 20:
                    _set_voice_active(True, "phone")   # keep the listener paused through long pauses on the line
            if _brief_requested and time.time() - _brief_requested < 30:
                _brief_requested = 0.0
                yield f"event: brief\ndata: {json.dumps(await _brief())}\n\n"
            log = AuditLog()
            r = log.recent(1); e = log.recent_events(1)
            rid = r[0]["id"] if r else 0; eid = e[0]["id"] if e else 0
            if rid != last_id or eid != last_evt:
                last_id, last_evt = rid, eid
                yield f"event: feed\ndata: {json.dumps({'items': _feed(40), 'approvals': _approvals()})}\n\n"
            nd = _now_doing()
            if nd.get("text") != last_now:
                last_now = nd.get("text", "")
                yield f"event: now\ndata: {json.dumps(nd)}\n\n"
            if time.time() - last_tick > 15:
                last_tick = time.time()
                yield f"event: status\ndata: {json.dumps(await status())}\n\n"
            else:
                yield ": keepalive\n\n"
            await asyncio.sleep(3)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.get("/api/voice/signed-url")
async def voice_signed_url():
    key, agent = ENV.get("ELEVENLABS_API_KEY"), ENV.get("ELEVENLABS_AGENT_ID")
    if not key or not agent:
        raise HTTPException(503, "ELEVENLABS_API_KEY / ELEVENLABS_AGENT_ID not set")
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get("https://api.elevenlabs.io/v1/convai/conversation/get-signed-url", params={"agent_id": agent}, headers={"xi-api-key": key})
    if r.status_code != 200:
        raise HTTPException(502, f"ElevenLabs {r.status_code}: {r.text[:200]}")
    return {"signed_url": r.json().get("signed_url"), "agent_id": agent}



@app.get("/api/voice/token")
async def voice_token():
    """WebRTC conversation token for the HUD. ElevenLabs recommends WebRTC in browsers: the websocket transport left
    user turns open for 10-30 s (transcript only arrived at disconnect), WebRTC ends them promptly."""
    key, agent = ENV.get("ELEVENLABS_API_KEY"), ENV.get("ELEVENLABS_AGENT_ID")
    if not key or not agent:
        raise HTTPException(503, "ELEVENLABS_API_KEY / ELEVENLABS_AGENT_ID not set")
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get("https://api.elevenlabs.io/v1/convai/conversation/token", params={"agent_id": agent}, headers={"xi-api-key": key})
    if r.status_code != 200:
        raise HTTPException(502, f"ElevenLabs {r.status_code}: {r.text[:200]}")
    return {"token": r.json().get("token"), "agent_id": agent}

_VOICE_FLAG = HERMES_HOME / "jarvis-state" / "voice_active.json"


def _set_voice_active(active: bool, source: str = "hud") -> dict[str, Any]:
    _VOICE_FLAG.parent.mkdir(parents=True, exist_ok=True)
    state = {"active": bool(active), "source": source, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    _VOICE_FLAG.write_text(json.dumps(state))
    return state


@app.get("/api/voice/active")
async def voice_active():
    try:
        return json.loads(_VOICE_FLAG.read_text())
    except Exception:
        return {"active": False}


@app.post("/api/voice/active")
async def voice_active_set(request: Request):
    body = await request.json()
    try:
        was = bool(json.loads(_VOICE_FLAG.read_text()).get("active"))
    except Exception:
        was = False
    state = _set_voice_active(bool(body.get("active")), str(body.get("source", "hud")))
    if state["active"] != was:   # heartbeats re-assert the flag every 20 s; only log real starts/ends
        AuditLog().event("voice_session_start" if state["active"] else "voice_session_end", state["source"])
    return state


@app.get("/health")
async def health():
    return {"ok": True}
