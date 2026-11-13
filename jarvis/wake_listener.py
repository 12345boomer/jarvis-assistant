"""Always-on wake listener (standalone; runs from ~/.hermes/jarvis-wake under launchd).

Two phrases, both fully on-device, either one opens/raises the HUD with a voice session:
  "Hey Jarvis"       openWakeWord's hey_jarvis model (pyopen-wakeword, TFLite), 16 kHz frames of 80 ms
  "Jarvis, wake up"  Vosk small English model in grammar mode: it may only answer with the phrase, a few
                     sound-alikes ("travis", "what's up"...) or [unk]. Fires only on a FINAL result with the three
                     words in a row at high confidence (2026-10-30: partial results fired on unrelated talk).
Pauses while a HUD voice session is active (flag file written by the dashboard) so Jarvis's
own voice from the speakers can't re-trigger it. Writes a heartbeat the doctor reads.

Env: WAKE_PHRASE_MODEL (Vosk model dir; "Jarvis, wake up" is off if missing), WAKE_MODEL (tflite path), WAKE_THRESHOLD (0..1, default 0.75), WAKE_CONFIRM (frames, default 4),
     WAKE_COOLDOWN (s, default 4), WAKE_HUD (path to jarvis-hud.sh), HERMES_HOME.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable

HERMES_HOME = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
STATE = HERMES_HOME / "jarvis-state"
FLAG = STATE / "voice_active.json"
HEARTBEAT = STATE / "wake_heartbeat.json"
HUD = os.environ.get("WAKE_HUD") or str(HERMES_HOME / "jarvis-hud.sh")
RATE, FRAME = 16000, 1280  # 80 ms


class Detector:
    """Pure decision logic (testable): consecutive over-threshold frames + cooldown."""

    def __init__(self, threshold: float = 0.6, confirm: int = 3, cooldown: float = 4.0, clock: Callable[[], float] = time.monotonic):
        self.threshold, self.confirm, self.cooldown, self.clock = threshold, confirm, cooldown, clock
        self.streak, self.last_fire, self.detections = 0, -1e9, 0
        self.last_fire_wall = 0.0   # wall-clock time of the last fire from either phrase (shared cooldown)

    def feed(self, scores: Iterable[float]) -> bool:
        over = any(s >= self.threshold for s in scores)
        if not over:
            self.streak = 0
            return False
        self.streak += 1
        if self.streak < self.confirm:
            return False
        self.streak = 0
        now = self.clock()
        if now - self.last_fire < self.cooldown:
            return False
        self.last_fire = now
        self.detections += 1
        return True


# Grammar for the phrase recognizer. Everything besides the phrase is a decoy: Vosk must pick the closest entry,
# so near-misses ("Travis, wake up", "Jarvis, what's up", "you need to wake up") land on a decoy instead.
PHRASE = "jarvis wake up"
PHRASE_GRAMMAR = [PHRASE, "hey jarvis", "jarvis", "wake up", "what is up", "what's up", "travis", "harvey",
                  "service", "marcus", "garlic", "darkness", "darvish", "jars", "awake", "jarvis awake", "[unk]"]


PHRASE_MIN_CONF = float(os.environ.get("WAKE_PHRASE_CONF", "0.95"))


def final_hit(result_json: str, min_conf: float = PHRASE_MIN_CONF) -> bool:
    """True when a Vosk FINAL result (SetWords on) holds 'jarvis wake up' as consecutive words, each >= min_conf.
    Partial results are never used: grammar mode squeezes any speech into the phrase list, and mid-utterance
    partials briefly read as the phrase during ordinary talk."""
    try:
        words = [(w.get("word"), float(w.get("conf", 0))) for w in json.loads(result_json or "{}").get("result") or []]
    except (ValueError, AttributeError, TypeError):
        return False
    want = PHRASE.split()
    for i in range(len(words) - len(want) + 1):
        if all(words[i + k][0] == want[k] and words[i + k][1] >= min_conf for k in range(len(want))):
            return True
    return False


def heard(result_json: str) -> str:
    try:
        return " ".join(f"{w['word']}({w.get('conf', 0):.2f})" for w in json.loads(result_json).get("result") or [])
    except Exception:
        return ""


def phrase_hit(result_json: str) -> bool:
    """True when a Vosk partial/final result contains the whole wake phrase."""
    try:
        d = json.loads(result_json or "{}")
    except ValueError:
        return False
    return PHRASE in " ".join((d.get("partial") or d.get("text") or "").split())


def rms(frame) -> float:
    try:
        import numpy as np
        a = np.asarray(frame, dtype="float32")
        return float(math.sqrt(float((a * a).mean()))) if a.size else 0.0
    except Exception:
        return 0.0


HUD_PROFILE = str(Path.home() / ".hermes" / "jarvis-hud-chrome")


def hud_window_open() -> bool:
    """Is the HUD's own Chrome (its dedicated profile) running?"""
    try:
        import subprocess
        r = subprocess.run(["pgrep", "-f", f"user-data-dir={HUD_PROFILE}"], capture_output=True, text=True, timeout=3)
        return r.returncode == 0 and bool(r.stdout.strip())
    except Exception:
        return True   # can't tell: trust the flag


