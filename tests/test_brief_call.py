from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from jarvis import brief_call as b
from jarvis.audit import AuditLog
from jarvis.calls import classify_outcome, wait_for_outcome

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 26, 9, 0, tzinfo=ET)   # a Monday
PERSONAL = {"name": "personal", "label": "personal", "email": "you@gmail.com"}
DB = {"name": "dailybuilt", "label": "DailyBuilt", "email": "you@yourbusiness.com"}


def email(i, sender, subject="Quick question", date="Sun, 25 Oct 2026 18:00:00 -0400", thread=None, labels=None):
    return {"id": f"m{i}", "threadId": thread or f"t{i}", "from": sender, "subject": subject, "snippet": "Can you send it?",
            "date": date, "labels": labels or ["INBOX"]}


def pick(who, need, src):
    return {"who": who, "need": need, "src": src}


# ---- compose: empty sections, order, word cap ------------------------------------------------------------------

def test_empty_sections_are_skipped():
    out = b.compose({"overdue": [], "today": [], "later": []}, [], ["10 AM, FNCE 3101 lecture"], [])
    assert "School." not in out["text"] and "Email." not in out["text"]
    assert out["text"].startswith(b.OPENER) and "Your day. 10 AM, FNCE 3101 lecture." in out["text"]
    assert out["text"].endswith(b.CLOSER) and "is that all" in out["text"].lower()


def test_everything_empty_says_so():
    out = b.compose({}, [], [], [])
    assert "Nothing due, no email that needs you, and a clear calendar." in out["text"]


def test_section_order_school_email_day():
    src = {"label": "DailyBuilt", "from": "Pat <p@x.com>", "account": "dailybuilt", "id": "m1", "subject": "Quote"}
    out = b.compose({"overdue": ["ECON 2201, Quiz 5"], "today": [], "later": []}, [pick("Pat", "wants the quote", src)],
                    ["2 PM, call with Chris"], ["Lecture overlaps Call at 2 PM"])
    t = out["text"]
    assert t.index("School.") < t.index("Email.") < t.index("Your day.")
    assert "Pat, DailyBuilt: wants the quote." in t
    assert "Heads up: Lecture overlaps Call at 2 PM." in t


def test_word_cap_trims_lookahead_first_and_keeps_due_today():
    later = [f"Wednesday: COURSE {i}, a very long assignment title number {i} at 11:59 PM" for i in range(12)]
    src = {"label": "personal", "from": "A <a@x.com>", "account": "personal", "id": "m", "subject": "s"}
    emails = [pick(f"Person{i}", "needs a signed form back before the board meeting on Thursday", src) for i in range(5)]
    out = b.compose({"overdue": [], "today": ["FNCE 3101, Problem Set 4 at 11:59 PM"], "later": later}, emails, ["10 AM, lecture"], [], max_words=120)
    assert out["words"] <= 120
    assert "FNCE 3101, Problem Set 4" in out["text"]                    # due today survives
    assert out["trimmed"].get("later", 0) > 0                           # lookahead went first
    assert "more days with work due" in out["text"] or not out["sections"]["later"]


def test_default_cap_is_250_words():
    later = [f"Thursday: ACCT {i}, chapter {i} homework with a long name at 11:59 PM" for i in range(40)]
    out = b.compose({"later": later}, [], [], [], max_words=250)
    assert out["words"] <= 250


# ---- school grouping -------------------------------------------------------------------------------------------

def test_school_sections_group_by_day():
    items = [
        {"course": "ECON 2201", "title": "Quiz 5", "due": (NOW - timedelta(hours=2)).isoformat()},
        {"course": "FNCE 3101", "title": "PS 4", "due": NOW.replace(hour=23, minute=59).isoformat()},
        {"course": "ACCT 2001", "title": "HW 7", "due": (NOW + timedelta(days=1, hours=14, minutes=59)).isoformat()},
        {"course": "ACCT 2001", "title": "HW 8", "due": (NOW + timedelta(days=1, hours=14, minutes=30)).isoformat()},
        {"course": "MGMT 3101", "title": "Far away", "due": (NOW + timedelta(days=6)).isoformat()},
    ]
    s = b.school_sections(items, NOW)
    assert s["overdue"] == ["ECON 2201, Quiz 5"]
    assert s["today"] == ["FNCE 3101, PS 4 at 11:59 PM"]
    assert s["later"] == ["Tuesday: ACCT 2001, HW 8 at 11:30 PM; ACCT 2001, HW 7 at 11:59 PM"]


