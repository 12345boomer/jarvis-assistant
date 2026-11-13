"""Load config/jarvis.yaml and resolve repo paths.

Paths can be overridden with env vars so tests and the Hermes plugin (which runs
inside Hermes's process, from a symlinked directory) resolve the same files:
  JARVIS_ROOT       repo root (default: parent of this package)
  JARVIS_CONFIG     path to jarvis.yaml (default: <root>/config/jarvis.yaml)
  JARVIS_DATA_DIR   where audit.db lives (default: <root>/data)
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get("JARVIS_ROOT") or Path(__file__).resolve().parents[1])


def _parse_yaml(text: str) -> Any:
    """PyYAML in our own env; ruamel.yaml when running inside Hermes's process."""
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text)
    except ImportError:
        from ruamel.yaml import YAML  # type: ignore
        return YAML(typ="safe").load(text)


def config_path() -> Path:
    return Path(os.environ.get("JARVIS_CONFIG") or ROOT / "config" / "jarvis.yaml")


def data_dir() -> Path:
    """Runtime data (audit.db). Lives under ~/.hermes, not the repo, because launchd services
    (dashboard, bridge) can't read ~/Documents on macOS."""
    hermes_home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    d = Path(os.environ.get("JARVIS_DATA_DIR") or hermes_home / "jarvis-data")
    d.mkdir(parents=True, exist_ok=True)
    return d


def audit_db_path() -> Path:
    return data_dir() / "audit.db"


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Read jarvis.yaml. Missing file -> empty dict so callers fall back to defaults."""
    p = path or config_path()
    if not p.exists():
        return {}
    return _parse_yaml(p.read_text()) or {}


@lru_cache(maxsize=1)
def cached_config() -> dict[str, Any]:
    """Config loaded once per process. The plugin calls this on every hook; the
    gateway restarts when config changes anyway."""
    return load_config()
