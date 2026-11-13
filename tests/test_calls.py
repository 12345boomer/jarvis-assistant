import io
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from jarvis.audit import AuditLog
from jarvis.calls import CallRequest, build_payload, load_env, place_call

ET = ZoneInfo("America/New_York")
ENV = {"ELEVENLABS_API_KEY": "k", "ELEVENLABS_AGENT_ID": "agent1", "ELEVENLABS_AGENT_PHONE_NUMBER_ID": "pn1", "BEN_PHONE_NUMBER": "+18605550100"}


class FakeResp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


def fake_opener(captured):
    def _open(request, timeout=0):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode())
        return FakeResp(b'{"success": true, "callSid": "CA123"}')
    return _open


def test_payload_shape():
    p = build_payload(CallRequest("+18605550100", "Chris K needs a yes on the contract today", urgent=True), ENV)
    assert p["agent_id"] == "agent1" and p["agent_phone_number_id"] == "pn1" and p["to_number"] == "+18605550100"
    assert p["conversation_initiation_client_data"]["dynamic_variables"]["urgent"] == "true"
    assert p["conversation_initiation_client_data"]["dynamic_variables"]["opening"].startswith("Ben, it's Jarvis. Chris K")
    assert "conversation_config_override" not in p["conversation_initiation_client_data"]   # rejected on phone calls
    assert p["conversation_initiation_client_data"]["dynamic_variables"]["reason"].startswith("Chris K")


def test_call_placed_outside_quiet_hours(cfg):
    cap = {}
    r = place_call(CallRequest("+18605550100", "test"), env=ENV, cfg=cfg, now=datetime(2026, 10, 9, 14, 0, tzinfo=ET), opener=fake_opener(cap))
    assert r["status"] == "placed" and cap["headers"]["Xi-api-key"] == "k"
    assert AuditLog().recent_events(1)[0]["kind"] == "call_placed"


def test_call_suppressed_in_quiet_hours(cfg):
    cap = {}
    r = place_call(CallRequest("+18605550100", "fyi"), env=ENV, cfg=cfg, now=datetime(2026, 10, 9, 2, 0, tzinfo=ET), opener=fake_opener(cap))
    assert r["status"] == "suppressed" and "body" not in cap
    assert AuditLog().recent_events(1)[0]["kind"] == "call_suppressed_quiet_hours"


def test_urgent_call_breaks_quiet_hours(cfg):
    cap = {}
    r = place_call(CallRequest("+18605550100", "VIP", urgent=True), env=ENV, cfg=cfg, now=datetime(2026, 10, 9, 2, 0, tzinfo=ET), opener=fake_opener(cap))
    assert r["status"] == "placed" and r["quiet_hours"] is True


def test_missing_env_is_reported(cfg):
    r = place_call(CallRequest("+1", "x"), env={}, cfg=cfg, now=datetime(2026, 10, 9, 14, 0, tzinfo=ET), opener=fake_opener({}))
    assert r["status"] == "error" and "ELEVENLABS_API_KEY" in r["why"]


def test_load_env(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# c\nA=1\nB=\"two\"\nC='3'\n\nBAD\n")
    assert load_env(p) == {"A": "1", "B": "two", "C": "3"}
