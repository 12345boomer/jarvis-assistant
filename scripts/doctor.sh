#!/usr/bin/env bash
# Health check: keys present, links intact, Hermes installed, plugin loaded, gateway alive.
# Prints PASS/WARN/FAIL per check and exits non-zero on any FAIL.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
export PATH="$HOME/.local/bin:$PATH"
fails=0

pass() { printf "  PASS  %s\n" "$1"; }
warn() { printf "  WARN  %s\n" "$1"; }
fail() { printf "  FAIL  %s\n" "$1"; fails=$((fails+1)); }

envval() { grep -E "^$1=" "$REPO/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'"; }

echo "Jarvis doctor"
echo "-- files"
[ -f "$REPO/.env" ] && pass ".env exists" || fail ".env missing (cp .env.example .env)"
[ "$(stat -f %Lp "$REPO/.env" 2>/dev/null)" = "600" ] && pass ".env mode 600" || warn ".env should be chmod 600"
for v in ANTHROPIC_API_KEY TELEGRAM_BOT_TOKEN TELEGRAM_ALLOWED_USERS; do
  [ -n "$(envval $v)" ] && pass "$v set" || fail "$v empty in .env"
done
case "$(envval ANTHROPIC_API_KEY)" in sk-ant-*|"") ;; *) warn "ANTHROPIC_API_KEY doesn't start with sk-ant-";; esac
[ -n "$(envval HONCHO_API_KEY)" ] && pass "HONCHO_API_KEY set" || warn "HONCHO_API_KEY empty (Phase 2 memory runs on built-in memory only)"
[ -f "$HERMES_HOME/google_client_secret.json" ] && pass "Google OAuth client stored" || warn "Google OAuth client missing (Phase 3: ~/.hermes/google_client_secret.json)"
[ -f "$HERMES_HOME/google_token.json" ] && pass "Google token present" || warn "Google not authorized yet (Phase 3 OAuth flow)"
case "$(envval TELEGRAM_ALLOWED_USERS)" in ''|*[!0-9,]*) [ -n "$(envval TELEGRAM_ALLOWED_USERS)" ] && warn "TELEGRAM_ALLOWED_USERS should be numeric IDs";; esac

echo "-- links"
for pair in "config.yaml:$REPO/config/hermes.yaml" ".env:$REPO/.env" "honcho.json:$REPO/config/honcho.json" "plugins/jarvis-guard:$REPO/plugins/jarvis_guard"; do
  dst="$HERMES_HOME/${pair%%:*}"; src="${pair#*:}"
  [ -L "$dst" ] && [ "$(readlink "$dst")" = "$src" ] && pass "$dst linked" || fail "$dst not linked to repo (run make link)"
done
if [ -f "$HERMES_HOME/SOUL.md" ]; then
  if [ "$REPO/SOUL.md" -nt "$HERMES_HOME/SOUL.md" ] || [ "$REPO/USER.md" -nt "$HERMES_HOME/SOUL.md" ]; then
    warn "SOUL.md is stale (run make link)"; else pass "SOUL.md built"; fi
else fail "SOUL.md not built"; fi

echo "-- hermes"
if command -v hermes >/dev/null; then pass "hermes on PATH ($(hermes --version 2>/dev/null | head -1))"; else fail "hermes not installed"; fi
if hermes plugins list --plain --no-bundled 2>/dev/null | grep -q "jarvis-guard"; then pass "jarvis-guard plugin visible"; else fail "jarvis-guard plugin not found by hermes"; fi
if hermes config get plugins.enabled 2>/dev/null | grep -q jarvis-guard; then pass "jarvis-guard enabled"; else fail "jarvis-guard not enabled in config"; fi
for d in productivity/google-workspace email/himalaya; do
  [ -d "$HERMES_HOME/skills/$d" ] && warn "bundled skill $d is back (hermes update re-seeded it); run make link" || true
done

echo "-- gateway"
if hermes gateway status 2>/dev/null | grep -qiE "running|active|pid"; then pass "gateway running"; else warn "gateway not running (make gateway-install or make start)"; fi
if grep -q "ai.hermes.gateway" <<<"$(launchctl list 2>/dev/null)"; then pass "launchd job registered"; else warn "launchd job not registered (make gateway-install)"; fi
[ -f "$HERMES_HOME/.gateway-launchd-unsupported" ] && warn "Hermes gave up on launchd; gateway runs unsupervised (won't survive reboot)"
if [ -f "$HOME/Library/LaunchAgents/ai.jarvis.watchdog.plist" ]; then
  code=$(launchctl print "gui/$(id -u)/ai.jarvis.watchdog" 2>/dev/null | grep "last exit code" | grep -oE '[0-9]+|never' | head -1)
  case "$code" in 0|never|"") pass "watchdog registered";; *) warn "watchdog registered but last run exited $code (see ~/.hermes/logs/watchdog.log)";; esac
else warn "watchdog not registered (make watchdog)"; fi

