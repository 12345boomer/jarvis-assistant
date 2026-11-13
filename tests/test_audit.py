from jarvis.audit import AuditLog, redact, summarize_args


def test_record_and_finish_roundtrip():
    log = AuditLog()
    log.record_call(tool="gmail_send_message", args={"to": "a@b.c"}, tier=2, decision="ask",
                    rule="tier2:*send*", reason="r", session_id="s1", tool_call_id="c1", platform="telegram")
    log.finish_call(session_id="s1", tool_call_id="c1", tool="gmail_send_message", duration_ms=42, ok=True)
    log.record_approval(session_id="s1", choice="once", approver="ben:gateway")
    rows = log.recent(10)
    assert len(rows) == 1
    r = rows[0]
    assert r["tool"] == "gmail_send_message" and r["tier"] == 2 and r["decision"] == "ask"
    assert r["duration_ms"] == 42 and r["ok"] == 1
    assert r["approval"] == "once" and r["approver"] == "ben:gateway"
    assert r["platform"] == "telegram"


def test_redaction_of_keys_and_values():
    out = redact({"api_key": "abc", "nested": {"Authorization": "x", "ok": "fine"},
                  "text": "token is sk-ant-abcdefghijklmnop and bot 123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"})
    assert out["api_key"] == "[redacted]"
    assert out["nested"]["Authorization"] == "[redacted]"
    assert out["nested"]["ok"] == "fine"
    assert "sk-ant-" not in out["text"] and "123456789:" not in out["text"]


def test_summary_is_truncated():
    s = summarize_args({"body": "x" * 5000})
    assert len(s) <= 500


def test_events():
    log = AuditLog()
    log.event("spend_alert", "1.60 of 2.00")
    assert log.recent_events(1)[0]["kind"] == "spend_alert"
