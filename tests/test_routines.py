from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from jarvis import routines

ET = ZoneInfo("America/New_York")


def cfg_with_vips(cfg):
    return {**cfg, "urgent": {**cfg["urgent"], "vip_senders": ["chris@example.com", "@example.org"]}}


def test_vip_query_builds_or_clause():
    q = routines._vip_query(["a@b.com", "@example.org", ""])
    assert q == "newer_than:1d (from:a@b.com OR from:example.org)"


def test_inbox_no_vips_configured_never_wakes(cfg):
    c = {**cfg, "urgent": {**cfg["urgent"], "vip_senders": []}}
    r, st = routines.inbox_check(datetime(2026, 10, 9, 10, 0, tzinfo=ET), c, {}, search=lambda q, n: [{"id": "x"}])
    assert r["wakeAgent"] is False and "vip" in r["note"]


def test_inbox_wakes_once_per_message(cfg):
    c = cfg_with_vips(cfg)
    msgs = [{"id": "m1", "from": "chris@example.com", "subject": "Need this today", "snippet": "deadline"}]
    now = datetime(2026, 10, 9, 10, 0, tzinfo=ET)
    r1, st1 = routines.inbox_check(now, c, {}, search=lambda q, n: msgs)
    assert r1["wakeAgent"] is True and r1["context"]["any_urgent"] is True and r1["context"]["may_call"] is True
    r2, st2 = routines.inbox_check(now + timedelta(minutes=30), c, st1, search=lambda q, n: msgs)
    assert r2["wakeAgent"] is False  # already seen


def test_inbox_holds_non_urgent_during_quiet_hours(cfg):
    c = cfg_with_vips(cfg)
    msgs = [{"id": "m2", "from": "alana@example.com", "subject": "Lunch next month?", "snippet": "no rush"}]
    night = datetime(2026, 10, 9, 2, 0, tzinfo=ET)
    r, st = routines.inbox_check(night, c, {}, search=lambda q, n: msgs)
    assert r["wakeAgent"] is False and st["held"][0]["id"] == "m2"
    # Next morning, a new VIP mail wakes the agent and carries the held item along.
    morning = datetime(2026, 10, 9, 9, 0, tzinfo=ET)
    r2, st2 = routines.inbox_check(morning, c, st, search=lambda q, n: msgs + [{"id": "m3", "from": "chris@example.com", "subject": "hi", "snippet": ""}])
    assert r2["wakeAgent"] is True and r2["context"]["held_from_overnight"][0]["id"] == "m2" and st2["held"] == []


def test_inbox_urgent_breaks_quiet_hours(cfg):
    c = cfg_with_vips(cfg)
    msgs = [{"id": "m4", "from": "chris@example.com", "subject": "URGENT: wire signature needed", "snippet": ""}]
    r, _ = routines.inbox_check(datetime(2026, 10, 9, 2, 0, tzinfo=ET), c, {}, search=lambda q, n: msgs)
    assert r["wakeAgent"] is True and r["context"]["quiet_hours"] is True and r["context"]["may_call"] is True


def _ev(eid, start, minutes=60, **kw):
    return {"id": eid, "summary": kw.get("summary", eid), "start": start.isoformat(), "end": (start + timedelta(minutes=minutes)).isoformat(),
            "attendees": kw.get("attendees", []), "location": "", "description": ""}


def test_meeting_prep_window_and_dedupe(cfg):
    now = datetime(2026, 10, 9, 13, 0, tzinfo=ET)
    events = [_ev("soon", now + timedelta(minutes=30)), _ev("later", now + timedelta(minutes=90)), _ev("past", now - timedelta(minutes=10))]
    r, st = routines.meeting_check(now, cfg, {}, list_events=lambda a, b: events)
    assert r["wakeAgent"] is True and [m["id"] for m in r["context"]["meetings"]] == ["soon"]
    r2, _ = routines.meeting_check(now + timedelta(minutes=5), cfg, st, list_events=lambda a, b: events)
    assert r2["wakeAgent"] is False


def test_meeting_prep_detects_conflict(cfg):
    now = datetime(2026, 10, 9, 13, 0, tzinfo=ET)
    a = _ev("a", now + timedelta(minutes=30), 60, summary="Alana")
    b = _ev("b", now + timedelta(minutes=45), 60, summary="Class")
    r, _ = routines.meeting_check(now, cfg, {}, list_events=lambda a_, b_: [a, b])
    m = {x["id"]: x for x in r["context"]["meetings"]}
    assert m["a"]["conflicts_with"] == ["Class"] and r["context"]["urgent_conflicts"][0]["id"] == "a"


def test_run_never_wakes_on_internal_error(monkeypatch, capsys):
    monkeypatch.setattr(routines, "gmail_search", lambda q, n: (_ for _ in ()).throw(RuntimeError("gapi down")))
    monkeypatch.setattr(routines, "cached_config", lambda: {"timezone": "America/New_York", "urgent": {"vip_senders": ["x@y.z"]}})
    assert routines.run("inbox") == 0
    out = capsys.readouterr().out.strip().splitlines()[-1]
    assert '"wakeAgent": false' in out and "gapi down" in out
