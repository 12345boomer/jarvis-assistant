import importlib
import json

import pytest
from fastapi.testclient import TestClient

from jarvis.audit import AuditLog


@pytest.fixture
def client(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_BOT_TOKEN=x\nHONCHO_API_KEY=y\nJARVIS_PUBLIC_URL=https://example.ts.net\nTWILIO_FROM_NUMBER=+15550001111\n")
    monkeypatch.setenv("JARVIS_ENV_FILE", str(env))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    (tmp_path / "hermes" / "cron").mkdir(parents=True)
    (tmp_path / "hermes" / "cron" / "jobs.json").write_text(json.dumps([{"id": "abc", "name": "morning-brief", "schedule": {"display": "5 7 * * *"},
                                                                           "next_run_at": "2026-10-10T07:05:00-04:00", "last_status": "ok", "enabled": True, "state": "scheduled", "script": None}]))
    import dashboard.app as appmod
    importlib.reload(appmod)
    return TestClient(appmod.app)


def test_status_shape(client):
    s = client.get("/api/status").json()
    assert set(s) >= {"now", "quiet_hours", "spend", "services", "cron"}
    assert s["services"]["telegram"] is True and s["services"]["funnel_url"].startswith("https://")
    assert s["cron"][0]["name"] == "morning-brief" and s["cron"][0]["gated"] is False


def test_feed_and_approvals_reflect_audit(client):
    log = AuditLog()
    log.record_call(tool="gmail_send_message", args={"to": "a@b.c"}, tier=2, decision="ask", rule="r", reason="needs ok", session_id="s")
    log.event("spend_alert", "1.60 of 2.00")
    f = client.get("/api/feed").json()["items"]
    assert any(i["kind"] == "event" and i["detail"] == "1.60 of 2.00" for i in f)
    a = client.get("/api/approvals").json()
    assert a["pending"][0]["tool"] == "gmail_send_message" and a["decided"] == []
    log.record_approval(session_id="s", choice="deny", approver="ben:gateway")
    a = client.get("/api/approvals").json()
    assert a["pending"] == [] and a["decided"][0]["approval"] == "deny"


def test_index_and_health(client):
    assert client.get("/health").json() == {"ok": True}
    r = client.get("/")
    assert r.status_code == 200 and "JARVIS" in r.text and "/api/stream" in r.text


def test_voice_requires_config(client):
    assert client.get("/api/voice/signed-url").status_code == 503


def test_hud_wake_logs_event(client):
    assert client.post("/api/hud/wake").json() == {"ok": True}
    assert AuditLog().recent_events(1)[0]["kind"] == "wake_word"


def test_voice_active_flag_roundtrip(client):
    assert client.get("/api/voice/active").json()["active"] is False
    r = client.post("/api/voice/active", json={"active": True, "source": "hud"}).json()
    assert r["active"] is True and r["source"] == "hud"
    assert client.get("/api/voice/active").json()["active"] is True
    assert AuditLog().recent_events(1)[0]["kind"] == "voice_session_start"
    client.post("/api/voice/active", json={"active": False})
    assert client.get("/api/voice/active").json()["active"] is False


def test_token_auth(monkeypatch, tmp_path):
    env = tmp_path / ".env"; env.write_text("DASHBOARD_SECRET=s3cret\n")
    monkeypatch.setenv("JARVIS_ENV_FILE", str(env)); monkeypatch.setenv("HERMES_HOME", str(tmp_path / "h"))
    import dashboard.app as appmod
    importlib.reload(appmod)
    c = TestClient(appmod.app)
    assert c.get("/api/status").status_code == 401
    r = c.get("/api/status?token=s3cret", follow_redirects=False)
    assert r.status_code in (302, 307) and "jarvis=s3cret" in r.headers.get("set-cookie", "")
    c.cookies.clear()
    r = c.get("/?token=s3cret&wake=1", follow_redirects=False)
    assert r.headers["location"].endswith("/?wake=1")  # other params survive, token is stripped
    assert c.get("/api/status", cookies={"jarvis": "s3cret"}).status_code == 200