echo "-- voice (Phase 5)"
if [ "$(envval API_SERVER_ENABLED)" = "true" ]; then
  HP=$(envval API_SERVER_PORT); HP=${HP:-8644}
  if curl -s -m 5 -o /dev/null -w "%{http_code}" "http://127.0.0.1:$HP/health" 2>/dev/null | grep -qE "^(200|401)$"; then pass "Hermes API server answering on $HP"; else warn "Hermes API server not answering on $HP (gateway restarted?)"; fi
  if curl -s -m 5 -o /dev/null -w "%{http_code}" http://127.0.0.1:8642/health 2>/dev/null | grep -qE "^200$"; then pass "voice bridge answering on 8642"; else warn "voice bridge not answering on 8642 (make bridge)"; fi
else warn "API server disabled (API_SERVER_ENABLED in .env)"; fi
if command -v tailscale >/dev/null 2>&1 || [ -x "/Applications/Tailscale.app/Contents/MacOS/Tailscale" ]; then
  TS=$(command -v tailscale || echo "/Applications/Tailscale.app/Contents/MacOS/Tailscale")
  if "$TS" funnel status 2>/dev/null | grep -q "8642"; then pass "Tailscale Funnel exposing 8642"; else warn "Tailscale Funnel not exposing 8642 (make funnel)"; fi
else warn "Tailscale not installed (needed for the voice agent to reach Jarvis)"; fi
for v in ELEVENLABS_API_KEY ELEVENLABS_AGENT_ID ELEVENLABS_AGENT_PHONE_NUMBER_ID BEN_PHONE_NUMBER; do
  [ -n "$(envval $v)" ] && pass "$v set" || warn "$v empty (Phase 5)"
done

echo "-- routines (Phase 6)"
n=$(hermes cron list 2>/dev/null | grep -cE "^\s+[0-9a-f]{12} \[active\]")
[ "${n:-0}" -ge 2 ] && pass "$n cron jobs active (inbox-watch, meeting-prep; morning-brief is on demand and paused)" || warn "only ${n:-0} active cron jobs (expected inbox-watch, meeting-prep)"
for f in jarvis-inbox-gate.py jarvis-meetings-gate.py; do [ -f "$HERMES_HOME/scripts/$f" ] && pass "gate $f in place" || warn "gate $f missing (make link)"; done

echo "-- brief call + Claude Code backup call"
CRONLIST=$(hermes cron list 2>/dev/null)
job_block() { echo "$CRONLIST" | awk -v n="$1" '/^ +[0-9a-f]{12} \[/{blk=$0; next} {blk=blk"\n"$0} $1=="Name:" && $2==n {found=1} found && /^$/ {print blk; exit} END {if (found) print blk}' | head -12; }
BJ=$(job_block brief-call)
WANT=$("$REPO/.venv/bin/python" -c 'from jarvis.brief_call import cron_expr; from jarvis.config import load_config; print(cron_expr(load_config()))' 2>/dev/null)
if [ -z "$BJ" ]; then fail "brief-call schedule not installed (make brief-call-install)"
elif ! echo "$BJ" | grep -q "\[active\]"; then fail "brief-call job is paused (hermes cron resume <id>)"
elif ! echo "$BJ" | grep "Schedule:" | sed 's/^ *Schedule: *//' | grep -qxF "$WANT"; then warn "brief-call schedule differs from config/jarvis.yaml ($WANT): make brief-call-install"
else pass "brief-call scheduled ($WANT), $(echo "$BJ" | grep -o 'Next run: *[^ ]*' | sed 's/  */ /')"; fi
[ -f "$HERMES_HOME/scripts/jarvis-brief-call.py" ] && pass "gate jarvis-brief-call.py in place" || fail "jarvis-brief-call.py missing (make link)"
CJ=$(job_block cc-backup)
if [ -n "$CJ" ] && echo "$CJ" | grep -q "\[active\]"; then pass "cc-backup check scheduled (every 5 min)"; else fail "cc-backup check not scheduled (make cc-backup-install)"; fi
if /usr/bin/python3 "$REPO/scripts/install_cc_hooks.py" --check >/dev/null 2>&1; then pass "Claude Code hooks in ~/.claude/settings.json"; else fail "Claude Code backup hooks missing (make cc-backup-install)"; fi
cmp -s "$REPO/scripts/cc_hook.py" "$HERMES_HOME/jarvis-cc/cc_hook.py" && pass "cc_hook.py copy current" || warn "cc_hook.py copy stale or missing (make link)"
[ -f "$HERMES_HOME/google_token_dailybuilt.json" ] && pass "DailyBuilt Gmail connected" || warn "DailyBuilt Gmail not connected (make google-auth ACCOUNT=dailybuilt HINT=you@yourbusiness.com)"

