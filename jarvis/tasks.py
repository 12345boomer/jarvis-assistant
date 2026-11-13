"""Ben's task list: a JSON file Jarvis (voice/chat) and the HUD both edit."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERMES_HOME = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
PATH = Path(os.environ.get("JARVIS_TASKS_FILE") or HERMES_HOME / "jarvis-state" / "tasks.json")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load() -> list[dict[str, Any]]:
    try:
        return json.loads(PATH.read_text())
    except Exception:
        return []


def save(tasks: list[dict[str, Any]]) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(tasks, indent=2))


def add(text: str, due: str | None = None, source: str = "hud") -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty task")
    t = {"id": uuid.uuid4().hex[:8], "text": text[:200], "due": due, "done": False, "created": _now(), "source": source}
    tasks = load(); tasks.append(t); save(tasks)
    return t


def complete(task_id: str, done: bool = True) -> dict[str, Any] | None:
    tasks = load()
    for t in tasks:
        if t["id"] == task_id or (done and t["text"].lower() == task_id.lower()):
            t["done"] = done; t["completed"] = _now() if done else None
            save(tasks); return t
    return None


def remove(task_id: str) -> bool:
    tasks = load(); kept = [t for t in tasks if t["id"] != task_id]
    save(kept); return len(kept) != len(tasks)


def open_tasks() -> list[dict[str, Any]]:
    return [t for t in load() if not t.get("done")]
