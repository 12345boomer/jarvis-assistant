"""Voice bridge: a fast OpenAI-compatible endpoint for ElevenLabs's voice agent.

Why: Hermes's full agent loop takes ~5 s before the first word, and ElevenLabs
gives up before that. This bridge answers conversational turns straight from
Claude (first token well under a second) and only hands off to Hermes when the
caller needs real data or an action (email, calendar, files), saying "one moment"
first so the line is never silent.

Runs standalone (no imports from the jarvis package) so it can live outside
~/Documents, where launchd-started processes are allowed to read.

Env (from the .env next to this file, or the process environment):
  ANTHROPIC_API_KEY, API_SERVER_KEY (bearer expected from ElevenLabs),
  HERMES_API_URL (default http://127.0.0.1:8644), VOICE_MODEL (default claude-haiku-5-5),
  VOICE_PROMPT_FILE, SOUL_FILE (defaults next to this file / ~/.hermes/SOUL.md)
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
from anthropic import AsyncAnthropic
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

HERE = Path(__file__).resolve().parent
log = logging.getLogger("jarvis.bridge")


def _load_env() -> dict[str, str]:
    env = dict(os.environ)
    p = HERE / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, v = s.split("=", 1)
                env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


ENV = _load_env()
MODEL = ENV.get("VOICE_MODEL", "claude-haiku-5-5")
HERMES_URL = ENV.get("HERMES_API_URL", "http://127.0.0.1:8644").rstrip("/")
PROMPT_FILE = Path(ENV.get("VOICE_PROMPT_FILE") or HERE / "voice-agent-prompt.md")
SOUL_FILE = Path(ENV.get("SOUL_FILE") or Path.home() / ".hermes" / "SOUL.md")
FILLER = "One moment. "
GREETING = "Hey, sir. How can I help you?"
SIGNOFF = "Alright, talk soon."

ASK_JARVIS_TOOL = {
    "name": "ask_jarvis",
    "description": (
        "Hand a task to Jarvis's full system: read/search Ben's email, Drive and files, draft and "
        "send email, book or move meetings, control the Mac (wake screen, open apps, operate apps), search the web, remember things, "
        "and ask Jessica (Ben's agent team: Harvey and sales, leads, prospects, drafts, the other agents) a question. "
        "Use it for ACTIONS and for information the quick tools can't give. Takes 5-40 seconds. "
        "NOT for weather, today's calendar, unread mail, markets, or tasks: those have instant tools. "
        "Phrase the task as a complete instruction including any names, dates and details Ben gave."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "What Jarvis should look up or do, in one or two sentences."},
            "say_first": {"type": "string", "description": "Short spoken line to say immediately while working, e.g. 'Checking your inbox.' or 'Waking the screen now.'"},
        },
        "required": ["task", "say_first"],
    },
}

START_TASK_TOOL = {
    "name": "start_task",
    "description": (
        "Start a job that takes more than a minute and let Jarvis finish it in the background: build a presentation or "
        "document, research something properly, multi-step work on the Mac, anything with several steps. Returns at once; "
        "Jarvis does the work and reports back to Ben on Telegram (files attached there). Put EVERY detail Ben gave in the "
        "task: topic, audience, event, length, tone, who it's for, where to send it. Prefer ask_jarvis for quick lookups."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "The full job, as a complete instruction."},
            "say_first": {"type": "string", "description": "Short spoken confirmation, e.g. 'On it. I'll message you on Telegram when the deck is ready.'"},
        },
        "required": ["task", "say_first"],
    },
}

# Instant tools backed by the HUD's cached data (weather, calendar, mail, markets, tasks): sub-second answers.
QUICK_TOOLS = [
    {"name": "weather", "description": "Current weather and today's high/low/rain chance at Ben's location. Instant.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "calendar_today", "description": "Ben's events today with times, and what's next. Instant.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "inbox", "description": "ONLY the current unread email that needs Ben (VIPs first): sender, subject, snippet. Instant. For mail from a specific person, older or already-read mail, reading a full email, or replying, use ask_jarvis instead.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "markets", "description": "Ben's watchlist: price and % change today. Instant.", "input_schema": {"type": "object", "properties": {"symbol": {"type": "string", "description": "Optional single ticker to report"}}}},
    {"name": "brief", "description": "Ben's brief (weather, today's calendar, mail that needs him, tasks, markets). Instant. Also shows it on the HUD screen. Use when he asks for his brief, his morning brief, his rundown, or 'what's my day look like'.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "school", "description": "What's due on HuskyCT (UConn classes): assignments, quizzes, exams in the next two weeks with time left. Instant. Use for 'what's due', 'any homework', 'when is the problem set due'.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "tasks", "description": "Ben's task list. action=list|add|done. Instant.", "input_schema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["list", "add", "done"]}, "text": {"type": "string"}}, "required": ["action"]}},
]
HUD_URL = ENV.get("HUD_URL", "http://127.0.0.1:8650").rstrip("/")
HUD_COOKIE = {"Cookie": f"jarvis={ENV.get('DASHBOARD_SECRET', '')}"}


async def hud_get(path: str) -> Any:
    async with httpx.AsyncClient(timeout=8) as hc:
        r = await hc.get(f"{HUD_URL}{path}", headers=HUD_COOKIE)
        r.raise_for_status()
        return r.json()


async def hud_post(path: str, body: dict[str, Any]) -> Any:
    async with httpx.AsyncClient(timeout=8) as hc:
        r = await hc.post(f"{HUD_URL}{path}", headers=HUD_COOKIE, json=body)
        r.raise_for_status()
        return r.json()


def _hm(iso: str) -> str:
    try:
        from datetime import datetime
        d = datetime.fromisoformat(iso)
        return d.strftime("%-I:%M%p").lower().replace(":00", "")
    except Exception:
        return iso


async def run_quick_tool(name: str, args: dict[str, Any]) -> str:
    """Return compact spoken-friendly text for a quick tool. Never raises."""
    try:
        if name == "brief":
            d = await hud_get("/api/brief")
            try:
                await hud_post("/api/hud/brief", {"open": True})
            except Exception:
                pass
            return (d.get("spoken") or "Nothing to report.") + " It's on your screen too."
        if name == "school":
            d = await hud_get("/api/school")
            if not d.get("ok"):
                return "HuskyCT isn't linked yet. Ben needs to log in to HuskyCT in the Jarvis Browser once."
            items = d.get("items") or []
            if not items:
                return "Nothing due on HuskyCT in the next two weeks."
            def w(h):
                return "overdue" if h < 0 else (f"in {int(h)} hours" if h < 24 else f"in {int(h // 24)} days")
            return f"{len(items)} things due. " + " ".join(f"{i['course']} {i['title']}, {w(i['due_h'])}." for i in items[:4])
        if name == "weather":
            w = await hud_get("/api/weather")
            if "temp" not in w:
                return "Weather isn't available right now."
            return (f"{w['desc']}, {w['temp']} degrees, feels like {w['feels']}. High {w['hi']}, low {w['lo']}"
                    + (f", {w['rain']} percent chance of rain" if w.get('rain') else "") + f". {w.get('place', '')}.")
        if name == "calendar_today":
            t = await hud_get("/api/today")
            ev = t.get("events") or []
            if not ev:
                return "Nothing on the calendar today."
            lines = [f"{_hm(e['start'])} to {_hm(e['end'])}: {e['summary']}" + (" (now)" if e.get("live") else "") for e in ev]
            nxt = t.get("next")
            return "; ".join(lines) + (f". Next up: {nxt['summary']} at {_hm(nxt['start'])}." if nxt else ". Nothing more today.")
        if name == "inbox":
            i = await hud_get("/api/inbox")
            items = i.get("items") or []
            if not items:
                return "No unread mail needs you right now. (Unread only; for a specific sender or older mail, use ask_jarvis.)"
            top = items[:5]
            return f"{i.get('unread', len(items))} unread. " + " ".join(
                f"{'VIP: ' if m.get('vip') else ''}{m['from']} on {m['subject']}." for m in top)
        if name == "markets":
            m = await hud_get("/api/markets")
            qs = [q for q in (m.get("quotes") or []) if not q.get("error")]
            sym = (args.get("symbol") or "").upper().replace("^", "")
            if sym:
                qs = [q for q in qs if q["symbol"].replace("^", "").upper().startswith(sym)] or qs[:1]
            def fmt(q):
                p = q.get("price"); c = q.get("change_pct")
                ps = f"{p:,.0f}" if p and p >= 1000 else f"{p:.2f}" if p else "n/a"
                return f"{q['symbol'].replace('^', '').replace('-USD', '')} {ps}, {'up' if (c or 0) >= 0 else 'down'} {abs(c or 0):.1f} percent"
            return "; ".join(fmt(q) for q in qs[:6]) + "."
        if name == "tasks":
            act = args.get("action", "list")
            if act == "add":
                t = await hud_post("/api/tasks", {"text": args.get("text", "")})
                n = len((await hud_get("/api/tasks")).get("tasks") or [])
                return f"Added '{t['text']}'. {n} open."
            if act == "done":
                tasks = (await hud_get("/api/tasks")).get("tasks") or []
                want = (args.get("text") or "").lower()
                hit = next((x for x in tasks if want and (want in x["text"].lower())), None)
                if not hit:
                    return "I couldn't find that task. Open tasks: " + "; ".join(x["text"] for x in tasks[:5])
                await hud_post(f"/api/tasks/{hit['id']}/done", {"done": True})
                return f"Done: {hit['text']}."
            tasks = (await hud_get("/api/tasks")).get("tasks") or []
            return ("Open tasks: " + "; ".join(x["text"] for x in tasks[:8]) + ".") if tasks else "Your list is empty."
    except Exception as e:
        return f"That lookup failed: {type(e).__name__}."
    return "Unknown tool."

_BACKGROUND: set = set()
app = FastAPI(title="Jarvis voice bridge")
_client: AsyncAnthropic | None = None


def client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=ENV.get("ANTHROPIC_API_KEY", ""))
    return _client


def system_prompt() -> str:
    parts = []
    if SOUL_FILE.exists():
        parts.append(SOUL_FILE.read_text())
    if PROMPT_FILE.exists():
        parts.append(PROMPT_FILE.read_text())
    from datetime import datetime
    parts.append(datetime.now().astimezone().strftime("Right now it is %A, %B %-d %Y, %-I:%M %p (%Z). Use this for anything time-related."))
    parts.append(
        "You are on a live voice line having a real conversation with Ben: talk like a person, natural spoken sentences, "
        "a few at a time, no lists, no markdown. Before sending, texting, booking or posting anything, say what you'll do and "
        "get his okay. "
        "Speed matters: for weather, today's calendar, unread mail, markets, tasks, or his brief, call the instant tool and answer from it. "
        "Call ask_jarvis only for actions or information those tools can't give; always fill say_first with a short line "
        "that tells Ben what you're doing ('Checking your email now.', 'Waking the screen.'). "
        "For anything that takes real work (a presentation, a document, research, several steps on the Mac) call start_task "
        "with the complete brief; it runs in the background and Ben gets the result on Telegram. Talk through the one or two "
        "choices that matter, then start it; don't interrogate him. "
        "Do not use ask_jarvis for questions about yourself, your voice, or settings: answer directly and tell Ben "
        "those are changed in Claude Code, not on a call. "
        "On a phone call the Mac's microphone stays off and the HUD only opens once you start real work for him. "
        "If Ben asks to move the conversation to the Mac/laptop/computer, say 'Got it, switching to the Mac.' and call end_call. "
        "Wrapping up: once you've answered or started his request, check in naturally ('Anything else?' or 'Is that all?'). "
        "If he's done (no, that's all, bye, hang up), sign off in a few words and call end_call in the same turn. No farewell speech."
    )
    return "\n\n---\n\n".join(parts)


def openai_tools_to_claude(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """ElevenLabs sends its system tools (end_call, skip_turn, ...) in OpenAI format."""
    out = []
    ours = {ASK_JARVIS_TOOL["name"], START_TASK_TOOL["name"], *[q["name"] for q in QUICK_TOOLS]}
    for t in tools or []:
        fn = t.get("function") if t.get("type") == "function" else None
        if not fn or not fn.get("name") or fn["name"] in ours:
            continue
        out.append({"name": fn["name"], "description": fn.get("description") or fn["name"],
                    "input_schema": fn.get("parameters") or {"type": "object", "properties": {}}})
    return out


def to_claude_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Split OpenAI-style messages into (extra system text, Claude messages)."""
    system_extra: list[str] = []
    out: list[dict[str, Any]] = []
    for m in messages:
        role, content = m.get("role"), m.get("content")
        if isinstance(content, list):  # OpenAI content parts
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        content = (content or "").strip()
        if role == "tool":  # tool result from ElevenLabs; keep it as plain context
            role, content = "user", f"(tool result: {content[:500]})"
        elif role == "assistant" and not content and m.get("tool_calls"):
            content = "(called " + ", ".join(tc.get("function", {}).get("name", "tool") for tc in m["tool_calls"]) + ")"
        if role == "system":
            system_extra.append(content)
        elif role in ("user", "assistant") and content:
            if out and out[-1]["role"] == role:
                out[-1]["content"] += "\n" + content
            else:
                out.append({"role": role, "content": content})
    if not out or out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": "(call connected)"})
    if out[-1]["role"] != "user":
        out.append({"role": "user", "content": "(waiting)"})
    return "\n".join(system_extra), out


