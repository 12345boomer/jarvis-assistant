"""jarvis-guard: thin Hermes plugin shim.

This directory is symlinked to ~/.hermes/plugins/jarvis-guard. The permission logic
lives in the repo's `jarvis` package so it has one home and one test suite. We find
the repo root in this order:
  1. JARVIS_ROOT env var
  2. a `.repo_root` file next to this __init__.py (written by `make link`; survives
     Hermes copying the plugin into a temp dir, e.g. `hermes plugins doctor`)
  3. resolving this file's symlink back to the repo
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE = Path(__file__).parent


def _repo_root() -> Path:
    if env := os.environ.get("JARVIS_ROOT"):
        return Path(env)
    marker = _HERE / ".repo_root"
    if marker.exists():
        p = Path(marker.read_text().strip())
        if (p / "jarvis" / "guard.py").exists():
            return p
    return Path(__file__).resolve().parents[2]


_ROOT = _repo_root()
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
os.environ.setdefault("JARVIS_ROOT", str(_ROOT))

from jarvis import guard  # noqa: E402


def register(ctx) -> None:
    guard.register(ctx)
