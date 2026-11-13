#!/usr/bin/env bash
# Every 5 minutes (via launchd): if the Hermes gateway isn't running, start it.
# Covers the macOS 26 case where launchd silently drops the gateway job.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"   # networksetup is in /usr/sbin
LOG="$HOME/.hermes/logs/watchdog.log"
mkdir -p "$(dirname "$LOG")"
# Healthy output looks like "Gateway is supervised by launchd (PID 12345)" or "running".
if ! hermes gateway status 2>/dev/null | grep -qiE "supervised by launchd \(PID|is running|active \(running\)"; then
  echo "$(date -Iseconds) gateway down; starting" >> "$LOG"
  hermes gateway start >> "$LOG" 2>&1 || launchctl kickstart -k "gui/$(id -u)/ai.hermes.gateway" >> "$LOG" 2>&1
fi

# ---- Tailscale / Funnel (the phone and HUD voice depend on it) ---------------------------------------------------
# After lid-close sleep cycles the Tailscale network extension can come back "offline" and never recover on its own
# (control server unreachable, "no route to host"). ElevenLabs then can't reach the voice bridge: calls answer with
# the greeting and never reply ("custom_llm 502 TLS handshake failed"). `tailscale up` alone rarely fixes it; a
# Wi-Fi off/on does. Only runs when Funnel is configured, and only touches Wi-Fi when Tailscale is actually down.
TS="/Applications/Tailscale.app/Contents/MacOS/Tailscale"
ts_online() { "$TS" status --json 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get("Self",{}).get("Online") else 1)' 2>/dev/null; }
if [ -x "$TS" ] && grep -qs "^JARVIS_PUBLIC_URL=https" "$HOME/.hermes/jarvis-bridge/.env" "$HOME/Documents/JARVIS/.env"; then
  if ! ts_online; then
    echo "$(date -Iseconds) tailscale offline; tailscale up" >> "$LOG"
    "$TS" up >> "$LOG" 2>&1; sleep 10
    if ! ts_online; then
      WIFI=$(networksetup -listallhardwareports 2>/dev/null | awk '/Wi-Fi/{getline; print $2}')
      if [ -n "$WIFI" ] && networksetup -getairportpower "$WIFI" 2>/dev/null | grep -q "On"; then
        echo "$(date -Iseconds) still offline; toggling Wi-Fi ($WIFI)" >> "$LOG"
        networksetup -setairportpower "$WIFI" off; sleep 4; networksetup -setairportpower "$WIFI" on; sleep 15
      fi
      ts_online && echo "$(date -Iseconds) tailscale back online" >> "$LOG" || echo "$(date -Iseconds) tailscale STILL offline (needs a look: tailscale status)" >> "$LOG"
    else
      echo "$(date -Iseconds) tailscale back online" >> "$LOG"
    fi
  fi
  # The node being online is not enough: after a recovery the Funnel ingress can keep failing TLS for minutes, and
  # once it needed `funnel off` + `funnel --bg` to come back. Probe the real internet path (public DNS, not MagicDNS).
  PUB=$(grep -hs "^JARVIS_PUBLIC_URL=" "$HOME/.hermes/jarvis-bridge/.env" "$HOME/Documents/JARVIS/.env" | head -1 | cut -d= -f2- | tr -d '"')
  if ts_online && [ -n "$PUB" ]; then
    H="${PUB#https://}"; H="${H%%/*}"; IP=$(dig +short @1.1.1.1 "$H" A 2>/dev/null | head -1)
    probe() { [ -n "$IP" ] && [ "$(curl -s -m 15 -o /dev/null -w '%{http_code}' --resolve "$H:443:$IP" "$PUB/health" 2>/dev/null)" = "200" ]; }
    if ! probe; then
      echo "$(date -Iseconds) funnel not reachable from the internet; re-applying" >> "$LOG"
      "$TS" funnel off >/dev/null 2>&1; sleep 2; "$TS" funnel --bg 8642 >> "$LOG" 2>&1
      for i in 1 2 3 4 5 6 7 8; do sleep 15; probe && break; done
      if probe; then echo "$(date -Iseconds) funnel reachable again" >> "$LOG"
      else
        echo "$(date -Iseconds) funnel STILL unreachable (ElevenLabs can't reach the bridge)" >> "$LOG"
        # Cloudflare WARP (full-tunnel VPN) blocks Tailscale's relays; name it instead of retrying silently. One text per hour.
        if /usr/local/bin/warp-cli status 2>/dev/null | grep -q "Connected"; then
          MARK="$HOME/.hermes/jarvis-state/warp-warned"; mkdir -p "$(dirname "$MARK")"
          if [ ! -f "$MARK" ] || [ -n "$(find "$MARK" -mmin +60 2>/dev/null)" ]; then
            touch "$MARK"; echo "$(date -Iseconds) WARP is connected; texting Ben" >> "$LOG"
            hermes send -t telegram -q "Jarvis phone line is blocked: Cloudflare WARP is connected on the Mac and it cuts off the funnel. Run: warp-cli disconnect (or toggle WARP off in the menu bar)." >> "$LOG" 2>&1 || true
          fi
        fi
      fi
    fi
  fi
fi
