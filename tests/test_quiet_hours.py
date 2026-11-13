from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from jarvis.quiet_hours import email_is_urgent, is_quiet, may_notify, meeting_is_urgent

ET = ZoneInfo("America/New_York")


def at(h, m=0):
    return datetime(2026, 10, 8, h, m, tzinfo=ET)


def test_quiet_window_crosses_midnight(cfg):
    assert is_quiet(at(23, 30), cfg)
    assert is_quiet(at(2), cfg)
    assert is_quiet(at(6, 59), cfg)
    assert not is_quiet(at(7), cfg)
    assert not is_quiet(at(12), cfg)
    assert not is_quiet(at(22, 59), cfg)


def test_quiet_uses_configured_timezone(cfg):
    # 03:00 UTC on Oct 8 is 23:00 ET on Oct 7 -> quiet.
    assert is_quiet(datetime(2026, 10, 8, 3, 0, tzinfo=ZoneInfo("UTC")), cfg)


def test_email_urgent_requires_vip_and_phrase(cfg):
    c = {**cfg, "urgent": {**cfg["urgent"], "vip_senders": ["boss@example.com", "@example.org"]}}
    assert email_is_urgent("boss@example.com", "Need this today", "", c)
    assert email_is_urgent("cofounder@example.org", "", "deadline moved", c)
    assert not email_is_urgent("boss@example.com", "Lunch next month?", "", c)
    assert not email_is_urgent("spam@random.com", "URGENT deadline", "", c)


def test_meeting_urgent_within_window_with_conflict(cfg):
    now = at(9)
    assert meeting_is_urgent(now + timedelta(minutes=90), True, now, cfg)
    assert not meeting_is_urgent(now + timedelta(minutes=90), False, now, cfg)
    assert not meeting_is_urgent(now + timedelta(hours=5), True, now, cfg)


def test_may_notify(cfg):
    assert may_notify(at(12), False, cfg)
    assert not may_notify(at(2), False, cfg)
    assert may_notify(at(2), True, cfg)