HERMES_TIMEOUT = float(ENV.get("VOICE_HERMES_TIMEOUT", "60"))


TASK_TIMEOUT = float(ENV.get("VOICE_TASK_TIMEOUT", "1500"))     # background jobs started from a call (25 min)
HERMES_BIN = ENV.get("HERMES_BIN") or str(Path.home() / ".local" / "bin" / "hermes")
VOICE_PREAMBLE = ("(From a live voice line with Ben. Be fast: answer in at most three short spoken sentences, plain text. "
                  "Don't open a browser or do deep research unless asked.)")
TASK_PREAMBLE = ("(Background job Ben gave over the phone. Do the whole job now; don't ask clarifying questions, make sensible "
                 "choices and state what you assumed. Your final reply is forwarded to Ben on Telegram as-is, so make it a short "
                 "summary with file paths. Files Ben asked for (decks, documents) are saved under ~/Downloads/Jarvis/ and you give the "
                 "path as 'Downloads/Jarvis/<file>'; do not attach them to Telegram or upload them unless Ben asked for that. "
                 "Quality bar: Claude-app grade. Decks go through the jarvis-deck skill (house style, rendered and inspected before "
                 "delivery); never a plain bullet deck, never fewer slides to save effort. "
                 "If Ben asked for something to be sent, texted, booked or posted, do it as part of the job; don't stop to ask. "
                 "Ask only if a detail is genuinely missing.)")


