SHELL := /bin/bash
export PATH := $(HOME)/.local/bin:/opt/homebrew/bin:$(PATH)

.PHONY: help install link doctor test audit spend status logs start stop restart gateway-install watchdog nosleep

help:
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

install: ## Install Hermes Agent (if missing), uv, and the jarvis package
	@command -v brew >/dev/null || { echo "Install Homebrew first: https://brew.sh"; exit 1; }
	@command -v uv >/dev/null || brew install uv
	@command -v hermes >/dev/null || (curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --non-interactive --skip-computer-use)
	uv sync
	@[ -f .env ] || { cp .env.example .env && chmod 600 .env && echo "Created .env — fill in your keys."; }

link: ## Symlink config, .env, plugin, skills into ~/.hermes and build SOUL.md
	bash scripts/link.sh

doctor: ## Check keys, links, Hermes, plugin, gateway, launchd
	bash scripts/doctor.sh

test: ## Run unit tests
	uv run pytest -q

audit: ## Show the last 50 audit log rows
	uv run jarvis audit

spend: ## Today's model spend vs cap
	uv run jarvis spend

status: ## Gateway status + last log lines
	hermes gateway status || true
	@tail -n 20 ~/.hermes/logs/gateway.log 2>/dev/null || true

logs: ## Follow gateway logs
	tail -f ~/.hermes/logs/gateway.log

start: ## Start the gateway service
	hermes gateway start

stop: ## Stop the gateway service
	hermes gateway stop

restart: ## Restart the gateway (after config/skill changes)
	hermes gateway restart

gateway-install: ## Register the gateway with launchd (auto-start on login, restart on crash) + watchdog
	hermes gateway install
	bash scripts/install_watchdog.sh

watchdog: ## (Re)register only the 5-minute watchdog
	bash scripts/install_watchdog.sh

HERMES_PY := $(shell ls -d $(HOME)/.hermes/installs/*/environments/*/venv/bin/python 2>/dev/null | head -1)

ACCOUNT ?= personal
HINT ?=
GTOKEN := $(HOME)/.hermes/google_token$(if $(filter personal,$(ACCOUNT)),,_$(ACCOUNT)).json

google-auth: ## Print the Google sign-in URL. Second account: make google-auth ACCOUNT=dailybuilt HINT=you@yourbusiness.com
	$(HERMES_PY) skills/google/scripts/setup.py --auth-url --account $(ACCOUNT) $(if $(HINT),--login-hint $(HINT),)

google-code: ## Finish Google OAuth: make google-code [ACCOUNT=dailybuilt] URL='http://localhost:1/?...'
	$(HERMES_PY) skills/google/scripts/setup.py --auth-code '$(URL)' --account $(ACCOUNT) && chmod 600 $(GTOKEN)

google-check: ## Verify a Google token with a live API call: make google-check [ACCOUNT=dailybuilt]
	$(HERMES_PY) skills/google/scripts/setup.py --check-live --account $(ACCOUNT)
	skills/google/scripts/gapi --account $(ACCOUNT) gmail search 'in:inbox' --max 1 | head -c 300; echo

TS := $(shell command -v tailscale 2>/dev/null || echo /Applications/Tailscale.app/Contents/MacOS/Tailscale)

funnel: ## Expose the Hermes API server (8642) publicly over HTTPS via Tailscale Funnel (persists across reboots)
	"$(TS)" funnel --bg 8642
	@"$(TS)" funnel status

funnel-status: ## Show the public Funnel URL
	@"$(TS)" funnel status

funnel-off: ## Stop exposing 8642
	"$(TS)" funnel --bg off 8642 || "$(TS)" funnel reset

voice-agent: ## Create/update the ElevenLabs agent from config/voice-agent-prompt.md (needs JARVIS_PUBLIC_URL)
	python3 scripts/voice_setup.py agent

twilio-number: ## Claim the Twilio trial number -> TWILIO_FROM_NUMBER
	python3 scripts/voice_setup.py twilio-number

twilio-verify: ## Twilio calls BEN_PHONE_NUMBER with a code (trial accounts can only call verified numbers)
	python3 scripts/voice_setup.py twilio-verify

phone-import: ## Import the Twilio number into ElevenLabs and attach the agent
	python3 scripts/voice_setup.py phone-import

voice-status: ## Show which Phase 5 values are set (masked)
	python3 scripts/voice_setup.py status

bridge: ## Install/update the voice bridge (fast Claude path for calls) as a launchd service on 8642
	bash scripts/install_bridge.sh

bridge-logs: ## Follow the voice bridge log
	tail -f ~/.hermes/logs/bridge.log

bridge-restart: ## Restart the voice bridge service
	launchctl kickstart -k "gui/$$(id -u)/ai.jarvis.bridge"