# ---- email filtering -------------------------------------------------------------------------------------------

def test_email_filter_drops_automated_promos_and_replied():
    inbox = [
        email(1, "Pacsun <hello@mkt.pacsun.com>", "Last call"),
        email(2, "GitHub <notifications@github.com>", "PR merged"),
        email(3, "Shop <deals@shop.com>", "Sale", labels=["INBOX", "CATEGORY_PROMOTIONS"]),
        email(4, "Pat <pat@client.com>", "Quote?", date="Sun, 25 Oct 2026 10:00:00 -0400", thread="t4"),
        email(5, "Prof Lee <lee@school.edu>", "Office hours", thread="t5"),
        email(6, "Ben <you@gmail.com>", "note to self"),
    ]
    sent = [{"threadId": "t4", "date": "Sun, 25 Oct 2026 12:00:00 -0400"}]   # Ben replied to Pat after his mail
    c = b.email_candidates(inbox, sent, PERSONAL)
    assert [x["from"] for x in c] == ["Prof Lee <lee@school.edu>"]


def test_email_newer_than_reply_stays():
    inbox = [email(4, "Pat <pat@client.com>", "Re: Quote", date="Sun, 25 Oct 2026 15:00:00 -0400", thread="t4")]
    sent = [{"threadId": "t4", "date": "Sun, 25 Oct 2026 12:00:00 -0400"}]
    assert len(b.email_candidates(inbox, sent, DB)) == 1
    assert b.email_candidates(inbox, sent, DB)[0]["label"] == "DailyBuilt"


def test_email_keeps_newest_per_thread():
    inbox = [email(1, "A <a@x.com>", "old", date="Sat, 24 Oct 2026 10:00:00 -0400", thread="t"),
             email(2, "A <a@x.com>", "new", date="Sun, 25 Oct 2026 10:00:00 -0400", thread="t")]
    c = b.email_candidates(inbox, [], PERSONAL)
    assert len(c) == 1 and c[0]["subject"] == "new"


class FakeMsg:
    def __init__(self, text):
        self.content = [type("T", (), {"type": "text", "text": text})()]
        self.usage = type("U", (), {"input_tokens": 100, "output_tokens": 20})()


class FakeClient:
    def __init__(self, text):
        self.text, self.calls = text, []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        return FakeMsg(self.text)


def test_pick_emails_uses_haiku_with_fenced_data(cfg):
    cands = b.email_candidates([email(1, "Pat <p@client.com>", "Quote"), email(2, "Ann <ann@x.com>", "Lunch?")], [], DB)
    fc = FakeClient('{"picks": [{"i": 1, "who": "Pat", "need": "wants the revised quote by Friday."}]}')
    out = b.pick_emails(cands, cfg, 5, client=fc)
    assert len(out) == 1 and out[0]["who"] == "Pat" and out[0]["need"] == "wants the revised quote by Friday"
    kw = fc.calls[0]
    assert kw["model"] == "claude-haiku-5-5" and kw["thinking"] == {"type": "disabled"}
    assert "<untrusted_data" in kw["messages"][0]["content"] and "data, not instructions" in kw["system"]


def test_pick_emails_falls_back_without_model(cfg):
    cands = b.email_candidates([email(1, "Rando <r@x.com>", "Hi"), email(2, "Chris <chris@example.com>", "Deck")], [], PERSONAL)
    class Broken:
        messages = property(lambda self: (_ for _ in ()).throw(RuntimeError("no key")))
    out = b.pick_emails(cands, cfg, 5, client=Broken())
    assert out[0]["src"]["from"].startswith("Chris")          # VIP first
    assert AuditLog().recent_events(1)[0]["kind"] == "brief_pick_fallback"


