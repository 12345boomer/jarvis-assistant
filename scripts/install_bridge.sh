#!/usr/bin/env bash
# Install the voice bridge as a launchd service OUTSIDE ~/Documents (launchd processes can't read Documents).
# Copies jarvis/voice_bridge.py, config/voice-agent-prompt.md and .env into ~/.hermes/jarvis-bridge/,
# builds a venv there with uv, and registers ai.jarvis.bridge (auto-start, auto-restart). Re-run after edits.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
DIR="$HERMES_HOME/jarvis-bridge"
PLIST="$HOME/Library/LaunchAgents/ai.jarvis.bridge.plist"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"

mkdir -p "$DIR" "$HERMES_HOME/logs"
cp "$REPO/jarvis/voice_bridge.py" "$DIR/voice_bridge.py"
cp "$REPO/config/voice-agent-prompt.md" "$DIR/voice-agent-prompt.md"
cp "$REPO/.env" "$DIR/.env" && chmod 600 "$DIR/.env"

if [ ! -x "$DIR/.venv/bin/python" ]; then
  uv venv -q "$DIR/.venv"
fi
uv pip install -q --python "$DIR/.venv/bin/python" "fastapi>=0.115,<1" "uvicorn>=0.30,<1" "anthropic>=0.40,<2" "httpx>=0.27,<1"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>ai.jarvis.bridge</string>
  <key>ProgramArguments</key><array><string>$DIR/.venv/bin/python</string><string>$DIR/voice_bridge.py</string></array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$HERMES_HOME/logs/bridge.log</string>
  <key>StandardErrorPath</key><string>$HERMES_HOME/logs/bridge.log</string>
</dict></plist>
EOF
if launchctl print "gui/$(id -u)/ai.jarvis.bridge" >/dev/null 2>&1; then
  launchctl bootout "gui/$(id -u)/ai.jarvis.bridge" 2>/dev/null || true
  for i in $(seq 1 20); do launchctl print "gui/$(id -u)/ai.jarvis.bridge" >/dev/null 2>&1 || break; sleep 0.5; done
fi
launchctl bootstrap "gui/$(id -u)" "$PLIST"
sleep 3
code=$(curl -s -m 5 -o /dev/null -w "%{http_code}" http://127.0.0.1:8642/health || true)
echo "bridge registered: $PLIST (health on 8642 -> $code)"
