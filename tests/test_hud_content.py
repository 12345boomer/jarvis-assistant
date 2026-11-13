import importlib
import json

import pytest
from fastapi.testclient import TestClient

from jarvis import tasks as taskstore


@pytest.fixture
def client(monkeypatch, tmp_path):
    env = tmp_path / ".env"; env.write_text("TELEGRAM_BOT_TOKEN=x\n")
    monkeypatch.setenv("JARVIS_ENV_FILE", str(env))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setenv("JARVIS_TASKS_FILE", str(tmp_path / "tasks.json"))
    importlib.reload(taskstore)
    import dashboard.app as appmod
    importlib.reload(appmod)
    appmod._cache.clear()
    return TestClient(appmod.app), appmod


def test_tasks_roundtrip(client):
    c, _ = client
    t = c.post("/api/tasks", json={"text": "Email Chris the deck"}).json()
    assert t["text"] == "Email Chris the deck" and t["done"] is False
    assert len(c.get("/api/tasks").json()["tasks"]) == 1
    assert c.post(f"/api/tasks/{t['id']}/done").json()["done"] is True
    assert c.get("/api/tasks").json()["tasks"] == []
    assert len(c.get("/api/tasks?all=true").json()["tasks"]) == 1
    assert c.delete(f"/api/tasks/{t['id']}").json()["removed"] is True
    assert c.post("/api/tasks", json={"text": "  "}).status_code == 400


def test_tasks_done_by_text(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_TASKS_FILE", str(tmp_path / "t.json")); importlib.reload(taskstore)
    taskstore.add("call mom")
    assert taskstore.complete("Call Mom")["done"] is True


def test_today_and_inbox_shape(client, monkeypatch):
    c, appmod = client
    async def fake_gapi(*args):
        if args[0] == "calendar":
            return [{"id": "e1", "summary": "OFP", "start": "2099-01-01T16:00:00-05:00", "end": "2099-01-01T18:00:00-05:00"}]
        return [{"id": "m1", "from": "Chris <chris@example.com>", "subject": "Deck by today", "snippet": "deadline", "date": "x"},
                {"id": "m2", "from": "News <n@x.com>", "subject": "Hello", "snippet": "", "date": "x"}]
    monkeypatch.setattr(appmod, "_gapi", fake_gapi)
    t = c.get("/api/today").json()
    assert t["events"][0]["summary"] == "OFP" and t["next"]["id"] == "e1"
    i = c.get("/api/inbox").json()
    assert i["unread"] == 2 and i["items"][0]["vip"] is True and i["items"][0]["from"] == "Chris"


def test_now_doing_idle_and_busy(client):
    c, appmod = client
    assert c.get("/api/now").json()["state"] == "idle"
    from jarvis.audit import AuditLog
    AuditLog().record_call(tool="terminal", args={"command": "ls -la"}, tier=1, decision="allow")
    n = c.get("/api/now").json()
    assert n["state"] == "busy" and "running a command" in n["text"] and "ls -la" in n["text"]


def test_cached_keeps_last_good_value(client):
    c, appmod = client
    calls = {"n": 0}
    async def flaky():
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("boom")
        return {"v": calls["n"]}
    import asyncio
    assert asyncio.run(appmod.cached("k", 0, flaky)) == {"v": 1}
    r = asyncio.run(appmod.cached("k", 0, flaky))
    assert r["v"] == 1 and "boom" in r["error"]


def test_news_parses_rss(client, monkeypatch):
    c, appmod = client
    rss = "<rss><channel><title>CNBC - Top</title><item><title>Markets rally</title><link>http://x</link></item><item><title>Markets rally</title><link>http://y</link></item></channel></rss>"
    class R:  # fake response
        text = rss
    class C:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, headers=None): return R()
    monkeypatch.setattr(appmod.httpx, "AsyncClient", C)
    monkeypatch.setattr(appmod, "_hud_cfg", lambda: {"news_feeds": ["http://feed"]})
    n = c.get("/api/news").json()
    assert n["items"] == [{"title": "Markets rally", "link": "http://x", "pub": "", "source": "CNBC"}]
    appmod._cache.clear()
    monkeypatch.setattr(appmod, "_hud_cfg", lambda: {"news_feeds": ["https://www.cnbc.com/id/1/device/rss/rss.html"]})
    assert c.get("/api/news").json()["items"][0]["source"] == "CNBC"


