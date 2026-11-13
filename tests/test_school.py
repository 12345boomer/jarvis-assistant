from datetime import datetime
from zoneinfo import ZoneInfo

from jarvis import school
from jarvis.tiers import ASK, DO, classify

TZ = ZoneInfo("America/New_York")
ICS = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:a1
DTSTART;TZID=America/New_York:20261012T235900
DTEND;TZID=America/New_York:20261012T235900
SUMMARY:Problem Set 3
DESCRIPTION:FNCE 3101-001 Financial Management\\, Fall 2026
URL:https://lms.uconn.edu/ultra/courses/_1_1/outline
END:VEVENT
BEGIN:VEVENT
UID:a2
DTSTART:20261020T160000Z
SUMMARY:ECON2201 Midterm 1
 (in class)
END:VEVENT
BEGIN:VEVENT
UID:a3
DTSTART;VALUE=DATE:20261101
SUMMARY:Far away thing
END:VEVENT
BEGIN:VEVENT
UID:old
DTSTART:20260901T120000Z
SUMMARY:Old quiz
END:VEVENT
END:VCALENDAR
"""


def test_parse_and_upcoming():
    events = school.parse_ics(ICS)
    assert len(events) == 4
    assert events[0]["summary"] == "Problem Set 3" and "Fall 2026" in events[0]["description"]
    assert events[1]["summary"] == "ECON2201 Midterm 1(in class)"            # folded line (RFC 5545: the leading space is the fold)
    now = datetime(2026, 10, 10, 20, 0, tzinfo=TZ)
    items = school.upcoming(events, now=now, days=14)
    assert [i["course"] for i in items] == ["FNCE 3101", "ECON 2201"]       # old and far-away dropped
    assert items[0]["urgency"] == "soon" and 50 < items[0]["due_h"] < 53
    assert items[1]["due"].startswith("2026-10-20T12:00")                     # UTC -> ET


def test_due_without_feed(monkeypatch):
    monkeypatch.setattr(school, "feed_url", lambda: "")
    d = school.due()
    assert d["ok"] is False and "setup" in d["error"]


def test_api_path_guard():
    import pytest
    with pytest.raises(ValueError):
        school.api_get("/evil")


def test_school_commands_are_tier1(cfg):
    p = cfg["permissions"]
    assert classify("terminal", {"command": "$JARVIS school due --days 7"}, p).tier == DO
    assert classify("terminal", {"command": "$JARVIS school api /learn/api/v1/users/me"}, p).tier == DO
    assert classify("terminal", {"command": "$JARVIS browser open https://lms.uconn.edu"}, p).tier == DO


def test_jarvis_browser_is_hidden_by_default():
    from jarvis import cdp
    hidden = cdp.launch_args("https://x")
    assert "--headless=new" in hidden and hidden[0].endswith("Google Chrome") and "--remote-debugging-port=9333" in hidden
    visible = cdp.launch_args("https://x", visible=True)
    assert visible[:4] == ["open", "-n", "-a", "Google Chrome"] and "--headless=new" not in visible
