import pytest

from jarvis import mac
from jarvis.audit import AuditLog
from jarvis.tiers import ASK, DO, NEVER, classify


def test_open_cmd_variants():
    assert mac.open_cmd("Spotify") == ["open", "-a", "Spotify"]
    assert mac.open_cmd("https://youtube.com") == ["open", "https://youtube.com"]
    assert mac.open_cmd("youtube.com") == ["open", "https://youtube.com"]
    with pytest.raises(ValueError):
        mac.open_cmd("/etc/passwd")
    with pytest.raises(ValueError):
        mac.open_cmd("App; rm -rf ~")


def test_wake_and_volume_cmds():
    cmds = mac.wake_screen_cmds()
    assert cmds[0][:2] == ["caffeinate", "-u"] and cmds[1][1] == str(mac.HUD_SCRIPT)
    assert mac.volume_cmd(150) == ["osascript", "-e", "set volume output volume 100"]
    assert mac.say_cmd('He said "hi" \\ bye') == ["osascript", "-e", 'display notification "He said \\"hi\\" \\\\ bye" with title "Jarvis"']


def test_run_logs_event_without_executing():
    calls = []
    class R:  # fake CompletedProcess
        returncode = 0; stderr = ""
    r = mac.run([["echo", "hi"]], event="mac_open", detail="x", runner=lambda c, **k: calls.append(c) or R())
    assert r["status"] == "ok" and calls == [["echo", "hi"]]
    assert AuditLog().recent_events(1)[0]["kind"] == "mac_open"


@pytest.mark.parametrize("tool,args,tier", [
    ("terminal", {"command": "open -a Spotify"}, DO),
    ("terminal", {"command": "open https://youtube.com"}, DO),
    ("terminal", {"command": "$HOME/Documents/JARVIS/.venv/bin/jarvis mac wake-screen"}, DO),
    ("terminal", {"command": "osascript -e 'tell application \"Spotify\" to activate'"}, DO),
    ("terminal", {"command": "osascript -e 'set volume output volume 20'"}, DO),
    ("terminal", {"command": "osascript -e 'tell application \"Messages\" to send \"hi\" to buddy \"x\"'"}, ASK),
    ("terminal", {"command": "caffeinate -u -t 5"}, DO),
    ("tool_describe", {"names": ["computer_use"]}, DO),
    ("computer_use", {"action": "capture", "app": "Mail"}, DO),
    ("computer_use", {"action": "click", "element": 14}, DO),
    ("computer_use", {"action": "type", "text": "from:stripe"}, DO),
    ("computer_use", {"action": "type", "text": "Place order"}, ASK),
    ("computer_use", {"action": "key", "keys": "return"}, DO),
    ("computer_use", {"action": "type", "text": "4111 1111 1111 1111"}, NEVER),
    ("computer_use", {"action": "type", "text": "hunter2hunter2hunter2!"}, NEVER),   # password-shaped token
    ("computer_use", {"action": "type", "text": "meet me at 3pm at the cafe tomorrow"}, DO),  # ordinary sentence with a digit
    ("computer_use", {"action": "type", "text": "reset your password link please"}, DO),     # the word alone isn't a field
    ("browser_exec", {"script": "page.fill('#password', 'x')"}, NEVER),
    ("browser_exec", {"script": "page.click('text=Checkout')"}, ASK),
])
def test_mac_tiers(cfg, tool, args, tier):
    assert classify(tool, args, cfg["permissions"]).tier == tier


def test_brief_cmd_is_tier1(cfg):
    cmd = mac.brief_cmd()
    assert cmd[0] == "/bin/bash" and cmd[1].endswith("/.hermes/jarvis-hud.sh") and cmd[2] == "--brief"
    assert classify("terminal", {"command": "$HOME/Documents/JARVIS/.venv/bin/jarvis mac brief"}, cfg["permissions"]).tier == DO


