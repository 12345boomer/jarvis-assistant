import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Every test gets its own data dir so audit.db never touches the real one."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("JARVIS_CONFIG", str(REPO / "config" / "jarvis.yaml"))
    from jarvis import config
    config.cached_config.cache_clear()
    yield
    config.cached_config.cache_clear()


@pytest.fixture
def cfg():
    from jarvis.config import load_config
    return load_config(REPO / "config" / "jarvis.yaml")