async def ask_hermes(task: str, *, model: str = "jarvis-voice", timeout: float | None = None, preamble: str = VOICE_PREAMBLE) -> str:
    """Non-streaming call to Hermes's OpenAI-compatible API server, capped at `timeout` seconds (default HERMES_TIMEOUT)."""
    headers = {"Authorization": f"Bearer {ENV.get('API_SERVER_KEY', '')}", "Content-Type": "application/json"}
    body = {"model": model, "stream": False,
            "messages": [{"role": "user", "content": f"{preamble}\n{task}"}]}
    try:
        async with httpx.AsyncClient(timeout=timeout or HERMES_TIMEOUT) as hc:
            r = await hc.post(f"{HERMES_URL}/v1/chat/completions", headers=headers, json=body)
            r.raise_for_status()
            data = r.json()
            return (data["choices"][0]["message"]["content"] or "").strip() or "Jarvis came back with nothing."
    except httpx.TimeoutException:
        return "That's taking too long; I've stopped waiting. It may still finish in the background; check Telegram."
    except Exception as e:  # keep the call alive no matter what
        return f"That didn't go through: {type(e).__name__}. Tell Ben to try again in a minute."


def telegram_payload(text: str, subject: str = "") -> dict[str, Any] | None:
    """Bot API payload for a message to Ben's home channel, or None when Telegram isn't configured."""
    token, chat = ENV.get("TELEGRAM_BOT_TOKEN", ""), ENV.get("TELEGRAM_HOME_CHANNEL", "")
    if not token or not chat:
        return None
    body = (f"{subject}\n\n" if subject else "") + (text or "").strip()
    out: dict[str, Any] = {"chat_id": chat, "text": body[:4000], "disable_web_page_preview": True}
    thread = ENV.get("TELEGRAM_HOME_CHANNEL_THREAD_ID", "")
    if thread:
        out["message_thread_id"] = thread
    return out


