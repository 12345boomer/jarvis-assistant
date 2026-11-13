#!/usr/bin/env bash
# Install the Jarvis HUD as a launchd service OUTSIDE ~/Documents (launchd processes can't read Documents).
# Copies jarvis/ + dashboard/ + config/jarvis.yaml + .env into ~/.hermes/jarvis-dashboard/, builds a venv,
# registers ai.jarvis.dashboard on 127.0.0.1:8650 (auto-start, auto-restart). Re-run after edits.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
DIR="$HERMES_HOME/jarvis-dashboard"
PLIST="$HOME/Library/LaunchAgents/ai.jarvis.dashboard.plist"
PORT="${DASHBOARD_PORT:-8650}"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"

mkdir -p "$DIR/config" "$HERMES_HOME/logs"
rsync -a --delete --exclude '__pycache__' "$REPO/jarvis/" "$DIR/jarvis/"
rsync -a --delete "$REPO/dashboard/" "$DIR/dashboard/"
cp "$REPO/config/jarvis.yaml" "$DIR/config/jarvis.yaml"
cp "$REPO/scripts/jarvis-hud.sh" "$HOME/.hermes/jarvis-hud.sh" && chmod +x "$HOME/.hermes/jarvis-hud.sh"   # wake listener + dashboard launch the HUD from here
cp "$REPO/.env" "$DIR/.env" && chmod 600 "$DIR/.env"

if [ ! -x "$DIR/.venv/bin/python" ]; then uv venv -q "$DIR/.venv"; fi
uv pip install -q --python "$DIR/.venv/bin/python" "fastapi>=0.115,<1" "uvicorn>=0.30,<1" "httpx>=0.27,<1" "pyyaml>=6,<7"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>ai.jarvis.dashboard</string>
  <key>ProgramArguments</key><array>
    <string>$DIR/.venv/bin/python</string><string>-m</string><string>uvicorn</string>
    <string>dashboard.app:app</string><string>--host</string><string>127.0.0.1</string><string>--port</string><string>$PORT</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>EnvironmentVariables</key><dict>
    <key>JARVIS_ROOT</key><string>$DIR</string>
    <key>HERMES_HOME</key><string>$HERMES_HOME</string>
    <key>PYTHONPATH</key><string>$DIR</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$HERMES_HOME/logs/dashboard.log</string>
  <key>StandardErrorPath</key><string>$HERMES_HOME/logs/dashboard.log</string>
</dict></plist>
EOF
if launchctl print "gui/$(id -u)/ai.jarvis.dashboard" >/dev/null 2>&1; then
  launchctl bootout "gui/$(id -u)/ai.jarvis.dashboard" 2>/dev/null || true
  for i in $(seq 1 20); do launchctl print "gui/$(id -u)/ai.jarvis.dashboard" >/dev/null 2>&1 || break; sleep 0.5; done
fi
launchctl bootstrap "gui/$(id -u)" "$PLIST"
sleep 3
code=$(curl -s -m 5 -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/health" || true)
echo "dashboard registered: $PLIST (health on $PORT -> $code)"
echo "open: http://127.0.0.1:$PORT/$( [ -n "$(grep -E '^DASHBOARD_SECRET=.' "$REPO/.env" | cut -d= -f2-)" ] && echo '?token=<DASHBOARD_SECRET from .env>' )"
