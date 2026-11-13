import json
from datetime import datetime, timedelta, timezone

from jarvis import wake_listener as wl


class Clock:
    def __init__(self): self.t = 100.0
    def __call__(self): return self.t


def test_detector_needs_consecutive_frames():
    d = wl.Detector(threshold=0.6, confirm=3, cooldown=4, clock=Clock())
    assert d.feed([0.9]) is False
    assert d.feed([0.9]) is False
    assert d.feed([0.95]) is True
    assert d.detections == 1


def test_detector_stray_spike_resets():
    d = wl.Detector(threshold=0.6, confirm=3, cooldown=4, clock=Clock())
    assert d.feed([0.9]) is False
    assert d.feed([0.1]) is False   # streak broken
    assert d.feed([0.9]) is False
    assert d.feed([0.9]) is False
    assert d.feed([0.9]) is True


def test_detector_cooldown():
    c = Clock(); d = wl.Detector(threshold=0.6, confirm=1, cooldown=4, clock=c)
    assert d.feed([0.8]) is True
    c.t += 1
    assert d.feed([0.8]) is False   # within cooldown
    c.t += 5
    assert d.feed([0.8]) is True


def test_voice_active_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(wl, "FLAG", tmp_path / "voice_active.json")
    monkeypatch.setattr(wl, "hud_window_open", lambda: True)
    assert wl.voice_active() is False
    from datetime import datetime, timedelta, timezone, timedelta
    (tmp_path / "voice_active.json").write_text(json.dumps({"active": True, "ts": datetime.now(timezone.utc).isoformat()}))
    assert wl.voice_active() is True
    (tmp_path / "voice_active.json").write_text(json.dumps({"active": True, "ts": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}))
    assert wl.voice_active() is False  # stale flag ignored


def test_rms_and_heartbeat(tmp_path, monkeypatch):
    import numpy as np
    assert wl.rms(np.zeros(10, dtype="int16")) == 0.0
    assert wl.rms(np.array([3, -4], dtype="int16")) > 3
    monkeypatch.setattr(wl, "STATE", tmp_path); monkeypatch.setattr(wl, "HEARTBEAT", tmp_path / "hb.json")
    wl.heartbeat(rms=1.5, paused=False)
    assert json.loads((tmp_path / "hb.json").read_text())["rms"] == 1.5


def test_voice_active_ignored_when_hud_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(wl, "FLAG", tmp_path / "voice_active.json")
    (tmp_path / "voice_active.json").write_text(json.dumps({"active": True, "source": "hud", "ts": datetime.now(timezone.utc).isoformat()}))
    monkeypatch.setattr(wl, "hud_window_open", lambda: False)
    assert wl.voice_active() is False
    monkeypatch.setattr(wl, "hud_window_open", lambda: True)
    assert wl.voice_active() is True
    old = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
    (tmp_path / "voice_active.json").write_text(json.dumps({"active": True, "source": "hud", "ts": old}))
    assert wl.voice_active() is False   # stale after 60 s even with the window open


def test_phrase_hit_partial_and_final():
    assert wl.phrase_hit(json.dumps({"partial": "jarvis wake up"}))
    assert wl.phrase_hit(json.dumps({"text": "[unk] jarvis  wake up"}))
    assert not wl.phrase_hit(json.dumps({"partial": "jarvis"}))
    assert not wl.phrase_hit(json.dumps({"text": "travis wake up"}))
    assert not wl.phrase_hit("") and not wl.phrase_hit("not json")


def test_phrase_grammar_has_phrase_and_decoys():
    assert wl.PHRASE in wl.PHRASE_GRAMMAR and "[unk]" in wl.PHRASE_GRAMMAR and len(wl.PHRASE_GRAMMAR) > 5


def _words(*pairs):
    return json.dumps({"result": [{"word": w, "conf": c} for w, c in pairs], "text": " ".join(w for w, _ in pairs)})


def test_final_hit_needs_consecutive_confident_words():
    assert wl.final_hit(_words(("jarvis", 1.0), ("wake", 1.0), ("up", 1.0)))
    assert wl.final_hit(_words(("[unk]", 0.4), ("hey", 1.0), ("jarvis", 0.99), ("wake", 1.0), ("up", 0.97)))
    assert not wl.final_hit(_words(("jarvis", 0.80), ("wake", 1.0), ("up", 1.0)))      # shaky "jarvis"
    assert not wl.final_hit(_words(("jarvis", 1.0), ("[unk]", 1.0), ("wake", 1.0), ("up", 1.0)))   # not in a row
    assert not wl.final_hit(_words(("[unk]", 1.0), ("wake", 1.0), ("up", 1.0)))         # "...wake up" alone
    assert not wl.final_hit(json.dumps({"partial": "jarvis wake up"}))                  # partials never count
    assert not wl.final_hit("") and not wl.final_hit("not json")


def test_grammar_has_sound_alike_decoys():
    for d in ("darvish", "jars", "awake", "travis", "harvey"):
        assert d in wl.PHRASE_GRAMMAR


def test_listener_loop_uses_finals_only():
    import inspect
    src = inspect.getsource(wl.main)
    assert "final_hit(" in src and "PartialResult" not in src and "SetWords(True)" in src


def test_hud_space_ignores_typing():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "dashboard" / "static" / "index.html").read_text()
    assert "Date.now() - lastOtherKey < 1500" in html and "e.repeat" in html