echo "-- dashboard (Phase 7)"
if curl -s -m 5 -o /dev/null -w "%{http_code}" http://127.0.0.1:8650/health 2>/dev/null | grep -q "^200$"; then pass "HUD answering on 8650"; else warn "HUD not answering on 8650 (make dashboard-install)"; fi
grep -q "ai.jarvis.dashboard" <<<"$(launchctl list 2>/dev/null)" && pass "dashboard launchd job registered" || warn "dashboard launchd job not registered"
[ "$(grep -c '^DASHBOARD_SECRET=' "$REPO/.env" 2>/dev/null)" = "1" ] && pass "DASHBOARD_SECRET set once" || warn "DASHBOARD_SECRET missing or duplicated in .env"

echo "-- wake word (Phase 8c)"
if grep -q "ai.jarvis.wake" <<<"$(launchctl list 2>/dev/null)"; then
  pass "wake listener registered"
  HB="$HERMES_HOME/jarvis-state/wake_heartbeat.json"
  if [ -f "$HB" ]; then
    age=$(python3 -c "import json,time; print(int(time.time()-json.load(open('$HB'))['ts']))" 2>/dev/null || echo 999)
    silent=$(python3 -c "import json; print(json.load(open('$HB')).get('silent'))" 2>/dev/null)
    [ "${age:-999}" -lt 30 ] && pass "listener heartbeat ${age}s ago" || warn "listener heartbeat stale (${age}s); see ~/.hermes/logs/wake.log"
    [ "$silent" = "True" ] && warn "microphone delivers silence: grant Microphone to 'Jarvis Wake' in System Settings" || true
  else warn "no listener heartbeat yet"; fi
else warn "wake listener not registered (make wake-install)"; fi

echo "-- decks (Phase 9)"
if "$HOME/.hermes/jarvis-deck/.venv/bin/python" -c "import pptx" 2>/dev/null; then pass "deck builder env (python-pptx)"; else warn "deck builder env missing: make deck-install"; fi
[ -L "$HOME/.hermes/skills/jarvis-deck" ] && pass "skill jarvis-deck linked" || warn "skill jarvis-deck not linked: make link"
grep -q "jarvis-task" "$HOME/.hermes/config.yaml" 2>/dev/null && pass "api_server route jarvis-task (background jobs from calls)" || warn "jarvis-task route missing in config.yaml"
echo "-- HuskyCT + Jarvis Browser (Phase 10)"
if curl -s -m 2 http://127.0.0.1:9333/json/version >/dev/null 2>&1; then pass "Jarvis Browser answering on 9333"; else warn "Jarvis Browser not running (make browser)"; fi
[ -n "$(envval HUSKYCT_ICS_URL)" ] && pass "HUSKYCT_ICS_URL set" || warn "HUSKYCT_ICS_URL empty (log in to HuskyCT in the Jarvis Browser, then make huskyct-setup)"
[ -L "$HOME/.hermes/skills/jarvis-huskyct" ] && pass "skill jarvis-huskyct linked" || warn "skill jarvis-huskyct not linked (make link)"
grep -q '"sk-ant-api", "sk-ant-usr"' "$HOME/.hermes/hermes-agent/agent/anthropic_credentials.py" 2>/dev/null && pass "hermes sk-ant-usr key patch applied" || warn "hermes sk-ant-usr patch missing (make link): Console key would be sent as Claude Code and fail on promo credits"
echo "-- power"
if pmset -g 2>/dev/null | grep -qE "^\s*sleep\s+0"; then pass "no sleep on power"; else warn "Mac sleeps on power; Jarvis goes offline (make nosleep)"; fi

echo "Tailscale / Funnel (phone + HUD voice path)"
TS="/Applications/Tailscale.app/Contents/MacOS/Tailscale"
if [ -x "$TS" ]; then
  if "$TS" status --json 2>/dev/null | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("Self",{}).get("Online") else 1)' 2>/dev/null; then pass "tailscale online"; else fail "tailscale OFFLINE (voice calls get no reply; watchdog retries, or toggle Wi-Fi)"; fi
  PUB="$(envval JARVIS_PUBLIC_URL)"
  if [ -n "$PUB" ]; then
    H="${PUB#https://}"; H="${H%%/*}"; IP="$(dig +short @1.1.1.1 "$H" A 2>/dev/null | head -1)"
    if [ -n "$IP" ] && [ "$(curl -s -m 20 -o /dev/null -w '%{http_code}' --resolve "$H:443:$IP" "$PUB/health" 2>/dev/null)" = "200" ]; then pass "funnel answers from the internet"; else fail "funnel NOT reachable from the internet (ElevenLabs can't reach the bridge)"; fi
  fi
else warn "Tailscale app not installed"; fi
if /usr/local/bin/warp-cli status 2>/dev/null | grep -q "Connected"; then fail "Cloudflare WARP is connected: it blocks Tailscale's relays, so calls can't reach the bridge (warp-cli disconnect)"; else pass "Cloudflare WARP not connected"; fi

echo
[ $fails -eq 0 ] && echo "All required checks passed." || echo "$fails check(s) failed."
exit $fails
