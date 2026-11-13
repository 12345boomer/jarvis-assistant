# Jarvis

A personal AI assistant that runs 24/7 on a Mac. It knows who you are, works across your Google Workspace, talks over Telegram, voice and phone (with a British butler voice), can call you, and asks permission before doing anything that matters.

It isn't a trained model. It's built from existing pieces: [Hermes Agent](https://hermes-agent.nousresearch.com/docs/) as the harness, Claude as the brain, plus a permission guard, an audit log, a HUD and a set of skills written for this project.

Built by Ben Eydlin. This is the public copy of my own setup. Personal details (USER.md, contacts, accounts, phone numbers, IDs) are replaced with templates and placeholders. Fill them in with your own.

## What it does
- **Chat on Telegram**, with approval buttons for anything risky.
- **Phone and voice.** You can call Jarvis, and Jarvis calls you: a weekday 9:00 brief call (school deadlines, email that needs you, your day), urgent-decision calls, and a backup call when a Claude Code session has been waiting on you for 30 minutes. Built on ElevenLabs agents + Twilio, with a fast voice bridge so the first word comes in under a second.
- **Google Workspace**: read, search and summarize Gmail, Calendar, Drive, Docs, Sheets and Contacts across multiple accounts. Replies come from the account the email arrived on.
- **Iron Man-style HUD**: a full-screen dashboard with weather, your timeline, VIP mail, tasks, markets and headlines. Talk to it by clicking the core or saying "Hey Jarvis" (on-device wake word, no audio leaves the Mac).
- **Mac control**: wake the screen, open apps and sites, text people through Messages, and drive apps with computer use (asks before any irreversible click).
- **Real work from a call**: "make the deck on finance red flags" runs as a background job and produces a designed .pptx (charts, icons, quiz, speaker notes) in `~/Downloads/Jarvis/`.
- **School**: due dates from your LMS calendar feed, plus study guides and assignment drafts through a signed-in browser (the bundled skill targets Blackboard Learn Ultra).
- **Invoices, meeting prep, inbox watch, tasks**, each as a skill in `skills/`.

## Architecture
```
Telegram / Slack ──┐                               ┌── Google Workspace (Gmail, Calendar, Drive, Docs, Sheets)
Phone (Twilio) ─ ElevenAgents ─ Custom LLM ─┐      ├── browser / web / file toolsets (Hermes built-in)
HUD voice button ─ ElevenAgents ────────────┤      ├── Honcho (long-term memory)
                                            ▼      │
                    Hermes gateway (launchd, 24/7) ─┤
                      ├─ SOUL.md + USER.md (identity)
                      ├─ skills/*/SKILL.md
                      ├─ cron jobs (brief call, inbox watch, meeting prep)
                      └─ jarvis_guard plugin: permission tiers → audit.db
                                            │
                    Dashboard / HUD (FastAPI) ◄──┘  feed, approvals, audit log, voice
```

| Piece | What |
|---|---|
| Harness | Hermes Agent (Nous Research, MIT): gateway, skills, MCP, approval hooks, cron, launchd |
| Brain | Claude via the Anthropic API (Sonnet by default, Haiku for fast/cheap work). Swappable in `config/hermes.yaml` |
| Memory | Honcho Cloud |
| Voice/phone | ElevenLabs agents + Twilio, reached through a Tailscale Funnel URL |
| Dashboard | FastAPI + one static HTML page, no build step |

## Repo layout
```
USER.md              who you are (template, always loaded)
SOUL.md              Jarvis persona + hard rules
config/hermes.yaml   Hermes config (model, channels, approvals) → linked to ~/.hermes/config.yaml
config/jarvis.yaml   quiet hours, VIPs, urgent rules, mail accounts, brief call, HUD, spend cap
config/invoice.json  invoice settings per business
skills/              one folder per skill, each with SKILL.md → linked into ~/.hermes/skills/
plugins/jarvis_guard permission tiers + audit log (Hermes plugin)
jarvis/              Python package (audit, approvals, calls, brief, voice bridge, Mac control, spend)
dashboard/           FastAPI app + HUD page
scripts/             install, link, doctor, watchdog, cron stubs
tests/               pytest
.env                 secrets, gitignored. Template: .env.example
```

## Setup
macOS only (it uses launchd, AppleScript/JXA and Messages).
1. `cp .env.example .env` and fill in the keys. The file is grouped by phase and every line says where the key comes from.
2. Fill in `USER.md`, `config/jarvis.yaml` (VIPs, mail accounts, location) and `config/invoice.json` with your own details.
3. `make install` installs Hermes Agent, uv and the Python package.
4. `make link` symlinks config, skills, USER.md, SOUL.md and .env into `~/.hermes/`.
5. `make doctor` checks keys, Hermes, the gateway and launchd.
6. `make gateway-install` registers the Telegram gateway with launchd (starts on login, restarts on crash).

Later pieces each have a make target: `make google-auth` (Google OAuth), `make bridge`, `make voice-agent` and `make twilio-number` (voice and phone), `make dashboard-install` and `make hud` (HUD), `make wake-install` (wake word), `make browser` (the signed-in Jarvis Browser), `make brief-call-install`, `make cc-backup-install`. Run `make help` for the full list.

Day to day: `make status`, `make logs`, `make restart`, `make test`, `make audit`.

## Permission system
- **Tier 1, just do it:** read, search, summarize, draft, look things up.
- **Tier 2, ask first:** send email or messages, create or change calendar events, send invoices, share files, post, call anyone but you. Approve with a Telegram button, on the dashboard, or with a spoken yes. No answer in 5 minutes = no.
- **Tier 3, never:** move or pay money, enter passwords or financial details, permanently delete. Jarvis prepares it and you do it.
- Tiers live in `config/jarvis.yaml` and are enforced in code by `plugins/jarvis_guard`. Hermes's own deny-list is a second layer.
- Every tool call is logged to the audit DB (what, when, why, tier, decision, approver).
- Quiet hours (23:00 to 07:00 by default): nothing pings or calls you unless it matches an `urgent` rule.

## Security notes
- Secrets only in `.env` (mode 600, gitignored) and `~/.hermes/` token files. Never hardcoded, logged, or sent to the model.
- Email, web and document content is wrapped as untrusted data before the model sees it (`jarvis/fence.py`). Instruction-like text inside it is flagged and logged, never followed.
- Daily model spend cap in `config/jarvis.yaml` (alert at 80%, read-only at 100%), plus the caps you set in each provider's dashboard.
- Google access uses the minimum scopes per feature. Drive writes are limited to files Jarvis created (`drive.file`).

## License
No license yet, so all rights are reserved by default. Open an issue if you want to reuse it.