async def telegram_notify(text: str, subject: str = "") -> bool:
    """Send text to Ben's Telegram: Bot API first, `hermes send` as the fallback. Never raises."""
    payload = telegram_payload(text, subject)
    if payload:
        try:
            async with httpx.AsyncClient(timeout=15) as hc:
                r = await hc.post(f"https://api.telegram.org/bot{ENV['TELEGRAM_BOT_TOKEN']}/sendMessage", json=payload)
                if r.status_code == 200:
                    return True
                log.warning("telegram sendMessage %s: %s", r.status_code, r.text[:200])
        except Exception as e:
            log.warning("telegram sendMessage failed: %s", e)
    try:
        import asyncio
        proc = await asyncio.create_subprocess_exec(HERMES_BIN, "send", "-t", "telegram", "-q", *(["-s", subject] if subject else []), (text or "").strip()[:4000])
        return (await proc.wait()) == 0
    except Exception as e:
        log.warning("hermes send failed: %s", e)
        return False


ASK_HANDOFF_S = float(os.environ.get('ASK_HANDOFF_S', '45'))   # hand an ask_jarvis run to the background after this long
ASK_JOB_TIMEOUT = float(ENV.get("VOICE_ASK_JOB_TIMEOUT", "240"))  # how long a handed-off ask_jarvis run may take in total


async def run_long_task(task: str) -> None:
    """Background job from a call: full Hermes run (jarvis-task route = the big model), then the result goes to Telegram."""
    log.info("long task started: %s", task[:120])
    t0 = time.time()
    result = await ask_hermes(task, model="jarvis-task", timeout=TASK_TIMEOUT, preamble=TASK_PREAMBLE)
    if result.startswith("That's taking too long"):
        result = f"I ran out of time on this job after {int(TASK_TIMEOUT // 60)} minutes: {task[:200]}"
    ok = await telegram_notify(result, subject=f"From your call ({int(time.time() - t0)}s): {task[:60]}")
    log.info("long task finished in %.0fs (telegram %s)", time.time() - t0, "sent" if ok else "FAILED")


