"""Exercise the plugin hooks end to end with a fake Hermes ctx."""

from jarvis import guard
from jarvis.audit import AuditLog


class FakeCtx:
    def __init__(self):
        self.hooks = {}

    def register_hook(self, name, fn):
        self.hooks[name] = fn


def fresh():
    guard._audit = None
    guard._spend = None
    guard._warned_level = "ok"
    ctx = FakeCtx()
    guard.register(ctx)
    return ctx


def test_tier1_allows_and_logs():
    ctx = fresh()
    assert ctx.hooks["pre_tool_call"](tool_name="web_search", args={"q": "x"}, task_id="t", session_id="s") is None
    rows = AuditLog().recent(1)
    assert rows[0]["decision"] == "allow" and rows[0]["tier"] == 1


def test_tier2_escalates_to_approval():
    # In a conversation Tier 2 just runs (tier2_mode: do); an unattended cron run still gets the approval prompt.
    ctx = fresh()
    assert ctx.hooks["pre_tool_call"](tool_name="gmail_send_message", args={"to": "a@b.c"}, task_id="t", session_id="s") is None
    d = ctx.hooks["pre_tool_call"](tool_name="gmail_send_message", args={"to": "a@b.c"}, task_id="cron:job:run", session_id="cron_job_1")
    assert d["action"] == "approve" and d["rule_key"].startswith("jarvis:")
    ctx.hooks["post_approval_response"](command="gmail_send_message", choice="once", session_key="cron_job_1", surface="gateway")
    row = AuditLog().recent(1)[0]
    assert row["decision"] == "ask" and row["approval"] == "once" and row["approver"] == "ben:gateway"


def test_tier3_blocks_with_message():
    ctx = fresh()
    d = ctx.hooks["pre_tool_call"](tool_name="bank_transfer", args={}, task_id="t")
    assert d["action"] == "block" and "Ben" in d["message"]


def test_post_tool_call_records_outcome():
    ctx = fresh()
    ctx.hooks["pre_tool_call"](tool_name="read_file", args={"path": "x"}, task_id="t", session_id="s", tool_call_id="c")
    ctx.hooks["post_tool_call"](tool_name="read_file", args={}, result='{"error": "nope"}', task_id="t", session_id="s", tool_call_id="c", duration_ms=7)
    row = AuditLog().recent(1)[0]
    assert row["ok"] == 0 and row["duration_ms"] == 7


