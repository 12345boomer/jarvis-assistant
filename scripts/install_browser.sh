#!/usr/bin/env bash
# The Jarvis Browser: a dedicated Chrome profile (~/.hermes/jarvis-browser-chrome) with remote debugging on 127.0.0.1:9333.
# Runs HEADLESS: Ben's rule is that Jarvis never opens windows (the HUD is the only exception). For a one-time sign-in,
# `jarvis browser show <url>` brings up a visible window with the same profile; `jarvis browser hide` (or `jarvis school
# setup`) returns to hidden. Hermes's browser tool and `jarvis school` attach over CDP and reuse the signed-in session.
set -euo pipefail
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PROFILE="$HERMES_HOME/jarvis-browser-chrome"
PORT="${JARVIS_BROWSER_PORT:-9333}"
PLIST="$HOME/Library/LaunchAgents/ai.jarvis.browser.plist"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
[ -x "$CHROME" ] || { echo "Google Chrome not found in /Applications"; exit 1; }
mkdir -p "$PROFILE" "$HERMES_HOME/logs"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>ai.jarvis.browser</string>
  <key>ProgramArguments</key><array>
    <string>$CHROME</string>
    <string>--headless=new</string>
    <string>--user-data-dir=$PROFILE</string>
    <string>--remote-debugging-port=$PORT</string>
    <string>--no-first-run</string><string>--no-default-browser-check</string>
    <string>--window-size=1280,860</string>
    <string>--disable-features=TranslateUI</string>
    <string>about:blank</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>15</integer>
  <key>StandardOutPath</key><string>$HERMES_HOME/logs/browser.log</string>
  <key>StandardErrorPath</key><string>$HERMES_HOME/logs/browser.log</string>
</dict></plist>
PL
# If a Jarvis Browser was started by hand (same profile), leave it alone: launchd would fail to lock the profile.
if curl -s -m 2 "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1; then
  launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || true   # registers for next login; the running one keeps serving
  echo "browser already running on $PORT; launchd job registered for next login ($PLIST)"
  exit 0
fi
launchctl bootout "gui/$(id -u)/ai.jarvis.browser" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
sleep 4
curl -s -m 3 "http://127.0.0.1:$PORT/json/version" | grep -o '"Browser": *"[^"]*"' || echo "browser not answering yet; check $HERMES_HOME/logs/browser.log"