voice-test: ## Call the API server the way ElevenLabs will (model jarvis-voice, streaming)
	@KEY=$$(grep -E '^API_SERVER_KEY=' .env | cut -d= -f2-); curl -s -N -m 60 http://127.0.0.1:8642/v1/chat/completions -H "Authorization: Bearer $$KEY" -H "Content-Type: application/json" -d '{"model":"jarvis-voice","stream":true,"messages":[{"role":"user","content":"Say hi in five words."}]}' | head -c 400; echo

dashboard: ## Run the HUD in the foreground on http://127.0.0.1:8650 (dev)
	uv run uvicorn dashboard.app:app --host 127.0.0.1 --port 8650 --reload

dashboard-install: ## Install/update the HUD as a launchd service on 127.0.0.1:8650
	bash scripts/install_dashboard.sh

hud: ## Open the HUD as a full-screen app window (Chrome app mode)
	bash scripts/jarvis-hud.sh

hud-wake: ## Open the HUD and auto-start a voice session (what the wake word does)
	bash scripts/jarvis-hud.sh --wake

dashboard-logs: ## Follow the HUD log
	tail -f ~/.hermes/logs/dashboard.log

wake-install: ## Install/update the "Hey Jarvis" listener as a launchd service (asks for mic permission once)
	bash scripts/install_wake.sh

wake-status: ## Listener heartbeat: mic level, paused?, detections
	@launchctl list | grep -q ai.jarvis.wake && echo "launchd: registered" || echo "launchd: NOT registered"
	@cat ~/.hermes/jarvis-state/wake_heartbeat.json 2>/dev/null | python3 -c "import sys,json,time; d=json.load(sys.stdin); print(f\"heartbeat {time.time()-d['ts']:.0f}s ago · mic rms {d.get('rms')} · paused {d.get('paused')} · detections {d.get('detections')} · silent {d.get('silent')}\")" || echo "no heartbeat yet"

wake-logs: ## Follow the listener log
	tail -f ~/.hermes/logs/wake.log

wake-stop: ## Stop the listener
	launchctl bootout "gui/$$(id -u)/ai.jarvis.wake" 2>/dev/null || true; pkill -f "jarvis-wake/wake_listener.py" || true

lock-install: ## Install the lid-close lock policy (sleepwatcher hooks; run sysadminctl -screenLock off yourself first)
	@bash scripts/install_lock.sh

deck-install: ## Install the deck builder env (python-pptx) under ~/.hermes/jarvis-deck
	bash scripts/install_deck.sh

deck-example: ## Build the house-style template deck into ~/Downloads/Jarvis/template-demo.pptx, render-check it, open it
	skills/deck/scripts/deckjs skills/deck/scripts/deck_template.js && ~/.hermes/jarvis-deck/.venv/bin/python skills/deck/scripts/deck_check.py ~/Downloads/Jarvis/template-demo.pptx | head -20 && open ~/Downloads/Jarvis/template-demo.pptx

brief-dry: ## Print today's brief-call text without calling (no ElevenLabs minutes)
	uv run jarvis brief-call --dry-run

brief-call: ## Call Ben with the brief right now (test; uses ElevenLabs minutes)
	uv run jarvis brief-call --now

brief-call-install: ## Schedule the weekday brief call from config/jarvis.yaml brief_call (no-agent Hermes cron)
	bash scripts/link.sh >/dev/null
	bash scripts/install_routine_job.sh brief-call "$$(uv run python -c 'from jarvis.brief_call import cron_expr; from jarvis.config import load_config; print(cron_expr(load_config()))')" jarvis-brief-call.py

cc-backup-install: ## Claude Code backup call: merge hooks into ~/.claude/settings.json + 5-minute check (no-agent cron)
	bash scripts/link.sh >/dev/null
	/usr/bin/python3 scripts/install_cc_hooks.py
	bash scripts/install_routine_job.sh cc-backup "*/$$(uv run python -c 'from jarvis.cc_backup import cc_cfg; from jarvis.config import load_config; print(cc_cfg(load_config()).get("check_every_minutes", 5))') * * * *" jarvis-cc-backup.py

cc-backup-status: ## What Claude Code prompts are waiting, and today's backup-call count
	uv run jarvis cc-backup status

cc-backup-remove: ## Take the backup-call hooks out of ~/.claude/settings.json (cron job stays; pause it with hermes cron pause)
	/usr/bin/python3 scripts/install_cc_hooks.py --remove

browser: ## Install/start the hidden Jarvis Browser (headless Chrome, DevTools on 9333) as a launchd service
	bash scripts/install_browser.sh

huskyct: ## Link HuskyCT: shows the Jarvis Browser on the login page (one time), then: make huskyct-setup
	uv run jarvis school login

huskyct-setup: ## After signing in: store the feed URL in .env, hide the browser again, show what's due
	uv run jarvis school setup && uv run jarvis school due --days 14

nosleep: ## Keep the Mac awake while plugged in (asks for your password)
	sudo pmset -c sleep 0 disksleep 0
	@echo "Mac will not sleep while on power. Lid closed = still sleeps unless an external display is attached."