def chunk(cid: str, model: str, delta: dict[str, Any], finish: str | None = None) -> str:
    payload = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
               "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    return f"data: {json.dumps(payload)}\n\n"


STATE_DIR = Path(ENV.get("JARVIS_STATE_DIR") or Path.home() / ".hermes" / "jarvis-state")
_call: dict[str, float] = {"last": 0.0, "started": 0.0}


def is_new_call(messages: list[dict[str, Any]]) -> bool:
    """First request of a phone call: no assistant turn yet beyond ElevenLabs's own first message."""
    assistant_turns = sum(1 for m in messages if m.get("role") == "assistant")
    return assistant_turns <= 1


_CLOSE_WORDS = {"yes", "yeah", "yep", "yup", "no", "nope", "nah", "thats all", "that is all", "thats it", "that is it", "nothing else",
                "im good", "all good", "all set", "thanks", "thank you", "thanks thats all", "thank you thats all", "thanks thats it",
                "yes thats all", "no thats all", "no thats it", "yes thats it", "yeah thats all", "yeah thats it", "nope thats all", "nope thats it",
                "hang up", "hangup", "end the call", "bye", "goodbye", "bye jarvis", "goodbye jarvis", "ok bye", "okay bye", "thanks bye"}
HANGUP = re.compile(r"\b(hang up|hangup|end the call|goodbye|bye)\b", re.I)
_MAC = r"(mac(book)?( air| pro)?|laptop|computer)"
TRANSFER = re.compile(
    r"\b(transfer|switch|move|hand ?off|handoff|go|continue)\b.{0,30}\b" + _MAC + r"\b"
    r"|\b(talk|speak|chat|continue)\b.{0,14}\b(through|on|via|over|from|with)\b.{0,10}\b" + _MAC + r"\b"
    r"|\b" + _MAC + r"('s)?\s+(mic|microphone)\b"
    r"|\b(mic|microphone)\b.{0,24}\b" + _MAC + r"\b", re.I)


def wants_transfer(user_text: str) -> bool:
    """Ben's rule: a call never turns the Mac's microphone on, except when he asks to move the conversation there
    ("transfer to the MacBook", "let's talk through the computer", "put the mic on my laptop")."""
    t = (user_text or "").strip()
    return bool(t) and len(t) <= 160 and bool(TRANSFER.search(t))


async def _handoff_to_mac() -> None:
    """After a transfer: give the phone line a moment to drop, then open the HUD with a live voice session (Mac mic on).
    This is the only path that turns the Mac's microphone on from a call."""
    import asyncio
    await asyncio.sleep(2.5)
    p = await asyncio.create_subprocess_exec("/bin/bash", str(Path.home() / ".hermes" / "jarvis-hud.sh"), "--wake",
                                             stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    await p.wait()


async def _call_finish(text: str) -> None:
    """Last line, then end, in that order (two concurrent posts could land end-before-line)."""
    await _call_post("/api/call/event", {"role": "assistant", "text": text})
    await _call_post("/api/call/end", {})


def _mirror_hud() -> None:
    """Open the HUD in call mode (display only; the phone stays the microphone), once per call, the first time Jarvis
    does real work for Ben. Quick answers (weather, calendar, markets) leave the Mac alone."""
    if _call.get("phone_turn") and not _call.get("mirrored"):
        _call["mirrored"] = time.time()
        _bg(_call_post("/api/call/start", {}))


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", (t or "").lower().replace("'", ""))).strip()


_YES = {"yes", "yeah", "yep", "yup"}


def wants_to_end(user_text: str, last_assistant: str) -> bool:
    """Ben's call etiquette: after an answer Jarvis checks in. After 'Is that all?' a yes/no/bye ends the call at once;
    after 'Anything else?' only a no/that's all/bye does ("yes" there means he has more). An explicit 'hang up' /
    'bye' ends it regardless of what was said before."""
    t = (user_text or "").strip()
    if not t:
        return False
    if HANGUP.search(t) and len(t) <= 40:
        return True
    n, last = _norm(t), (last_assistant or "").lower()
    if n not in _CLOSE_WORDS:
        return False
    if "is that all" in last:
        return True
    return "anything else" in last and not (set(n.split()) & _YES)


def real_text(t: str) -> bool:
    """ElevenLabs sometimes sends '...' or a lone noise token as the user turn; that's not Ben talking."""
    return sum(ch.isalpha() for ch in (t or "")) >= 2


def is_hud_session() -> bool:
    """True when the HUD's own voice session is live (it sets the flag before its first user turn). Otherwise this
    request comes from the phone line."""
    try:
        d = json.loads((STATE_DIR / "voice_active.json").read_text())
        if not d.get("active") or not str(d.get("source", "")).startswith("hud"):
            return False
        from datetime import datetime, timezone
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(d["ts"])).total_seconds()
        return age < 60
    except Exception:
        return False


