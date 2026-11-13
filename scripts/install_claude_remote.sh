#!/bin/zsh
# One-time setup: start Claude Remote now and at every login.
set -e
SCRIPT="$HOME/Documents/JARVIS/scripts/claude-remote.command"
chmod +x "$SCRIPT"

if osascript -e 'tell application "System Events" to get the name of every login item' | grep -q "claude-remote"; then
  echo "Login item already there."
else
  osascript -e "tell application \"System Events\" to make login item at end with properties {path:\"$SCRIPT\", hidden:false}" >/dev/null
  echo "Added Claude Remote to Login Items."
fi

open "$SCRIPT"
echo "Started. A Terminal window titled 'Claude Remote' is now running. Leave it open."
