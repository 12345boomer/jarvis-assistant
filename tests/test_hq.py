import json
import subprocess

import pytest

from jarvis import hq


def _opt(cmd, flag):
    return cmd[cmd.index(flag) + 1]


# ---- env scrubbing ---------------------------------------------------------------------------------------------

def test_child_env_drops_every_anthropic_api_variable():
    base = {"ANTHROPIC_API_KEY": "sk-ant-usr-x", "ANTHROPIC_AUTH_TOKEN": "t", "ANTHROPIC_BASE_URL": "u", "ANTHROPIC_MODEL": "m",
            "CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CODE_USE_VERTEX": "1", "CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "cli",
            "CLAUDE_CODE_OAUTH_TOKEN": "keep", "HOME": "/Users/ben", "PATH": "/usr/bin"}
    env = hq.child_env(base)
    assert not [k for k in env if k.startswith("ANTHROPIC_")]
    assert not {"CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"} & set(env)
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "keep" and env["HOME"] == "/Users/ben"     # subscription login stays
    assert ".local/bin" in env["PATH"] and env["PATH"].endswith("/usr/bin")


def test_ask_runs_with_scrubbed_env_in_hq(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-usr-secret")
    monkeypatch.setattr(hq, "HQ_DIR", tmp_path)
    seen = {}
    def run(cmd, **kw):
        seen.update(kw, cmd=cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="Harvey has 15 drafts waiting.\n", stderr="")
    assert hq.ask("what drafts are waiting?", run=run) == {"ok": True, "answer": "Harvey has 15 drafts waiting."}
    assert "ANTHROPIC_API_KEY" not in seen["env"] and seen["cwd"] == str(tmp_path)
    assert seen["cmd"][1:3] == ["-p", "what drafts are waiting?"] and seen["stdin"] == subprocess.DEVNULL


# ---- read-only tool list ----------------------------------------------------------------------------------------

def test_command_is_read_only():
    cmd = hq.command("q", claude="claude")
    assert _opt(cmd, "--tools") == "Read,Grep,Glob,Agent,Skill,Bash"
    assert _opt(cmd, "--permission-mode") == "dontAsk"
    assert _opt(cmd, "--setting-sources") == "project"          # not Ben's user-level allow list
    assert "--strict-mcp-config" in cmd                         # no connectors (Gmail send etc.)
    assert _opt(cmd, "--output-format") == "text"
    assert "read-only question" in _opt(cmd, "--append-system-prompt") and "Jessica chat" in _opt(cmd, "--append-system-prompt")
    perms = json.loads(_opt(cmd, "--settings"))["permissions"]
    allow, deny = perms["allow"], perms["deny"]
    assert {"Edit", "Write", "NotebookEdit"} <= set(deny) and "Read(**/.env)" in deny
    assert all(r.startswith("Bash(") for r in allow)
    bash = " ".join(allow)
    for verb in ("status)", "outbox)", "usage)", "export --out /dev/stdout)", "sqlite3 -readonly"):
        assert verb in bash
    for bad in ("--approve", "--reject", "sending", "rm ", "python", "curl", "Bash(*)"):
        assert bad not in bash
    assert not [r for r in allow if r.endswith(" export)")]       # plain export writes prospects.csv


# ---- timeout and errors -----------------------------------------------------------------------------------------

def test_timeout_returns_plain_message(monkeypatch, tmp_path):
    monkeypatch.setattr(hq, "HQ_DIR", tmp_path)
    def run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])
    r = hq.ask("how are sales going?", timeout=170, run=run)
    assert r == {"ok": False, "answer": "Jessica couldn't answer: she took longer than 2 minutes, so I stopped waiting."}
    assert hq.TIMEOUT_S < 180                                   # fires before Hermes's own terminal cap


@pytest.mark.parametrize("rc,out,err,expect", [
    (1, "", "Invalid API key · Please run /login", "Claude Code isn't logged in to Ben's subscription"),
    (0, "   ", "", "exit 0"),
    (2, "", "boom", "boom"),
])
def test_errors_return_plain_message(monkeypatch, tmp_path, rc, out, err, expect):
    monkeypatch.setattr(hq, "HQ_DIR", tmp_path)
    r = hq.ask("q", run=lambda cmd, **kw: subprocess.CompletedProcess(cmd, rc, stdout=out, stderr=err))
    assert not r["ok"] and r["answer"].startswith("Jessica couldn't answer:") and expect in r["answer"]


def test_missing_hq_and_empty_question(monkeypatch, tmp_path):
    assert not hq.ask("  ")["ok"]
    monkeypatch.setattr(hq, "HQ_DIR", tmp_path / "nope")
    assert "HQ folder" in hq.ask("q", run=lambda *a, **k: 1 / 0)["answer"]


def test_hq_ask_is_tier1(cfg):
    from jarvis.tiers import classify
    d = classify("terminal", {"command": '$HOME/Documents/JARVIS/.venv/bin/jarvis hq ask "how are sales going?"'}, cfg["permissions"])
    assert d.tier == 1