def test_brief_composes_from_hud_data(client):
    from datetime import datetime
    c, appmod = client
    today = {"events": [{"summary": "Econ", "start": "2026-10-10T11:00:00-04:00", "end": "2026-10-10T12:00:00-04:00", "past": True},
                        {"summary": "OFC meeting", "start": "2026-10-10T16:00:00-04:00", "end": "2026-10-10T18:00:00-04:00", "location": "BUSN 106", "past": False}]}
    inbox = {"items": [{"from": "Chris", "subject": "Deck for Monday", "vip": True}, {"from": "Newsletter", "subject": "x", "vip": False}]}
    weather = {"temp": 61, "desc": "Partly cloudy", "hi": 66, "lo": 48, "rain": 20}
    markets = {"quotes": [{"symbol": "NVDA", "price": 190.1, "change_pct": 2.4}, {"symbol": "SPY", "price": 600, "change_pct": -0.3}]}
    held = [{"from": "Eli <eli@example.com>", "subject": "Proposal v2"}]
    d = appmod.compose_brief(today, inbox, weather, markets, [{"text": "Email Alana", "done": False}], {"items": [{"title": "Fed holds"}]}, held,
                             datetime(2026, 10, 10, 8, 0))
    titles = [s["title"] for s in d["sections"]]
    assert titles == ["Weather", "Today", "Needs you", "Tasks", "Markets", "Headlines"]
    assert d["sections"][1]["lines"] == ["4pm–6pm  OFC meeting  · BUSN 106"]            # past event dropped
    assert d["sections"][2]["lines"] == ["Chris: Deck for Monday", "(overnight) Eli: Proposal v2", "1 other unread"]
    assert d["spoken"].startswith("Morning, Ben. 61° and Partly cloudy")
    assert "OFC meeting at 4pm" in d["spoken"] and "Chris" in d["spoken"] and "NVDA +2.4 percent" in d["spoken"]


def test_brief_endpoint_and_hud_trigger(client, monkeypatch):
    c, appmod = client
    async def fake_today(): return {"events": []}
    async def empty(): return {}
    monkeypatch.setattr(appmod, "_today", fake_today)
    for name in ("_inbox", "_weather", "_markets", "_news"):
        monkeypatch.setattr(appmod, name, empty)
    d = c.get("/api/brief").json()
    assert d["sections"][0] == {"title": "Today", "lines": ["Nothing left on the calendar."]}
    assert "Nothing on the calendar" in d["spoken"]
    r = c.post("/api/hud/brief", json={}).json()
    assert r == {"ok": True, "opened": False} and appmod._brief_requested > 0


def test_brief_includes_due_section(client):
    from datetime import datetime
    c, appmod = client
    school = {"ok": True, "items": [{"course": "FNCE 3101", "title": "Problem Set 3", "due_h": 30.0, "urgency": "soon"},
                                    {"course": "ECON 2201", "title": "Midterm", "due_h": 200.0, "urgency": "later"}]}
    d = appmod.compose_brief({"events": []}, {}, {}, {}, [], {}, [], datetime(2026, 10, 10, 8, 0), school)
    due = next(s for s in d["sections"] if s["title"] == "Due")
    assert due["lines"] == ["FNCE 3101: Problem Set 3  · in 1d 6h"]
    assert "FNCE 3101 Problem Set 3 is due in 1d 6h" in d["spoken"]


def test_call_mirror_endpoints(client, monkeypatch):
    c, appmod = client
    monkeypatch.setattr(appmod.subprocess, "Popen", lambda *a, **k: None)
    assert c.get("/api/call").json()["active"] is False
    assert c.post("/api/call/start").json() == {"ok": True}
    assert c.post("/api/call/event", json={"role": "user", "text": "what's the weather"}).json()["ok"] is True
    assert c.post("/api/call/event", json={"role": "assistant", "text": "Sixty and clear."}).json()["ok"] is True
    d = c.get("/api/call").json()
    assert d["active"] is True and [l["role"] for l in d["lines"]] == ["user", "assistant"]
    assert c.get("/api/voice/active").json()["source"] == "phone"      # wake listener pauses during the call
    assert c.post("/api/call/end").json() == {"ok": True}
    assert c.get("/api/call").json()["active"] is False and c.get("/api/voice/active").json()["active"] is False
    states = [p["state"] for _, p in appmod._call_events]
    assert states[-4:] == ["start", "line", "line", "end"]
