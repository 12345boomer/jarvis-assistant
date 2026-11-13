#!/bin/bash
# Lock policy: lid closed -> password on reopen; lid open + dark screen -> no password.
# Step 1 (Ben, once, needs his password): sudo sysadminctl -screenLock off
# Step 2 (this script): sleepwatcher hooks. macOS 26 removed CGSession, and the login framework's lock call hangs
# during the sleep transition, so ~/.sleep records the lid state and ~/.wakeup locks on wake when the lid was closed.
set -e
command -v brew >/dev/null || { echo "Homebrew missing"; exit 1; }
brew list sleepwatcher >/dev/null 2>&1 || brew install sleepwatcher
mkdir -p "$HOME/.hermes/logs"
# One lock per lid close (2026-11-05, third fix). The lock request sent at lid close lands on wake, before the desktop
# shows; it is the lock that matters. sleepwatcher runs ~/.wakeup several seconds after wake, by which time Ben has
# often already unlocked with Touch ID, so the wake hook can't tell "it locked and he unlocked" from "it never locked"
# by looking at the current state (both read unlocked); guessing "never locked" sent a second lock. So ~/.sleep also
# starts a watcher that polls the lock state every 0.1 s and records the moment it sees "locked". It is frozen during
# sleep and thaws the instant the Mac wakes, before the lock screen can be dismissed. ~/.wakeup reads that record and
# sends one fallback lock only when the watcher was running and never saw a lock.
cat > "$HOME/.sleep" <<'HOOK'
#!/bin/bash
# sleepwatcher sleep hook (installed by scripts/install_lock.sh). Lid closed: request the lock (it lands on wake) and
# start the watcher that records whether a lock really happened.
LOG="$HOME/.hermes/logs/sleepwatcher.log"; S="$HOME/.hermes/jarvis-state/lock"; FLAG="$HOME/.hermes/lock-on-wake"
mkdir -p "$S"
if ioreg -r -k AppleClamshellState -d 4 2>/dev/null | grep -q '"AppleClamshellState" = Yes'; then
  touch "$FLAG"; rm -f "$S/seen-locked" "$S/alive"
  [ -f "$S/watcher.pid" ] && kill "$(cat "$S/watcher.pid")" 2>/dev/null
  ( while true; do
      if ioreg -n Root -d1 -a | grep -A1 IOConsoleLocked | grep -q true; then date '+%F %T' > "$S/seen-locked"; exit 0; fi
      date +%s > "$S/alive"; sleep 0.1
    done ) >/dev/null 2>&1 &
  echo $! > "$S/watcher.pid"
  echo "$(date '+%F %T') sleep: lid closed -> lock requested, watcher on" >> "$LOG"
  ( /usr/bin/python3 -c 'import ctypes; ctypes.CDLL("/System/Library/PrivateFrameworks/login.framework/login").SACLockScreenImmediate()' >/dev/null 2>&1 & sleep 3; kill $! 2>/dev/null ) >/dev/null 2>&1 &
else
  rm -f "$FLAG"; echo "$(date '+%F %T') sleep: lid open -> no lock" >> "$LOG"
fi
HOOK
cat > "$HOME/.wakeup" <<'HOOK'
#!/bin/bash
# sleepwatcher wake hook (installed by scripts/install_lock.sh): never lock twice. Trust the watcher's record.
LOG="$HOME/.hermes/logs/sleepwatcher.log"; S="$HOME/.hermes/jarvis-state/lock"; FLAG="$HOME/.hermes/lock-on-wake"
( sleep 30; [ -x "$HOME/.hermes/jarvis-watchdog.sh" ] && bash "$HOME/.hermes/jarvis-watchdog.sh" ) >/dev/null 2>&1 &   # Tailscale often needs a kick after sleep
[ -e "$FLAG" ] || { echo "$(date '+%F %T') wake: no flag, staying unlocked" >> "$LOG"; exit 0; }
rm -f "$FLAG"
woke=$(date +%s)
for i in $(seq 1 15); do [ -f "$S/seen-locked" ] && break; sleep 0.2; done   # give the watcher a moment
pid=$(cat "$S/watcher.pid" 2>/dev/null); [ -n "$pid" ] && kill "$pid" 2>/dev/null; rm -f "$S/watcher.pid"
if [ -f "$S/seen-locked" ]; then
  echo "$(date '+%F %T') wake: lid-close lock happened at $(cat "$S/seen-locked"), nothing to do" >> "$LOG"; exit 0
fi
alive=$(cat "$S/alive" 2>/dev/null || echo 0)
if [ $((woke - alive)) -gt 5 ]; then
  # Watcher wasn't polling around wake (killed, crashed): we can't know, and a second lock is the known bad outcome.
  echo "$(date '+%F %T') wake: watcher not running at wake, unknown; not locking" >> "$LOG"; exit 0
fi
/usr/bin/python3 -c 'import ctypes; ctypes.CDLL("/System/Library/PrivateFrameworks/login.framework/login").SACLockScreenImmediate()' >> "$LOG" 2>&1 &
echo "$(date '+%F %T') wake: watcher saw no lock since lid close -> one fallback lock" >> "$LOG"
HOOK
chmod 700 "$HOME/.sleep" "$HOME/.wakeup"
brew services restart sleepwatcher >/dev/null
echo "sleepwatcher running: $(pgrep -x sleepwatcher >/dev/null && echo yes || echo NO)"
echo "screenLock: $(sysadminctl -screenLock status 2>&1 | grep -o 'screenLock is [a-z]*')"
