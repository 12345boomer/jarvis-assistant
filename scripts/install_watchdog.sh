#!/usr/bin/env bash
# Register the watchdog as a launchd job that runs every 5 minutes.
# The script is COPIED to ~/.hermes because launchd jobs can't read ~/Documents
# (macOS privacy protection, "Operation not permitted"). Re-run after editing watchdog.sh.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
TARGET="$HERMES_HOME/jarvis-watchdog.sh"
PLIST="$HOME/Library/LaunchAgents/ai.jarvis.watchdog.plist"

cp "$REPO/scripts/watchdog.sh" "$TARGET"
chmod +x "$TARGET"
mkdir -p "$HOME/Library/LaunchAgents" "$HERMES_HOME/logs"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>ai.jarvis.watchdog</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$TARGET</string></array>
  <key>StartInterval</key><integer>300</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$HERMES_HOME/logs/watchdog.log</string>
  <key>StandardErrorPath</key><string>$HERMES_HOME/logs/watchdog.log</string>
</dict></plist>
EOF
launchctl bootout "gui/$(id -u)/ai.jarvis.watchdog" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "watchdog registered: $PLIST -> $TARGET"
