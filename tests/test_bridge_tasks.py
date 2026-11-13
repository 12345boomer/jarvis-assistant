"""Voice bridge: background jobs from a call report to Ben's Telegram."""
import jarvis.voice_bridge as vb


def test_telegram_payload_requires_config(monkeypatch):
    monkeypatch.setitem(vb.ENV, "TELEGRAM_BOT_TOKEN", "")
    assert vb.telegram_payload("hi") is None


def test_telegram_payload_shape(monkeypatch):
    monkeypatch.setitem(vb.ENV, "TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setitem(vb.ENV, "TELEGRAM_HOME_CHANNEL", "123")
    monkeypatch.setitem(vb.ENV, "TELEGRAM_HOME_CHANNEL_THREAD_ID", "")
    p = vb.telegram_payload("Deck ready: /x.pptx", subject="From your call")
    assert p["chat_id"] == "123" and p["text"].startswith("From your call\n\nDeck ready")
    assert "message_thread_id" not in p
    monkeypatch.setitem(vb.ENV, "TELEGRAM_HOME_CHANNEL_THREAD_ID", "9")
    assert vb.telegram_payload("x")["message_thread_id"] == "9"


def test_start_task_tool_is_offered_and_needs_say_first():
    assert vb.START_TASK_TOOL["input_schema"]["required"] == ["task", "say_first"]
    assert "start_task" not in {t["name"] for t in vb.QUICK_TOOLS}
    # ElevenLabs's own copies of our tools are filtered out so they aren't registered twice
    assert vb.openai_tools_to_claude([{"type": "function", "function": {"name": "start_task"}},
                                      {"type": "function", "function": {"name": "end_call"}}])[0]["name"] == "end_call"


def test_hud_session_detection(tmp_path, monkeypatch):
    import json
    from datetime import datetime, timezone
    monkeypatch.setattr(vb, "STATE_DIR", tmp_path)
    assert vb.is_hud_session() is False                                   # no flag: it's the phone
    (tmp_path / "voice_active.json").write_text(json.dumps({"active": True, "source": "hud", "ts": datetime.now(timezone.utc).isoformat()}))
    assert vb.is_hud_session() is True
    (tmp_path / "voice_active.json").write_text(json.dumps({"active": True, "source": "phone", "ts": datetime.now(timezone.utc).isoformat()}))
    assert vb.is_hud_session() is False
    assert vb.GREETING == "Hey, sir. How can I help you?"


def test_new_call_and_real_text():
    sysm = {"role": "system", "content": "x"}
    assert vb.is_new_call([sysm]) is True
    assert vb.is_new_call([sysm, {"role": "assistant", "content": "Hey, sir."}, {"role": "user", "content": "weather?"}]) is True
    assert vb.is_new_call([sysm, {"role": "assistant", "content": "a"}, {"role": "user", "content": "b"}, {"role": "assistant", "content": "c"}, {"role": "user", "content": "d"}]) is False
    assert vb.real_text("...") is False and vb.real_text("") is False and vb.real_text("hi") is True


def test_call_closing_intent():
    assert vb.wants_to_end("yes", "Done. Is that all?") is True
    assert vb.wants_to_end("No, that's all.", "Is that all?") is True
    assert vb.wants_to_end("hang up", "Sixty degrees.") is True
    assert vb.wants_to_end("Bye Jarvis", "anything") is True
    assert vb.wants_to_end("yes", "Sixty degrees.") is False          # a bare yes only closes after "Is that all?"
    assert vb.wants_to_end("No, that's all", "Done. Anything else?") is True
    assert vb.wants_to_end("nope", "Sent. Anything else?") is True
    assert vb.wants_to_end("yes", "Sent. Anything else?") is False     # "yes" after "Anything else?" means he has more
    assert vb.wants_to_end("yeah thats all", "Anything else?") is False
    assert vb.wants_to_end("yes, and also email Chris", "Is that all?") is False
    assert vb.wants_to_end("", "Is that all?") is False


def test_wants_transfer_phrases():
    from jarvis.voice_bridge import wants_transfer
    yes = ["transfer to macbook", "Transfer the mic to my MacBook", "let's talk through the computer", "switch over to my laptop",
           "can we continue on the mac", "put the mic on my laptop", "use the computer's microphone", "move this to my Mac Air"]
    no = ["open my laptop", "turn on my screen", "what's on my computer", "is that all", "make the deck and send it",
          "open a transfer form", "text Chris that I'm late"]
    assert all(wants_transfer(t) for t in yes), [t for t in yes if not wants_transfer(t)]
    assert not any(wants_transfer(t) for t in no), [t for t in no if wants_transfer(t)]


def test_mirror_hud_only_on_phone_turn_and_once(monkeypatch):
    from jarvis import voice_bridge as vb
    posted = []
    monkeypatch.setattr(vb, "_bg", lambda coro: (posted.append("start"), coro.close()))
    vb._call.update({"phone_turn": 0.0, "mirrored": 0.0})
    vb._mirror_hud(); assert posted == []
    vb._call.update({"phone_turn": 1.0, "mirrored": 0.0})
    vb._mirror_hud(); vb._mirror_hud(); assert posted == ["start"]


def test_inbound_initiation_sets_every_variable_the_agent_needs():
    import re
    from pathlib import Path
    d = vb.initiation_data()
    assert d["type"] == "conversation_initiation_client_data" and d["dynamic_variables"]["opening"] == vb.GREETING
    prompt = (Path(__file__).resolve().parents[1] / "config" / "voice-agent-prompt.md").read_text()
    assert set(re.findall(r"\{\{(\w+)\}\}", prompt)) <= set(d["dynamic_variables"])
