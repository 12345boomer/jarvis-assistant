#!/usr/bin/env bash
# Open the Jarvis HUD as a dedicated full-screen Chrome app window (no tabs, no browser chrome) and bring it to the front.
# Usage: jarvis-hud.sh [--wake | --brief | --call] [--no-post]
#   --wake  auto-starts the voice session; --brief opens the brief overlay. If a HUD page is already open, it is told
#   over its SSE stream (POST /api/hud/wake|brief) unless --no-post (the dashboard already did that).
# Copied to ~/.hermes/jarvis-hud.sh by `make link` so launchd jobs (wake listener, dashboard) can run it.
#
# Two macOS facts shape this script:
#  1. Closing the HUD window does NOT quit its Chrome; a windowless process lingers. We detect that via the dashboard's
#     open-page count and kill the zombie before launching, otherwise "Hey Jarvis" would raise nothing.
#  2. A process started from launchd can't bring a window to the front by exec'ing the Chrome binary. Launching through
#     LaunchServices (`open -n -a`) activates it, so the HUD actually appears on top.
set -u
PORT="${DASHBOARD_PORT:-8650}"
SECRET=$(grep -E '^DASHBOARD_SECRET=' "$HOME/.hermes/jarvis-dashboard/.env" 2>/dev/null | cut -d= -f2- | tr -d '"')
URL="http://127.0.0.1:$PORT/?token=$SECRET"
MODE="${1:-}"; NOPOST="${2:-}"
[ "$MODE" = "--wake" ] && URL="$URL&wake=1"
[ "$MODE" = "--brief" ] && URL="$URL&brief=1"
[ "$MODE" = "--call" ] && URL="$URL&call=1"
CHROME_APP="Google Chrome"
PROFILE="$HOME/.hermes/jarvis-hud-chrome"   # own profile: keeps the cookie, never mixes with Ben's tabs
API="http://127.0.0.1:$PORT/api"

# Turn the display on first (no-op if already on).
caffeinate -u -t 3 2>/dev/null &

if [ ! -d "/Applications/$CHROME_APP.app" ]; then open "$URL"; exit 0; fi

HUDPID=$(pgrep -f "user-data-dir=$PROFILE" | head -1)
if [ -n "$HUDPID" ]; then
  clients=$(curl -s -m 3 -H "Cookie: jarvis=$SECRET" "$API/hud/clients" | sed -n 's/.*"clients": *\([0-9]*\).*/\1/p')
  FRONTPID=$(lsappinfo info -only pid "$(lsappinfo front)" 2>/dev/null | sed -n 's/.*"pid"=\([0-9]*\).*/\1/p')
  if [ "${clients:-0}" -gt 0 ] && [ "$FRONTPID" = "$HUDPID" ]; then
    # A HUD page is open and already in front: just tell it what to do over its event stream.
    if [ -n "$MODE" ] && [ "$NOPOST" != "--no-post" ]; then
      code=$(curl -s -m 3 -o /dev/null -w "%{http_code}" -X POST -H "Cookie: jarvis=$SECRET" "$API/hud/${MODE#--}")
      echo "hud ${MODE#--} -> $code"
    fi
    exit 0
  fi
  # Either the window was closed (Chrome lingering with no page) or the HUD is behind another app. A background
  # process can't raise a window without Accessibility, but a fresh launch through LaunchServices does come to the
  # front, so relaunch. The page keeps no local state; everything comes back from the API in about a second.
  pkill -f "user-data-dir=$PROFILE" 2>/dev/null
  for i in $(seq 1 20); do pgrep -f "user-data-dir=$PROFILE" >/dev/null || break; sleep 0.25; done
fi

open -n -a "$CHROME_APP" --args --user-data-dir="$PROFILE" --app="$URL" --start-fullscreen --no-first-run \
  --no-default-browser-check --disable-features=TranslateUI --autoplay-policy=no-user-gesture-required \
  --remote-debugging-port=9334
echo "hud launched${MODE:+ ($MODE)}"