STALE_S = float(os.environ.get("WAKE_FLAG_STALE_S", "60"))


def voice_active() -> bool:
    try:
        d = json.loads(FLAG.read_text())
        if not d.get("active"):
            return False
        ts = d.get("ts", "")
        # the HUD re-asserts the flag every 20 s while a session is live; anything older than 60 s is a dead session
        from datetime import datetime, timezone
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds() if ts else 0
        if age >= STALE_S:
            return False
        if d.get("source", "hud").startswith("hud") and not hud_window_open():
            return False   # the HUD was closed mid-conversation; nothing is listening, so we must
        return True
    except Exception:
        return False


def heartbeat(**fields: Any) -> None:
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        HEARTBEAT.write_text(json.dumps({"ts": time.time(), **fields}))
    except Exception:
        pass


def fire(reason: str = "wake word") -> None:
    print(f"[wake] {time.strftime('%Y-%m-%d %H:%M:%S')} detected ({reason}); opening HUD", flush=True)
    try:
        subprocess.Popen(["/bin/bash", HUD, "--wake"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"[wake] failed to launch HUD: {e}", flush=True)


def main() -> int:
    import numpy as np
    import sounddevice as sd
    from pyopen_wakeword import OpenWakeWord, OpenWakeWordFeatures

    model_path = os.environ.get("WAKE_MODEL") or str(Path(__file__).with_name("hey_jarvis_v0.1.tflite"))
    det = Detector(float(os.environ.get("WAKE_THRESHOLD", "0.75")), int(os.environ.get("WAKE_CONFIRM", "4")), float(os.environ.get("WAKE_COOLDOWN", "6")))
    features, model = OpenWakeWordFeatures.from_builtin(), OpenWakeWord.from_model(model_path)
    print(f"[wake] model={model.id} threshold={det.threshold} confirm={det.confirm}", flush=True)
    phrase_dir = os.environ.get("WAKE_PHRASE_MODEL") or str(Path(__file__).with_name("vosk-model-small-en-us-0.15"))
    rec = None
    if Path(phrase_dir).is_dir():
        from vosk import KaldiRecognizer, Model, SetLogLevel
        SetLogLevel(-1)
        rec = KaldiRecognizer(Model(phrase_dir), RATE, json.dumps(PHRASE_GRAMMAR))
        rec.SetWords(True)   # per-word confidence for final_hit
        print(f'[wake] phrase "Jarvis, wake up" on ({Path(phrase_dir).name})', flush=True)
    else:
        print(f"[wake] phrase model missing ({phrase_dir}); only Hey Jarvis", flush=True)

    # Pin the input device by name (default "MacBook Air Microphone") so macOS Continuity
    # switching the default input to an iPhone can't silently hijack the listener.
    want = os.environ.get("WAKE_DEVICE", "MacBook Air Microphone").lower()
    device = None
    for i, d in enumerate(sd.query_devices()):
        if d.get("max_input_channels", 0) > 0 and want in d.get("name", "").lower():
            device = i
            break
    print(f"[wake] input device: {sd.query_devices(device)['name'] if device is not None else 'system default'}", flush=True)

    silent_since, warned, paused, last_hb = None, False, False, 0.0
    with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME, device=device) as stream:
        print("[wake] listening", flush=True)
        while True:
            frame, _ = stream.read(FRAME)
            frame = frame.reshape(-1)
            level = rms(frame)
            now = time.time()
            # mic permission check: all-zero audio for 10 s means macOS isn't delivering sound
            if level < 1.0:
                silent_since = silent_since or now
                if not warned and now - silent_since > 10:
                    print("[wake] WARNING: microphone delivers only silence; grant Microphone access to 'Jarvis Wake' in System Settings", flush=True)
                    warned = True
            else:
                silent_since, warned = None, False
            # pause while the HUD is mid-conversation (Jarvis's own voice would re-trigger)
            if now - last_hb > 2:
                p = voice_active()
                if p != paused:
                    paused = p
                    print(f"[wake] {'paused (voice session active)' if p else 'resumed'}", flush=True)
                    if not p:
                        features.reset(); model.reset(); det.streak = 0
                        if rec:
                            rec.Reset()
                heartbeat(rms=round(level, 1), paused=paused, detections=det.detections, silent=warned)
                last_hb = now
            if paused:
                continue
            scores = []
            for emb in features.process_streaming(frame.tobytes()):
                scores.extend(model.process_streaming(emb))
            fired = None
            if det.feed(scores) and now - det.last_fire_wall > det.cooldown:
                print(f"[wake] peak score {max(scores):.2f} over {det.confirm} frames", flush=True)   # for tuning WAKE_THRESHOLD
                fired = "hey jarvis"
            elif rec is not None:
                # Final results only (~0.5 s after the phrase ends); see final_hit for why partials are out.
                if rec.AcceptWaveform(frame.tobytes()):
                    res = rec.Result()
                    if final_hit(res) and now - det.last_fire_wall > det.cooldown:
                        fired = "jarvis wake up"
                        print(f"[wake] heard: {heard(res)}", flush=True)
            if fired:
                det.last_fire_wall = now
                fire(fired)
                features.reset(); model.reset()
                if rec:
                    rec.Reset()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