def _bg(coro) -> None:
    import asyncio
    t = asyncio.create_task(coro)
    _BACKGROUND.add(t)
    t.add_done_callback(_BACKGROUND.discard)


async def _call_post(path: str, body: dict[str, Any]) -> None:
    try:
        await hud_post(path, body)
    except Exception as e:
        log.debug("call event %s failed: %s", path, e)


async def stream_turn(messages: list[dict[str, Any]], model_name: str,
                      extra_tools: list[dict[str, Any]] | None = None) -> AsyncIterator[str]:
    """Phone calls mirror onto the HUD: the Mac shows the transcript and the reactor, but only the phone listens.
    A HUD voice session (flag source=hud) is left alone; it has its own captions."""
    phone = not is_hud_session()
    user_text = ""
    if phone:
        now = time.time()
        if is_new_call(messages) and now - _call["started"] > 20:
            _call["started"] = now
            _call["mirrored"] = 0.0      # Ben's rule: a call touches the Mac only once he asks for something
        _call["last"] = now
        _call["phone_turn"] = 1.0
        for m in reversed(messages):
            c = m.get("content")
            if m.get("role") == "user" and isinstance(c, str) and real_text(c):
                user_text = c.strip(); break
        if user_text:
            _bg(_call_post("/api/call/event", {"role": "user", "text": user_text[:600]}))
    else:
        _call["phone_turn"] = 0.0
    spoken: list[str] = []
    ended = False
    last_assistant = next((m.get("content") for m in reversed(messages) if m.get("role") == "assistant" and isinstance(m.get("content"), str)), "")
    end_tool = next((t for t in (extra_tools or []) if t.get("name") == "end_call"), None)
    if phone and end_tool and wants_transfer(user_text):
        cid = "chatcmpl-" + uuid.uuid4().hex[:24]
        yield chunk(cid, model_name, {"role": "assistant"})
        yield chunk(cid, model_name, {"content": "Got it, switching to the Mac. "})
        yield chunk(cid, model_name, {"tool_calls": [{"index": 0, "id": "call_" + uuid.uuid4().hex[:12], "type": "function",
                                                      "function": {"name": "end_call", "arguments": "{}"}}]})
        yield chunk(cid, model_name, {}, finish="tool_calls")
        yield "data: [DONE]\n\n"
        _call["last"] = _call["started"] = 0.0
        _bg(_call_finish("Switching to the Mac."))
        _bg(_handoff_to_mac())
        return
    if phone and end_tool and wants_to_end(user_text, last_assistant):
        cid = "chatcmpl-" + uuid.uuid4().hex[:24]
        yield chunk(cid, model_name, {"role": "assistant"})
        yield chunk(cid, model_name, {"content": SIGNOFF + " "})
        yield chunk(cid, model_name, {"tool_calls": [{"index": 0, "id": "call_" + uuid.uuid4().hex[:12], "type": "function",
                                                      "function": {"name": "end_call", "arguments": "{}"}}]})
        yield chunk(cid, model_name, {}, finish="tool_calls")
        yield "data: [DONE]\n\n"
        _call["last"] = _call["started"] = 0.0
        _bg(_call_finish(SIGNOFF))
        return
    async for frame in _stream_turn(messages, model_name, extra_tools):
        if phone and frame.startswith("data: {"):
            try:
                delta = json.loads(frame[6:])["choices"][0]["delta"]
                if delta.get("content"):
                    spoken.append(delta["content"])
                if any(tc.get("function", {}).get("name") == "end_call" for tc in delta.get("tool_calls") or []):
                    ended = True
            except Exception:
                pass
        yield frame
    if phone:
        text = "".join(spoken).strip()
        if text:
            _bg(_call_post("/api/call/event", {"role": "assistant", "text": text[:800]}))
        if ended:
            _call["last"] = _call["started"] = 0.0
            _bg(_call_post("/api/call/end", {}))