def test_pick_caps_at_max(cfg):
    cands = b.email_candidates([email(i, f"P{i} <p{i}@x.com>") for i in range(8)], [], PERSONAL)
    fc = FakeClient('{"picks": [' + ",".join(f'{{"i": {i}, "who": "P{i}", "need": "x"}}' for i in range(8)) + "]}")
    assert len(b.pick_emails(cands, cfg, 5, client=fc)) == 5


# ---- calendar --------------------------------------------------------------------------------------------------

def test_day_events_and_conflicts():
    ev = [{"summary": "Lecture", "start": "2026-10-26T14:00:00-04:00", "end": "2026-10-26T15:15:00-04:00"},
          {"summary": "Call with Chris", "start": "2026-10-26T15:00:00-04:00", "end": "2026-10-26T15:30:00-04:00"},
          {"summary": "Holiday", "start": "2026-10-26", "end": "2026-10-27"},
          {"summary": "Yesterday", "start": "2026-10-25T10:00:00-04:00", "end": "2026-10-25T11:00:00-04:00"}]
    lines, conflicts = b.day_events(ev, NOW)
    assert lines == ["all day, Holiday", "2 PM, Lecture", "3 PM, Call with Chris"]
    assert conflicts == ["Lecture overlaps Call with Chris at 3 PM"]


# ---- schedule ----------------------------------------------------------------------------------------------------

def test_cron_expr(cfg):
    assert b.cron_expr(cfg) == "0 9 * * 1,2,3,4,5"
    assert b.cron_expr({"brief_call": {"time": "08:30", "days": ["sat", "sun"]}}) == "30 8 * * 0,6"


@pytest.mark.parametrize("when,state,expect", [
    (NOW, {}, "call"),
    (NOW.replace(hour=10, minute=0), {}, "call"),                  # within max_late_minutes
    (NOW.replace(hour=11, minute=0), {}, "telegram"),              # Mac woke up late
    (NOW, {"last_date": "2026-10-26"}, "skip"),                    # never twice a day
    (NOW + timedelta(days=5), {}, "skip"),                         # Saturday
])
def test_should_run(cfg, when, state, expect):
    assert b.should_run(cfg, when, state)[0] == expect


# ---- the call and its fallback -----------------------------------------------------------------------------------

@pytest.fixture
def fake_build(monkeypatch, tmp_path):
    monkeypatch.setattr("jarvis.routines.STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(b, "build", lambda cfg=None, now=None, **k: {"text": "Morning, Ben. Here's your brief. X. " + b.CLOSER,
                                                                   "telegram": "X", "detail": "d", "words": 9, "accounts": ["personal"],
                                                                   "notes": [], "email_candidates": 0, "trimmed": {}})


ENV = {"BEN_PHONE_NUMBER": "+18605550100"}


def test_answered_call_sends_no_telegram(cfg, fake_build):
    sent, dialed = [], []
    def dial(req, **k):
        dialed.append(req); return {"status": "placed", "response": {"conversation_id": "c1"}}
    r = b.run("scheduled", NOW, env=ENV, cfg=cfg, dial=dial, wait=lambda p, e: {"answered": True, "why": "2 turns"},
              send=lambda t, s: sent.append(t) or True)
    assert r["status"] == "answered" and not sent
    req = dialed[0]
    assert req.first_message.startswith("Morning, Ben. Here's your brief.") and req.variables["call_type"] == "brief"


@pytest.mark.parametrize("why", ["no-answer", "busy", "voicemail", "Ben never spoke"])
def test_missed_call_falls_back_to_telegram_once(cfg, fake_build, why):
    sent, dials = [], []
    def dial(req, **k):
        dials.append(1); return {"status": "placed", "response": {"conversation_id": "c1"}}
    kw = dict(env=ENV, cfg=cfg, dial=dial, wait=lambda p, e: {"answered": False, "why": why}, send=lambda t, s: sent.append(s) or True)
    r = b.run("scheduled", NOW, **kw)
    assert r["status"] == "telegram" and len(sent) == 1 and why in sent[0]
    assert b.run("scheduled", NOW + timedelta(minutes=5), **kw)["status"] == "skipped"   # no second call that day
    assert len(dials) == 1


