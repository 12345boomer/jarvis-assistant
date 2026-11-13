#!/usr/bin/env bash
# Install the "Hey Jarvis" wake-word listener as a launchd service, OUTSIDE ~/Documents.
#   ~/.hermes/jarvis-wake/.venv            Python 3.12 + pyopen-wakeword + sounddevice
#   ~/.hermes/jarvis-wake/hey_jarvis_v0.1.tflite   openWakeWord model (SHA-256 pinned)
#   ~/.hermes/jarvis-wake/Jarvis Wake.app  tiny applet wrapper so macOS attributes the mic grant to it
#   ~/Library/LaunchAgents/ai.jarvis.wake.plist
# Re-run after editing jarvis/wake_listener.py.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
DIR="$HERMES_HOME/jarvis-wake"
APP="$DIR/Jarvis Wake.app"
PLIST="$HOME/Library/LaunchAgents/ai.jarvis.wake.plist"
MODEL_URL="https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/hey_jarvis_v0.1.tflite"
MODEL_SHA="14bff778604985e1b5c19f0f7bbe477a69cf281d8db34b232b3b972411f710e2"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"

mkdir -p "$DIR" "$HERMES_HOME/logs" "$HERMES_HOME/jarvis-state"
cp "$REPO/jarvis/wake_listener.py" "$DIR/wake_listener.py"

if [ ! -x "$DIR/.venv/bin/python" ]; then
  uv python install 3.12 >/dev/null 2>&1 || true
  uv venv -q --python 3.12 "$DIR/.venv"
fi
uv pip install -q --python "$DIR/.venv/bin/python" "pyopen-wakeword==1.1.0" "sounddevice>=0.5,<1" "numpy>=1.26,<3"

if [ ! -f "$DIR/hey_jarvis_v0.1.tflite" ]; then
  curl -sL -m 120 -o "$DIR/hey_jarvis_v0.1.tflite" "$MODEL_URL"
fi
got=$(shasum -a 256 "$DIR/hey_jarvis_v0.1.tflite" | cut -d' ' -f1)
if [ "$MODEL_SHA" != "__MODEL_SHA__" ] && [ "$got" != "$MODEL_SHA" ]; then
  echo "model checksum mismatch ($got); refusing to use it" >&2; rm -f "$DIR/hey_jarvis_v0.1.tflite"; exit 1
fi

# Applet wrapper: `do shell script` blocks for the listener's lifetime, so the applet is the
# responsible process for TCC and its name shows up under System Settings → Microphone.
rm -rf "$APP"
osacompile -o "$APP" -e "do shell script \"cd '$DIR' && exec '$DIR/.venv/bin/python' '$DIR/wake_listener.py' >> '$HERMES_HOME/logs/wake.log' 2>&1\""
/usr/libexec/PlistBuddy -c "Add :NSMicrophoneUsageDescription string 'Jarvis listens for the wake phrase on-device.'" "$APP/Contents/Info.plist" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier ai.jarvis.wake" "$APP/Contents/Info.plist" 2>/dev/null || /usr/libexec/PlistBuddy -c "Add :CFBundleIdentifier string ai.jarvis.wake" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" "$APP/Contents/Info.plist" 2>/dev/null || true   # no Dock icon
codesign --force --sign - "$APP" >/dev/null 2>&1 || true

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>ai.jarvis.wake</string>
  <key>ProgramArguments</key><array><string>/usr/bin/open</string><string>-W</string><string>-a</string><string>$APP</string></array>
  <key>EnvironmentVariables</key><dict><key>HERMES_HOME</key><string>$HERMES_HOME</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>15</integer>
  <key>StandardOutPath</key><string>$HERMES_HOME/logs/wake-launchd.log</string>
  <key>StandardErrorPath</key><string>$HERMES_HOME/logs/wake-launchd.log</string>
</dict></plist>
EOF
if launchctl print "gui/$(id -u)/ai.jarvis.wake" >/dev/null 2>&1; then
  launchctl bootout "gui/$(id -u)/ai.jarvis.wake" 2>/dev/null || true
  for i in $(seq 1 20); do launchctl print "gui/$(id -u)/ai.jarvis.wake" >/dev/null 2>&1 || break; sleep 0.5; done
fi
pkill -f "jarvis-wake/wake_listener.py" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
sleep 6
echo "wake listener registered: $PLIST"
tail -3 "$HERMES_HOME/logs/wake.log" 2>/dev/null || true
echo "If macOS asked about the microphone for 'Jarvis Wake', click Allow. Check: make wake-status"