async def _stream_turn(messages: list[dict[str, Any]], model_name: str,
                       extra_tools: list[dict[str, Any]] | None = None) -> AsyncIterator[str]:
    cid = "chatcmpl-" + uuid.uuid4().hex[:24]
    extra_system, claude_msgs = to_claude_messages(messages)
    system = system_prompt() + ("\n\n---\n\n" + extra_system if extra_system else "")
    tools = [ASK_JARVIS_TOOL, START_TASK_TOOL, *QUICK_TOOLS] + (extra_tools or [])
    quick_names = {q["name"] for q in QUICK_TOOLS}
    yield chunk(cid, model_name, {"role": "assistant"})

    # Nothing real said yet (inbound call, Ben silent for a second): greet instantly, no model round-trip.
    real_user_text = [m["content"] for m in claude_msgs if m["role"] == "user" and m["content"] not in ("(call connected)", "(waiting)") and real_text(m["content"])]
    if not real_user_text:
        yield chunk(cid, model_name, {"content": GREETING})
        yield chunk(cid, model_name, {}, finish="stop")
        yield "data: [DONE]\n\n"
        return

    pending_tool: dict[str, Any] | None = None   # ask_jarvis hand-off or a quick tool (handled here)
    relay_tool: dict[str, Any] | None = None     # an ElevenLabs tool such as end_call (relayed back)
    tool_input_json = ""
    async with client().messages.stream(model=MODEL, max_tokens=1200, system=system,
                                        messages=claude_msgs, tools=tools) as s:
        async for ev in s:
            t = ev.type
            if t == "content_block_start" and getattr(ev.content_block, "type", "") == "tool_use":
                tool_input_json = ""
                if ev.content_block.name in (ASK_JARVIS_TOOL["name"], START_TASK_TOOL["name"]) or ev.content_block.name in quick_names:
                    pending_tool = {"id": ev.content_block.id, "name": ev.content_block.name}
                else:
                    relay_tool = {"id": ev.content_block.id, "name": ev.content_block.name}
            elif t == "content_block_delta":
                d = ev.delta
                if getattr(d, "type", "") == "text_delta":
                    yield chunk(cid, model_name, {"content": d.text})
                elif getattr(d, "type", "") == "input_json_delta":
                    tool_input_json += d.partial_json
        final = await s.get_final_message()

    # Claude may emit several tool_use blocks in one turn (e.g. weather + calendar for a two-part question). Every one
    # of them needs a tool_result in the follow-up or the API rejects the request, so work from the final message's
    # blocks rather than the single id tracked while streaming.
    handled = {ASK_JARVIS_TOOL["name"], START_TASK_TOOL["name"], *quick_names}
    tool_blocks = [b for b in final.content if getattr(b, "type", "") == "tool_use"]
    relay_blocks = [b for b in tool_blocks if b.name not in handled]
    pending_blocks = [b for b in tool_blocks if b.name in handled]

    if relay_blocks and not pending_blocks:
        # Hand the function call back to ElevenLabs in OpenAI streaming format; it executes it (e.g. hangs up).
        rb = relay_blocks[0]
        yield chunk(cid, model_name, {"tool_calls": [{"index": 0, "id": rb.id, "type": "function",
                                                      "function": {"name": rb.name, "arguments": json.dumps(rb.input if isinstance(rb.input, dict) else {})}}]})
        yield chunk(cid, model_name, {}, finish="tool_calls")
        yield "data: [DONE]\n\n"
        return

    if pending_blocks:
        import asyncio
        results: dict[str, str] = {b.id: "ok" for b in relay_blocks}   # a relay tool next to real work: acknowledge it
        pending_tool = {"id": pending_blocks[0].id, "name": pending_blocks[0].name}

        def args_of(b) -> dict[str, Any]:
            if isinstance(b.input, dict):
                return b.input
            try:
                return json.loads(tool_input_json or "{}")
            except json.JSONDecodeError:
                return {"task": tool_input_json}

        quick = [b for b in pending_blocks if b.name in quick_names]
        if quick:
            outs = await asyncio.gather(*(run_quick_tool(b.name, args_of(b)) for b in quick), return_exceptions=True)
            for b, out in zip(quick, outs):
                results[b.id] = out if isinstance(out, str) else f"{b.name} failed: {out}"
        for b in pending_blocks:
            if b.name in quick_names:
                continue
            _mirror_hud()
            targs = args_of(b)
            if b.name == START_TASK_TOOL["name"] and len((targs.get("task") or "").strip()) < 12:
                # Truncated or empty brief (e.g. the model hit max_tokens): never launch a blank job.
                log.warning("start_task without a usable task (stop_reason=%s, raw=%r)", final.stop_reason, tool_input_json[:200])
                results[b.id] = "I didn't catch the details of that job. Ask Ben to say it again in one sentence."
            elif b.name == START_TASK_TOOL["name"]:
                # Fire and forget: the job runs on the big model for up to TASK_TIMEOUT and reports on Telegram.
                yield chunk(cid, model_name, {"content": (targs.get("say_first") or "On it. ").strip() + " "})
                job = asyncio.create_task(run_long_task(targs.get("task", "")))
                _BACKGROUND.add(job)
                job.add_done_callback(_BACKGROUND.discard)
                results[b.id] = "Started. Ben already heard the confirmation; say one short different line, like 'Anything else?'"
            else:
                # Say what we're doing, then keep the line alive with a short line every 15 s.
                yield chunk(cid, model_name, {"content": (targs.get("say_first") or FILLER).strip() + " "})
                # The call itself is protected by the ASK_HANDOFF_S hand-off below, so the job may outlive it (a question for
                # Jessica takes 1-3 min); past the hand-off its answer goes to Telegram.
                job = asyncio.create_task(ask_hermes(targs.get("task", ""), timeout=ASK_JOB_TIMEOUT))
                waited = 0.0
                while True:
                    done, _ = await asyncio.wait({job}, timeout=15)
                    if done:
                        results[b.id] = job.result()
                        break
                    waited += 15
                    if waited >= ASK_HANDOFF_S:
                        # ElevenLabs gives a custom LLM only so long per turn ("LLM Cascade Error: TimeoutError"). Let the
                        # Hermes run finish on its own and deliver its result the way background jobs do.
                        _BACKGROUND.add(job)
                        job.add_done_callback(_BACKGROUND.discard)
                        task_text = targs.get("task", "")
                        async def _deliver(j=job, t=task_text):
                            try:
                                await telegram_notify(await j, subject=f"From your call: {t[:60]}")
                            except Exception as e:
                                log.warning("handoff delivery failed: %s", e)
                        _BACKGROUND.add(asyncio.create_task(_deliver()))
                        results[b.id] = ("Still running; it finishes in the background and Ben gets the result on Telegram. "
                                         "Tell him that in one short line.")
                        break
                    yield chunk(cid, model_name, {"content": "Still on it. "})
        result = results.get(pending_tool["id"], "")
        followup = claude_msgs + [
            {"role": "assistant", "content": final.content},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": b.id, "content": results.get(b.id, "ok")} for b in tool_blocks]},
        ]
        async with client().messages.stream(model=MODEL, max_tokens=1200, system=system, messages=followup,
                                            tools=tools, tool_choice={"type": "none"}) as s2:
            spoke = False
            async for ev in s2:
                if ev.type == "content_block_delta" and getattr(ev.delta, "type", "") == "text_delta":
                    spoke = True
                    yield chunk(cid, model_name, {"content": ev.delta.text})
        if not spoke:
            fallback = "Anything else?" if pending_tool["name"] == START_TASK_TOOL["name"] else (result[:400] or "Done. Anything else?")
            yield chunk(cid, model_name, {"content": fallback})

    yield chunk(cid, model_name, {}, finish="stop")
    yield "data: [DONE]\n\n"