def test_text_and_contact_cmds(cfg):
    cmd = mac.text_cmd("+1 (203) 555-1234", 'Running 10 "late"')
    assert cmd[:2] == ["osascript", "-e"] and 'send "Running 10 \\"late\\"" to participant "+1 (203) 555-1234"' in cmd[2] and "iMessage" in cmd[2]
    assert "SMS" in mac.text_cmd("chris@x.com", "hi", sms=True)[2]
    for bad in ("Chris", "+1; rm -rf ~", ""):
        with pytest.raises(ValueError):
            mac.text_cmd(bad, "hi")
    with pytest.raises(ValueError):
        mac.text_cmd("+12035551234", "  ")
    assert 'name contains "Chris"' in mac.contact_cmd("Chris")[2]
    perms = cfg["permissions"]
    assert classify("terminal", {"command": "$JARVIS mac text \"+12035551234\" -m \"hi\""}, perms).tier == ASK
    assert classify("terminal", {"command": "$JARVIS mac contact \"Chris\""}, perms).tier == DO


def test_applescript_reads_are_tier1_writes_ask(cfg):
    p = cfg["permissions"]
    assert classify("terminal", {"command": "osascript -e 'tell application \"Contacts\" to get name of every person'"}, p).tier == DO
    assert classify("terminal", {"command": "osascript -e 'tell application \"System Events\" to get name of processes'"}, p).tier == DO
    assert classify("terminal", {"command": "osascript -e 'tell application \"Contacts\" to delete every person'"}, p).tier == ASK
    assert classify("terminal", {"command": "osascript -e 'tell application \"Messages\" to send \"hi\" to participant \"+1\"'"}, p).tier == ASK


PS = f"""  123 {mac.CHROME_BIN} --headless=new --user-data-dir=/Users/b/.hermes/jarvis-browser-chrome
  456 {mac.CHROME_BIN} --type=renderer --lang=en
  789 {mac.CHROME_BIN} --user-data-dir=/Users/b/.hermes/jarvis-hud-chrome --app=http://127.0.0.1:8650/
  999 {mac.CHROME_BIN}
 1000 /Applications/Other.app/Contents/MacOS/Google Chrome
"""


def test_main_chrome_pid_skips_hud_headless_and_renderers():
    assert mac.main_chrome_pid(PS) == 999
    assert mac.main_chrome_pid("  1 /bin/sh\n") is None


def test_chrome_cmds_target_pid_and_validate():
    assert mac.chrome_tabs_cmd(999)[:3] == ["osascript", "-l", "JavaScript"] and "Application(999)" in mac.chrome_tabs_cmd(999)[-1]
    open_js = mac.chrome_open_cmd(999, "https://tradingview.com/chart/")[-1]
    assert "Application(999)" in open_js and '"https://tradingview.com/chart/"' in open_js and "c.Tab(" in open_js
    with pytest.raises(ValueError):
        mac.chrome_open_cmd(999, "file:///etc/passwd")
    focus_js = mac.chrome_focus_cmd(999, 'trading"view')[-1]
    assert '"trading\\"view"' in focus_js   # JSON-escaped, no script injection
    with pytest.raises(ValueError):
        mac.chrome_focus_cmd(999, "")


def test_open_cmds_routes_urls_to_main_chrome():
    assert mac.open_cmds("https://x.com", pid=999)[0][:3] == ["osascript", "-l", "JavaScript"]
    assert mac.open_cmds("youtube.com", pid=999)[0][-1].count("https://youtube.com") >= 1
    assert mac.open_cmds("Spotify", pid=999) == [["open", "-a", "Spotify"]]
    assert mac.open_cmds("https://x.com", pid=0) == [["open", "https://x.com"]]   # Chrome not running: plain open


def test_chrome_commands_are_tier1():
    import yaml
    cfg = yaml.safe_load(open("config/jarvis.yaml"))
    for c in ["$JARVIS mac chrome tabs", "$JARVIS mac chrome open -m https://x.com", "$JARVIS mac chrome focus -m tradingview"]:
        assert classify("terminal", {"command": c}, cfg["permissions"]).tier == DO
