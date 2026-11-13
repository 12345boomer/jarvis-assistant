#!/usr/bin/env python3
"""Phase 5 setup: ElevenLabs voice agent + Twilio number, driven by .env. Stdlib only.

Subcommands (all idempotent; values are written back to .env, never printed):
  agent          create or update the ElevenLabs agent (prompt, George voice, Custom LLM -> Hermes)
  twilio-number  claim the Twilio trial number (or reuse the one already on the account) -> TWILIO_FROM_NUMBER
  twilio-verify  ask Twilio to call BEN_PHONE_NUMBER with a validation code (needed on trial accounts)
  phone-import   import TWILIO_FROM_NUMBER into ElevenLabs and attach the agent -> ELEVENLABS_AGENT_PHONE_NUMBER_ID
  status         show what is configured (masked)

Run with any Python 3.10+: python3 scripts/voice_setup.py <subcommand>
"""

from __future__ import annotations

import base64
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
PROMPT_PATH = ROOT / "config" / "voice-agent-prompt.md"
EL = "https://api.elevenlabs.io"
TW = "https://api.twilio.com/2010-04-01"
AGENT_NAME = "Jarvis"
FIRST_MESSAGE = "Hey, sir. How can I help you?"  # inbound greeting, spoken by ElevenLabs the instant it answers (outbound overrides it with the reason)
DEFAULT_VOICE = "JBFqnCBsd6RMkjVDRZzb"  # George, British male


# ---- .env helpers ----------------------------------------------------------

def read_env() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in ENV_PATH.read_text().splitlines():
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k, v = s.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def write_env(key: str, value: str) -> None:
    """Set KEY=value in .env in place (append if missing). Never logs the value."""
    text = ENV_PATH.read_text()
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.M)
    if pattern.search(text):
        text = pattern.sub(f"{key}={value}", text, count=1)
    else:
        text = text.rstrip("\n") + f"\n{key}={value}\n"
    ENV_PATH.write_text(text)
    ENV_PATH.chmod(0o600)
    print(f"  .env  {key} = <set>")


def need(env: dict[str, str], *keys: str) -> None:
    missing = [k for k in keys if not env.get(k)]
    if missing:
        sys.exit(f"missing in .env: {', '.join(missing)}")


# ---- HTTP helpers ----------------------------------------------------------

def call(method: str, url: str, *, headers: dict[str, str], body: bytes | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"error": raw[:500]}