def initiation_data() -> dict[str, Any]:
    """Dynamic variables for a call Jarvis didn't place (inbound Twilio calls). The agent's first message is
    "{{opening}}", and ElevenLabs refuses to start a conversation with it unset; outbound calls and the HUD send their
    own. Static values only: nothing here is private, so the endpoint needs no auth."""
    return {"type": "conversation_initiation_client_data",
            "dynamic_variables": {"opening": GREETING, "call_type": "inbound", "reason": "", "urgent": "false",
                                  "brief": "", "brief_detail": ""}}


@app.post("/elevenlabs/init")
async def elevenlabs_init(request: Request) -> dict[str, Any]:
    return initiation_data()


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "model": MODEL, "hermes": HERMES_URL}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request, authorization: str = Header(default="")) -> Any:
    expected = ENV.get("API_SERVER_KEY", "")
    if not expected or authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="bad bearer token")
    body = await request.json()
    messages = body.get("messages") or []
    model_name = body.get("model") or "jarvis-voice"
    extra_tools = openai_tools_to_claude(body.get("tools") or [])
    if body.get("stream", True):
        return StreamingResponse(stream_turn(messages, model_name, extra_tools), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    text = ""
    async for frame in stream_turn(messages, model_name, extra_tools):
        if frame.startswith("data: {"):
            text += json.loads(frame[6:]).get("choices", [{}])[0].get("delta", {}).get("content", "") or ""
    return JSONResponse({"id": "chatcmpl-" + uuid.uuid4().hex[:24], "object": "chat.completion", "model": model_name,
                         "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]})


if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    uvicorn.run(app, host=ENV.get("VOICE_BRIDGE_HOST", "127.0.0.1"), port=int(ENV.get("VOICE_BRIDGE_PORT", "8642")), log_level="info")
