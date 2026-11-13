"""Quiet hours and the 'urgent' rules that may break them. Pure functions, no I/O."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo


def _parse_hhmm(s: str) -> time:
    h, m = str(s).split(":")
    return time(int(h), int(m))


def is_quiet(now: datetime | None, cfg: dict[str, Any]) -> bool:
    """True if `now` falls inside quiet hours. Handles windows that cross midnight."""
    qh = cfg.get("quiet_hours") or {}
    if not qh:
        return False
    tz = ZoneInfo(cfg.get("timezone", "America/New_York"))
    now = (now or datetime.now(tz)).astimezone(tz)
    start, end = _parse_hhmm(qh.get("start", "23:00")), _parse_hhmm(qh.get("end", "07:00"))
    t = now.time()
    if start <= end:
        return start <= t < end
    return t >= start or t < end


def email_is_urgent(sender: str, subject: str, body: str, cfg: dict[str, Any]) -> bool:
    """VIP sender AND a time-sensitive phrase. Both are required."""
    u = cfg.get("urgent") or {}
    vips = [v.lower() for v in (u.get("vip_senders") or [])]
    sender_l = (sender or "").lower()
    is_vip = any(sender_l == v or (v.startswith("@") and sender_l.endswith(v)) or v in sender_l for v in vips)
    if not is_vip:
        return False
    text = f"{subject}\n{body}".lower()
    return any(p.lower() in text for p in (u.get("time_sensitive_phrases") or []))


def meeting_is_urgent(start: datetime, has_conflict: bool, now: datetime, cfg: dict[str, Any]) -> bool:
    u = cfg.get("urgent") or {}
    window = timedelta(minutes=int(u.get("meeting_conflict_window_minutes", 120)))
    return has_conflict and timedelta(0) <= (start - now) <= window


def may_notify(now: datetime | None, urgent: bool, cfg: dict[str, Any]) -> bool:
    """Outside quiet hours anything may notify; inside, only urgent items."""
    return urgent or not is_quiet(now, cfg)
