#!/usr/bin/env bash
# Hermes treats every `sk-ant-*` key that isn't `sk-ant-api*` as a Claude Code OAuth token and then sends requests with
# Claude Code's identity (Bearer auth, claude-code beta, claude-code user-agent). Ben's Console key is `sk-ant-usr-…`
# (the Console's newer user-key format), so Hermes misfiled it: those requests bill as "Claude Code", which the Max
# plan's promotional API credits don't cover -> "credit balance is too low" while plain API calls work fine.
# This one-line patch tells Hermes that `sk-ant-usr` is a Console key. Idempotent; `make link` re-applies it after
# `hermes update`. Upstream: agent/anthropic_credentials.py::_is_oauth_token.
set -euo pipefail
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
F="$HERMES_HOME/hermes-agent/agent/anthropic_credentials.py"
[ -f "$F" ] || { echo "patch_hermes: $F not found"; exit 0; }
if grep -q 'startswith(("sk-ant-api", "sk-ant-usr"))' "$F"; then
  echo "  hermes   sk-ant-usr patch already applied"
elif grep -q 'key.startswith("sk-ant-api")' "$F"; then
  sed -i '' 's/if not key or key.startswith("sk-ant-api"):/if not key or key.startswith(("sk-ant-api", "sk-ant-usr")):  # jarvis: Console user keys are API keys (scripts\/patch_hermes.sh)/' "$F"
  find "$HERMES_HOME/hermes-agent/agent" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
  echo "  hermes   applied sk-ant-usr patch to $F"
else
  echo "  hermes   WARNING: _is_oauth_token looks different upstream; check $F by hand"
fi