def test_failed_call_sends_telegram_and_logs(cfg, fake_build):
    sent = []
    r = b.run("scheduled", NOW, env=ENV, cfg=cfg, dial=lambda req, **k: {"status": "error", "why": "ElevenLabs HTTP 402"},
              send=lambda t, s: sent.append(s) or True)
    assert r["status"] == "telegram" and sent
    assert AuditLog().recent_events(1)[0]["kind"] == "brief_call_failed"


def test_now_mode_does_not_use_up_the_day(cfg, fake_build):
    b.run("now", NOW, env=ENV, cfg=cfg, dial=lambda req, **k: {"status": "placed", "response": {"conversation_id": "c"}},
          wait=lambda p, e: {"answered": True}, send=lambda t, s: True)
    from jarvis.routines import load_state
    assert "last_date" not in load_state("brief_call")
    assert b.should_run(cfg, NOW, load_state("brief_call"))[0] == "call"


# ---- outcome classification (calls.py) -------------------------------------------------------------------------

def conv(status="done", user=(), duration=40):
    return {"status": status, "metadata": {"call_duration_secs": duration},
            "transcript": [{"role": "agent", "message": "Morning, Ben."}] + [{"role": "user", "message": u} for u in user]}


def test_classify_answered():
    assert classify_outcome(conv(user=["Draft a reply to Pat"]))["answered"]


@pytest.mark.parametrize("c,tw,why", [
    (conv(user=[]), "completed", "Ben never spoke"),
    (conv(user=["..."]), "completed", "Ben never spoke"),
    (conv(user=["Hi, you've reached Ben. Please leave a message after the tone."]), "completed", "voicemail"),
    (conv(user=["Hi, if you record your name and reason for calling, I'll see if this person is available."]), "completed", "voicemail"),
    (conv(status="initiated"), "no-answer", "no-answer"),
    (conv(status="initiated"), "busy", "busy"),
    ({}, "", "no conversation record"),
])
def test_classify_not_answered(c, tw, why):
    out = classify_outcome(c, tw)
    assert not out["answered"] and out["why"] == why


