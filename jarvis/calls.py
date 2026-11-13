"""Outbound phone calls: Jarvis calls Ben through ElevenLabs's voice agent + Twilio.

Quiet hours are enforced here, not left to the model: a non-urgent call inside
23:00-07:00 ET is refused and logged. Stdlib only (urllib) so it runs anywhere.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .audit import AuditLog
from .config import ROOT, cached_config
from .quiet_hours import is_quiet, may_notify

ELEVEN_OUTBOUND_URL = "https://api.elevenlabs.io/v1/convai/twilio/outbound-call"
ELEVEN_CONVERSATION_URL = "https://api.elevenlabs.io/v1/convai/conversations/{id}"
TWILIO_CALL_URL = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls/{call}.json"


def load_env(path: Path | None = None) -> dict[str, str]:
    """Minimal .env reader (KEY=VALUE, # comments). We never print these."""
    p = path or ROOT / ".env"
    out: dict[str, str] = {}
    if not p.exists():
        return out
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


@dataclass(frozen=True)
class CallRequest:
    to_number: str
    reason: str
    urgent: bool = False
    first_message: str = ""                                   # replaces "Ben, it's Jarvis. {reason}" (brief call)
    variables: dict[str, str] = field(default_factory=dict)  # extra ElevenLabs dynamic variables (brief, call_type)


def build_payload(req: CallRequest, env: dict[str, str]) -> dict[str, Any]:
    return {
        "agent_id": env["ELEVENLABS_AGENT_ID"],
        "agent_phone_number_id": env["ELEVENLABS_AGENT_PHONE_NUMBER_ID"],
        "to_number": req.to_number,
        # The agent's first message is "{{opening}}" (scripts/voice_setup.py). ElevenLabs rejects a first_message
        # override on phone calls, so the opening line travels as a dynamic variable instead.
        "conversation_initiation_client_data": {
            "dynamic_variables": {"reason": req.reason or "You asked me to call.", "urgent": str(req.urgent).lower(),
                                  "opening": req.first_message or f"Ben, it's Jarvis. {req.reason or 'You asked me to call.'}",
                                  **{k: str(v) for k, v in req.variables.items()}},
        },
    }


def place_call(req: CallRequest, *, env: dict[str, str] | None = None, cfg: dict[str, Any] | None = None,
               now: datetime | None = None, opener=None) -> dict[str, Any]:
    """Returns {'status': 'placed'|'suppressed'|'error', ...}. Never raises on HTTP errors."""
    env = env if env is not None else load_env()
    cfg = cfg or cached_config()
    audit = AuditLog()
    if not may_notify(now, req.urgent, cfg):
        audit.event("call_suppressed_quiet_hours", req.reason[:200])
        return {"status": "suppressed", "why": "quiet hours; pass urgent=True only if it meets the urgent rules"}
    missing = [k for k in ("ELEVENLABS_API_KEY", "ELEVENLABS_AGENT_ID", "ELEVENLABS_AGENT_PHONE_NUMBER_ID") if not env.get(k)]
    if missing:
        return {"status": "error", "why": f"missing in .env: {', '.join(missing)}"}
    body = json.dumps(build_payload(req, env)).encode()
    request = urllib.request.Request(ELEVEN_OUTBOUND_URL, data=body, method="POST",
                                     headers={"xi-api-key": env["ELEVENLABS_API_KEY"], "Content-Type": "application/json"})
    open_fn = opener or urllib.request.urlopen
    try:
        with open_fn(request, timeout=20) as resp:
            data = json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        audit.event("call_failed", f"HTTP {e.code}: {detail}")
        return {"status": "error", "why": f"ElevenLabs HTTP {e.code}", "detail": detail}
    except Exception as e:  # network, timeout
        audit.event("call_failed", str(e)[:300])
        return {"status": "error", "why": str(e)}
    if data.get("success") is False:   # HTTP 200 but ElevenLabs/Twilio refused (minutes out, number not allowed, ...)
        audit.event("call_failed", f"refused: {str(data.get('message'))[:280]}")
        return {"status": "error", "why": f"ElevenLabs refused the call: {str(data.get('message'))[:200]}", "response": data}
    audit.event("call_placed", f"to={req.to_number[-4:].rjust(len(req.to_number), '*')} urgent={req.urgent} reason={req.reason[:160]}")
    return {"status": "placed", "quiet_hours": is_quiet(now, cfg), "response": data}


# ---- did Ben actually pick up? ---------------------------------------------------------------------------------

# Carrier voicemail and iPhone call screening greetings, as ElevenLabs transcribes them on the "user" side.
_VOICEMAIL = re.compile(r"(voice ?mail|leave (a|your) message|after the (tone|beep)|not available|unavailable|"
                        r"record your (name|message)|reason for calling|mailbox|the person you('re| are) (calling|trying)|"
                        r"please stay on the line|is not accepting calls|can't take your call)", re.I)
_DIAL_FAILED = {"busy", "no-answer", "failed", "canceled"}


def classify_outcome(conversation: dict[str, Any] | None, twilio_status: str = "") -> dict[str, Any]:
    """Pure. ElevenLabs conversation (+ Twilio call status) -> {'answered': bool, 'why': str}.
    Answered means Ben said something real: no answer, busy, voicemail, and a silent line all count as not answered."""
    tw = (twilio_status or "").lower()
    if tw in _DIAL_FAILED:
        return {"answered": False, "why": tw}
    conv = conversation or {}
    if not conv:
        return {"answered": False, "why": "no conversation record"}
    user_turns = [str(t.get("message") or "").strip() for t in conv.get("transcript") or [] if t.get("role") == "user"]
    user_turns = [t for t in user_turns if sum(ch.isalpha() for ch in t) >= 2]
    if any(_VOICEMAIL.search(t) for t in user_turns[:3]):
        return {"answered": False, "why": "voicemail"}
    if user_turns:
        return {"answered": True, "why": f"{len(user_turns)} turns from Ben"}
    meta = conv.get("metadata") or {}
    agent_turns = [t for t in conv.get("transcript") or [] if t.get("role") == "agent" and str(t.get("message") or "").strip()]
    if not agent_turns and float(meta.get("call_duration_secs") or 0) <= 5:
        # Answered, then ElevenLabs dropped before saying a word (2026-10-27: a voice that fails on the phone path).
        return {"answered": False, "why": "dropped on pickup, Jarvis never spoke"}
    if conv.get("status") == "failed":
        reason = str(meta.get("termination_reason") or (meta.get("error") or {}).get("reason") or "failed")[:120]
        return {"answered": False, "why": f"call failed: {reason}"}
    return {"answered": False, "why": "Ben never spoke"}


def _get_json(url: str, headers: dict[str, str], opener=None) -> dict[str, Any]:
    with (opener or urllib.request.urlopen)(urllib.request.Request(url, headers=headers), timeout=20) as r:
        return json.loads(r.read().decode() or "{}")


def twilio_call_status(call_sid: str, env: dict[str, str], opener=None) -> str:
    sid, tok = env.get("TWILIO_ACCOUNT_SID", ""), env.get("TWILIO_AUTH_TOKEN", "")
    if not (call_sid and sid and tok):
        return ""
    auth = base64.b64encode(f"{sid}:{tok}".encode()).decode()
    try:
        return str(_get_json(TWILIO_CALL_URL.format(sid=sid, call=call_sid), {"Authorization": f"Basic {auth}"}, opener).get("status") or "")
    except Exception:
        return ""


def get_conversation(conversation_id: str, env: dict[str, str], opener=None) -> dict[str, Any]:
    try:
        return _get_json(ELEVEN_CONVERSATION_URL.format(id=conversation_id), {"xi-api-key": env.get("ELEVENLABS_API_KEY", "")}, opener)
    except Exception:
        return {}


def wait_for_outcome(placed: dict[str, Any], env: dict[str, str], *, max_wait_s: float = 900, poll_s: float = 20,
                     opener=None, sleep=time.sleep) -> dict[str, Any]:
    """After place_call: wait until the call is over (Twilio final status or ElevenLabs done/failed), then classify.
    Gives up after max_wait_s and classifies whatever is there."""
    resp = placed.get("response") or {}
    conv_id, call_sid = resp.get("conversation_id") or "", resp.get("callSid") or resp.get("call_sid") or ""
    if not conv_id and not call_sid:
        return {"answered": False, "why": "no conversation id from ElevenLabs"}
    waited, conv, tw = 0.0, {}, ""
    while True:
        tw = twilio_call_status(call_sid, env, opener)
        conv = get_conversation(conv_id, env, opener) if conv_id else {}
        if tw in _DIAL_FAILED or conv.get("status") in ("done", "failed"):
            break
        if waited >= max_wait_s:
            break
        sleep(poll_s)
        waited += poll_s
    out = classify_outcome(conv, tw)
    out.update({"conversation_id": conv_id, "twilio_status": tw,
                "duration_s": (conv.get("metadata") or {}).get("call_duration_secs")})
    return out