def test_hooks_never_raise(monkeypatch):
    ctx = fresh()
    monkeypatch.setattr(guard, "classify", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert ctx.hooks["pre_tool_call"](tool_name="x", args={}, task_id="t") is None


def test_spend_cap_blocks_tools():
    ctx = fresh()
    cap = guard.cached_config()["spend"]["daily_cap_usd"]
    tokens = int(cap / 2.0 * 1_000_000) + 1_000  # Sonnet input at $2/M -> just over cap
    ctx.hooks["post_api_request"](model="claude-sonnet-5-5", usage={"prompt_tokens": tokens, "completion_tokens": 0})
    d = ctx.hooks["pre_tool_call"](tool_name="web_search", args={}, task_id="t")
    assert d["action"] == "block" and "cap" in d["message"].lower()
    assert "cap" in ctx.hooks["pre_llm_call"]()["context"].lower()


def test_tier2_runs_when_ben_asked_but_not_from_cron(monkeypatch, tmp_path):
    import pathlib
    import jarvis.guard as g
    from jarvis.config import load_config
    from jarvis.audit import AuditLog
    cfg = load_config(pathlib.Path(__file__).resolve().parents[1] / "config" / "jarvis.yaml")
    assert cfg["permissions"]["tier2_mode"] == "do"
    monkeypatch.setattr(g, "cached_config", lambda: cfg)
    monkeypatch.setattr(g, "_get_audit", lambda: AuditLog(tmp_path / "a.db"))
    class S:
        def status(self): return {"level": "ok"}
    monkeypatch.setattr(g, "_get_spend", lambda: S())
    send = {"command": "$GAPI gmail send --to a@b.c --subject hi --body x"}
    assert g.pre_tool_call("terminal", send, session_id="telegram-123", task_id="t1") is None          # Ben asked: just do it
    cron = g.pre_tool_call("terminal", send, session_id="cron_bb10e92cf016_20261015_1015", task_id="cron:bb10e92cf016:abc")
    assert cron and cron["action"] == "approve"                                                           # unattended: still gated


# ---- hidden Jarvis Browser: signed-in sites only ---------------------------------------------------------------

def test_hidden_browser_blocks_ordinary_sites():
    ctx = fresh()
    for args in ({"url": "https://www.tradingview.com/chart"}, {"url": "youtube.com"},
                 {"code": "await page.goto('https://x.com/home')"}):
        d = ctx.hooks["pre_tool_call"](tool_name="browser_navigate", args=args, task_id="t", session_id="s")
        assert d["action"] == "block" and "jarvis mac open" in d["message"], args
    row = AuditLog().recent(1)[0]
    assert row["decision"] == "block" and row["rule"] == "hidden-browser"


def test_hidden_browser_allows_signed_in_sites_and_plain_reads():
    ctx = fresh()
    for args in ({"url": "https://lms.uconn.edu/ultra/courses"}, {"code": "print(page_info())"}, {}):
        assert ctx.hooks["pre_tool_call"](tool_name="browser_exec", args=args, task_id="t", session_id="s") is None
    # other tools are untouched, even with a URL
    assert ctx.hooks["pre_tool_call"](tool_name="web_extract", args={"urls": ["https://x.com"]}, task_id="t", session_id="s") is None


def test_hidden_browser_violation_pure():
    v = guard.hidden_browser_violation
    assert v("browser_exec", {"code": "goto('https://evil.uconn.edu.example.com')"}, ["uconn.edu"]) == "evil.uconn.edu.example.com"
    assert v("browser_exec", {"code": "goto('http://127.0.0.1:8650/')"}, ["uconn.edu"]) is None
    assert v("terminal", {"command": "open https://x.com"}, ["uconn.edu"]) is None


def test_browser_results_say_ben_cant_see_them():
    ctx = fresh()
    out = ctx.hooks["transform_tool_result"](tool_name="browser_exec", args={}, result="tabs: [lms.uconn.edu]")
    assert "Ben can't see it" in out and "jarvis mac chrome tabs" in out


def test_hidden_browser_host_parsing_tricks():
    v = guard.hidden_browser_violation
    a = ["uconn.edu"]
    assert v("browser_navigate", {"url": "https://lms.uconn.edu:x@evil.com/"}, a) == "evil.com"
    assert v("browser_navigate", {"url": "https://evil.com@lms.uconn.edu/"}, a) is None      # real host is uconn
    assert v("browser_exec", {"code": "location.href = '//evil.com/x'"}, a) == "evil.com"
    assert v("browser_navigate", {"url": "file:///etc/passwd"}, a) is not None
    assert v("browser_exec", {"code": "location='javascript:alert(1)'"}, a) is not None
    assert v("browser_navigate", {"url": "https://lms.uconn.edu./ultra"}, a) is None
    assert v("browser_navigate", {"url": "https://x.com"}, ["", "."]) == "x.com"             # empty allow entries ignored
    assert v("browser_exec", {"code": "x = {data: 1}; print(page_info())"}, a) is None
    assert v("browser_navigate", {"url": "data:text/html,<b>hi</b>"}, a) is not None


def test_hidden_browser_review_round_two():
    v, a = guard.hidden_browser_violation, ["uconn.edu"]
    assert v("browser_navigate", {"url": "java\tscript:alert(1)"}, a) is not None             # tab-split scheme
    assert v("browser_navigate", {"url": "javascript:alert(1)"}, a) is not None
    assert v("browser_navigate", {"url": "data:,hi"}, a) is not None
    assert v("browser_navigate", {"url": "ftp://lms.uconn.edu/x"}, a) is not None             # http(s) only
    assert v("browser_navigate", {"url": "https://lms.uconn.edu/ultra"}, a) is None
    assert v("browser_navigate", {"url": "lms.uconn.edu/ultra"}, a) is None
    assert v("browser_navigate", {"url": "about:blank"}, a) is None
    assert v("browser_exec", {"code": "goto('https:\\\\\\\\evil.com')"}, a) == "evil.com"     # backslashes as slashes
    assert v("browser_exec", {"code": "location = 'data:,x'"}, a) is not None
    assert v("browser_exec", {"code": "x = {data: 1}"}, a) is None