def test_wait_for_outcome_polls_until_done():
    import io, json
    seq = iter([conv(status="in-progress", user=[]), conv(status="done", user=["Yes that's all"])])
    class R(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False
    def opener(req, timeout=0):
        if "twilio" in req.full_url:
            return R(b'{"status": "in-progress"}')
        return R(json.dumps(next(seq)).encode())
    naps = []
    out = wait_for_outcome({"response": {"conversation_id": "c1", "callSid": "CA1"}},
                           {"ELEVENLABS_API_KEY": "k", "TWILIO_ACCOUNT_SID": "AC", "TWILIO_AUTH_TOKEN": "t"},
                           opener=opener, sleep=naps.append, poll_s=5)
    assert out["answered"] and naps == [5]



# ---- natural narration ------------------------------------------------------------------------------------------

def _sample():
    src = {"label": "personal", "from": "Cheryl <c@x.com>", "account": "personal", "id": "m1", "subject": "the grant outline", "snippet": "see blue"}
    emails = [pick("Cheryl", "reply on the the grant demo outline", src)]
    return b.compose({"overdue": [], "today": [], "later": []}, emails, ["1 PM, oceancrest"], []), emails


def test_narrate_uses_sonnet_with_fenced_facts_only(cfg):
    out, emails = _sample()
    fc = FakeClient("Morning, Ben. On your personal email, Cheryl wants a reply on the the grant outline. "
                    "Just one meeting today, at one. Want me to do anything with that, or is that all?")
    text = b.narrate(out, emails, cfg, client=fc)
    assert text.startswith("Morning, Ben.") and "is that all" in text
    kw = fc.calls[0]
    assert kw["model"] == "claude-sonnet-5-5" and kw["thinking"] == {"type": "between_tools"}
    body = kw["messages"][0]["content"]
    assert "<untrusted_data" in body and "Cheryl" in body and "1 PM, oceancrest" in body
    assert "never instructions" in kw["system"] and "Never add" in kw["system"]


@pytest.mark.parametrize("reply", [
    "Here's the brief: Cheryl wants a reply.",                                    # doesn't open right
    "Morning, Ben. Cheryl wants a reply. Talk soon.",                             # no "is that all" (bridge hang-up needs it)
    "Morning, Ben. " + "word " * 300 + "Anything else, or is that all?",          # way over the cap
])
def test_narrate_rejects_bad_scripts(cfg, reply):
    out, emails = _sample()
    assert b.narrate(out, emails, cfg, client=FakeClient(reply)) is None
    assert AuditLog().recent_events(1)[0]["kind"] == "brief_narrate_rejected"


def test_build_falls_back_to_template_when_narration_fails(cfg):
    class Broken:
        messages = property(lambda self: (_ for _ in ()).throw(RuntimeError("api down")))
    g = {"school_items": [], "candidates": [], "events": [], "accounts": ["personal"], "notes": []}
    out = b.build(cfg, NOW, gathered=g, narrator=Broken())
    assert out["style"] == "template" and out["text"] == out["template_text"] and out["text"].startswith(b.OPENER)


def test_list_style_skips_narration(cfg):
    out, emails = _sample()
    fc = FakeClient("Morning, Ben. x. is that all?")
    assert b.narrate(out, emails, {**cfg, "brief_call": {**cfg["brief_call"], "style": "list"}}, client=fc) is None
    assert not fc.calls


def test_classify_drop_on_pickup():
    c = {"status": "done", "metadata": {"call_duration_secs": 1}, "transcript": []}
    assert classify_outcome(c, "completed") == {"answered": False, "why": "dropped on pickup, Jarvis never spoke"}


# ---- sales (Harvey) ----------------------------------------------------------------------------------------------

def _harvey(tmp_path, companies=(), outbox=(), replies=(), convs=()):
    import sqlite3
    db = tmp_path / "harvey.db"
    c = sqlite3.connect(db)
    c.executescript("""
      CREATE TABLE companies (id TEXT PRIMARY KEY, name TEXT DEFAULT '', created_at TIMESTAMP);
      CREATE TABLE prospects (id TEXT PRIMARY KEY, company_id TEXT DEFAULT '', company TEXT DEFAULT '');
      CREATE TABLE outbox (id TEXT PRIMARY KEY, status TEXT);
      CREATE TABLE processed_replies (reply_id TEXT PRIMARY KEY, processed_at TIMESTAMP);
      CREATE TABLE conversations (id TEXT PRIMARY KEY, prospect_id TEXT, thread_json TEXT DEFAULT '[]', updated_at TIMESTAMP);""")
    c.executemany("INSERT INTO companies VALUES (?,?,?)", companies)
    c.executemany("INSERT INTO outbox VALUES (?,?)", outbox)
    c.executemany("INSERT INTO processed_replies VALUES (?,?)", replies)
    for i, (name, ts) in enumerate(convs):
        c.execute("INSERT INTO companies VALUES (?,?,?)", (f"cc{i}", name, "2026-01-01 00:00:00"))
        c.execute("INSERT INTO prospects VALUES (?,?,?)", (f"p{i}", f"cc{i}", ""))
        c.execute("INSERT INTO conversations VALUES (?,?,?,?)", (f"v{i}", f"p{i}", '[{"direction": "inbound"}]', ts))
    c.commit(); c.close()
    return str(db)


def test_harvey_sales_counts_last_24h_utc(tmp_path):
    # NOW = 9:00 ET = 13:00 UTC; window starts 13:00 UTC the day before.
    db = _harvey(tmp_path,
                 companies=[("a", "A", "2026-10-25T14:00:00"), ("b", "B", "2026-10-26 12:00:00"), ("old", "O", "2026-10-25 12:59:00")],
                 outbox=[("o1", "pending_review"), ("o2", "pending_review"), ("o3", "sent")],
                 replies=[("r1", "2026-10-26 08:00:00"), ("r0", "2026-10-24 08:00:00")],
                 convs=[("Harbor Dental", "2026-10-26 08:00:00")])
    s = b.harvey_sales(db, NOW)
    assert s == {"found": 2, "drafts": 2, "replies": 1, "repliers": ["Harbor Dental"]}
    assert b.sales_line(s) == "Harvey found 2 new businesses, 2 drafts are waiting for you, and Harbor Dental replied."


@pytest.mark.parametrize("sales,line", [
    ({"found": 6, "drafts": 3, "replies": 1, "repliers": ["Harbor Dental"]},
     "Harvey found 6 new businesses, 3 drafts are waiting for you, and Harbor Dental replied."),
    ({"found": 1, "drafts": 0, "replies": 0, "repliers": []}, "Harvey found 1 new business."),
    ({"found": 0, "drafts": 1, "replies": 2, "repliers": []}, "1 draft is waiting for you, and 2 new replies came in."),
    ({"found": 0, "drafts": 0, "replies": 3, "repliers": ["Harbor Dental"]}, "Harbor Dental replied, plus 2 other replies."),
    ({"found": 0, "drafts": 0, "replies": 0, "repliers": []}, ""),
    (None, ""),
])
def test_sales_line(sales, line):
    assert b.sales_line(sales) == line


def test_sales_missing_or_locked_db_is_skipped_and_logged(tmp_path):
    assert b.harvey_sales(str(tmp_path / "nope.db"), NOW) is None
    assert AuditLog().recent_events(1)[0]["kind"] == "brief_sales_skipped"
    bad = tmp_path / "bad.db"; bad.write_text("not a database")
    assert b.harvey_sales(str(bad), NOW) is None
    assert "DatabaseError" in AuditLog().recent_events(1)[0]["detail"]


def test_sales_db_is_opened_read_only(tmp_path):
    import sqlite3
    db = _harvey(tmp_path, outbox=[("o1", "pending_review")])
    b.harvey_sales(db, NOW)
    c = sqlite3.connect(db)
    assert c.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1      # untouched
    assert not (tmp_path / "harvey.db-journal").exists()


def test_sales_section_order_telegram_and_skip():
    src = {"label": "personal", "from": "A <a@x.com>", "account": "personal", "id": "m", "subject": "s"}
    line = "Harvey found 6 new businesses, 3 drafts are waiting for you, and Harbor Dental replied."
    out = b.compose({}, [pick("Ann", "a reply", src)], ["1 PM, oceancrest"], [], sales=line)
    t = out["text"]
    assert t.index("Email.") < t.index("Sales. " + line) < t.index("Your day.")
    assert "💼 Sales\n• " + line in out["telegram"]
    assert "Sales." not in b.compose({}, [], ["1 PM, x"], [], sales="")["text"]


def test_sales_keeps_word_cap():
    line = "Harvey found 6 new businesses, 3 drafts are waiting for you, and Harbor Dental replied."
    later = [f"Thursday: ACCT {i}, chapter {i} homework with a long name at 11:59 PM" for i in range(40)]
    out = b.compose({"later": later}, [], [], [], max_words=120, sales=line)
    assert out["words"] <= 120 and line in out["text"]


def test_narrate_rejects_reworded_sales(cfg):
    src = {"label": "personal", "from": "A <a@x.com>", "account": "personal", "id": "m", "subject": "s", "snippet": ""}
    emails = [pick("Ann", "a reply", src)]
    out = b.compose({}, emails, [], [], sales="Harvey found 6 new businesses.")
    fc = FakeClient("Morning, Ben. Ann wants a reply, and Harvey dug up half a dozen leads. Anything else, or is that all?")
    assert b.narrate(out, emails, cfg, client=fc) is None
    assert "sales_say_exactly" in fc.calls[0]["messages"][0]["content"]