def el(env: dict[str, str], method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    headers = {"xi-api-key": env["ELEVENLABS_API_KEY"], "Content-Type": "application/json"}
    body = json.dumps(payload).encode() if payload is not None else None
    return call(method, EL + path, headers=headers, body=body)


def tw(env: dict[str, str], method: str, path: str, form: dict | None = None) -> tuple[int, dict]:
    sid, tok = env["TWILIO_ACCOUNT_SID"], env["TWILIO_AUTH_TOKEN"]
    auth = base64.b64encode(f"{sid}:{tok}".encode()).decode()
    headers = {"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"}
    body = urllib.parse.urlencode(form).encode() if form is not None else None
    return call(method, f"{TW}/Accounts/{sid}{path}", headers=headers, body=body)


# ---- ElevenLabs agent ------------------------------------------------------

def agent_config(env: dict[str, str]) -> dict:
    prompt = PROMPT_PATH.read_text()
    prompt = re.sub(r"^# .*\n", "", prompt, count=1).strip()  # drop the file's title line
    public = env["JARVIS_PUBLIC_URL"].rstrip("/")
    return {
        "name": AGENT_NAME,
        "tags": ["jarvis"],
        "conversation_config": {
            "agent": {
                # The opening line is a dynamic variable, not a first_message override: since ~Oct 19 ElevenLabs rejects the
                # override on phone (Twilio) and WebRTC sessions ("Override for field 'first_message' is not allowed by config")
                # even with the override enabled below. Inbound calls get the placeholder default.
                # The opening line is the {{opening}} dynamic variable, not a first_message override: since ~Oct 19 ElevenLabs
                # rejects that override on phone and WebRTC sessions ("Override for field 'first_message' is not allowed
                # by config") although it is enabled below. Outbound calls (jarvis/calls.py) and the HUD send `opening`;
                # inbound Twilio calls get it from the bridge's /elevenlabs/init webhook (set in cmd_agent).
                "first_message": "{{opening}}",
                "disable_first_message_interruptions": True,   # background chatter can't cut the greeting off
                "language": "en",
                "dynamic_variables": {"dynamic_variable_placeholders": {"opening": FIRST_MESSAGE, "reason": "", "urgent": "false", "call_type": "inbound", "brief": "", "brief_detail": ""}},
                "prompt": {
                    "prompt": prompt,
                    "llm": "custom-llm",
                    "custom_llm": {
                        "url": f"{public}/v1",
                        "model_id": "jarvis-voice",
                        "api_key": {"secret_id": env["ELEVENLABS_SECRET_ID"]},
                        "api_type": "chat_completions",
                    },
                    "temperature": 0.4,
                    # ElevenLabs hangs up when the LLM calls this (the bridge relays the call).
                    "built_in_tools": {"end_call": {"type": "system", "name": "end_call", "description": "",
                                                    "params": {"system_tool_type": "end_call"}}},
                },
            },
            # Calm, consistent delivery (research 2026-10-09): higher stability, moderate similarity,
            # phone-grade output. v4 ignores style/speed.
            "tts": {"voice_id": env.get("ELEVENLABS_VOICE_ID") or DEFAULT_VOICE, "model_id": "eleven_v4_turbo",
                    "stability": 0.65, "similarity_boost": 0.78, "agent_output_audio_format": "ulaw_8000"},
            # turn_timeout: seconds of silence before the agent is allowed to speak again. 7 s made it
            # nag ("I'm still here, sir"); 30 is the platform max. silence_end_call_timeout hangs up
            # after a long quiet stretch instead of filling it.
            "turn": {"turn_timeout": 30, "silence_end_call_timeout": 30, "turn_eagerness": "normal", "initial_wait_time": 1},
        },
        # Outbound calls override the first message with the reason for calling (see jarvis/calls.py).
        "platform_settings": {"overrides": {"conversation_config_override": {"agent": {"first_message": True}},
                                            "enable_conversation_initiation_client_data_from_webhook": True}},
    }


def cmd_agent(env: dict[str, str]) -> None:
    need(env, "ELEVENLABS_API_KEY", "ELEVENLABS_SECRET_ID", "JARVIS_PUBLIC_URL")
    # Workspace setting: inbound calls fetch their dynamic variables (the {{opening}} greeting) from the bridge.
    hook = {"conversation_initiation_client_data_webhook": {"url": f"{env['JARVIS_PUBLIC_URL'].rstrip('/')}/elevenlabs/init", "request_headers": {}}}
    code, data = el(env, "PATCH", "/v1/convai/settings", hook)
    print(f"  init webhook -> {code}" + ("" if code == 200 else f" {data}"))
    if code != 200:
        sys.exit("refusing to switch the first message to {{opening}} without the inbound webhook")
    cfg = agent_config(env)
    if env.get("ELEVENLABS_AGENT_ID"):
        code, data = el(env, "PATCH", f"/v1/convai/agents/{env['ELEVENLABS_AGENT_ID']}", cfg)
        print(f"  agent update -> {code}" + ("" if code == 200 else f" {data}"))
        return
    code, data = el(env, "POST", "/v1/convai/agents/create", cfg)
    if code != 200 or not data.get("agent_id"):
        sys.exit(f"agent create failed: {code} {data}")
    write_env("ELEVENLABS_AGENT_ID", data["agent_id"])
    print("  agent created")


# ---- Twilio ---------------------------------------------------------------

def cmd_twilio_number(env: dict[str, str]) -> None:
    need(env, "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN")
    code, data = tw(env, "GET", "/IncomingPhoneNumbers.json?PageSize=5")
    nums = data.get("incoming_phone_numbers", []) if code == 200 else []
    if nums:
        number = nums[0]["phone_number"]
        print(f"  existing Twilio number: {number}")
    else:
        area = (env.get("BEN_PHONE_NUMBER") or "+1201")[2:5]
        code, data = tw(env, "GET", f"/AvailablePhoneNumbers/US/Local.json?AreaCode={area}&VoiceEnabled=true&PageSize=1")
        avail = data.get("available_phone_numbers", []) if code == 200 else []
        if not avail:
            code, data = tw(env, "GET", "/AvailablePhoneNumbers/US/Local.json?VoiceEnabled=true&PageSize=1")
            avail = data.get("available_phone_numbers", []) if code == 200 else []
        if not avail:
            sys.exit(f"no available numbers: {code} {data}")
        number = avail[0]["phone_number"]
        code, data = tw(env, "POST", "/IncomingPhoneNumbers.json", {"PhoneNumber": number, "FriendlyName": "Jarvis"})
        if code not in (200, 201):
            sys.exit(f"number provisioning failed: {code} {data}")
        print(f"  provisioned Twilio number: {number}")
    write_env("TWILIO_FROM_NUMBER", number)


def cmd_twilio_verify(env: dict[str, str]) -> None:
    need(env, "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "BEN_PHONE_NUMBER")
    code, data = tw(env, "GET", "/OutgoingCallerIds.json?PageSize=20")
    ids = [c["phone_number"] for c in data.get("outgoing_caller_ids", [])] if code == 200 else []
    if env["BEN_PHONE_NUMBER"] in ids:
        print("  Ben's number is already a verified caller ID")
        return
    code, data = tw(env, "POST", "/OutgoingCallerIds.json",
                    {"PhoneNumber": env["BEN_PHONE_NUMBER"], "FriendlyName": "Ben", "CallDelay": "5"})
    if code not in (200, 201):
        sys.exit(f"verification start failed: {code} {data}")
    print(f"  Twilio is calling {env['BEN_PHONE_NUMBER']} now. Enter this code on the keypad: {data.get('validation_code')}")


# ---- ElevenLabs phone import ------------------------------------------------

def cmd_phone_import(env: dict[str, str]) -> None:
    need(env, "ELEVENLABS_API_KEY", "ELEVENLABS_AGENT_ID", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER")
    if env.get("ELEVENLABS_AGENT_PHONE_NUMBER_ID"):
        print("  phone number already imported")
        return
    payload = {
        "phone_number": env["TWILIO_FROM_NUMBER"],
        "label": "Jarvis (Twilio)",
        "provider": "twilio",
        "sid": env["TWILIO_ACCOUNT_SID"],
        "token": env["TWILIO_AUTH_TOKEN"],
        "agent_id": env["ELEVENLABS_AGENT_ID"],
        "enable_sms": False,
    }
    code, data = el(env, "POST", "/v1/convai/phone-numbers", payload)
    if code != 200 or not data.get("phone_number_id"):
        sys.exit(f"phone import failed: {code} {data}")
    write_env("ELEVENLABS_AGENT_PHONE_NUMBER_ID", data["phone_number_id"])
    print("  number imported and attached to the agent")


def cmd_status(env: dict[str, str]) -> None:
    for k in ("ELEVENLABS_API_KEY", "ELEVENLABS_SECRET_ID", "ELEVENLABS_AGENT_ID", "ELEVENLABS_AGENT_PHONE_NUMBER_ID",
              "JARVIS_PUBLIC_URL", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER", "BEN_PHONE_NUMBER"):
        v = env.get(k, "")
        shown = v if k in ("JARVIS_PUBLIC_URL", "TWILIO_FROM_NUMBER", "BEN_PHONE_NUMBER") else ("<set>" if v else "")
        print(f"  {k:<34} {shown or '<empty>'}")


COMMANDS = {"agent": cmd_agent, "twilio-number": cmd_twilio_number, "twilio-verify": cmd_twilio_verify,
            "phone-import": cmd_phone_import, "status": cmd_status}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        sys.exit(__doc__)
    COMMANDS[sys.argv[1]](read_env())
