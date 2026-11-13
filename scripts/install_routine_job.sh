#!/usr/bin/env bash
# Create or update a no-agent Hermes cron job (the script is the job; no model call).
# Usage: install_routine_job.sh <name> <schedule> <script under ~/.hermes/scripts/>
# Used by `make brief-call-install` and `make cc-backup-install`. Safe to re-run: an existing job with the same
# name gets its schedule/script updated instead of a duplicate.
set -euo pipefail
export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"
NAME="$1"; SCHEDULE="$2"; SCRIPT="$3"
[ -f "$HOME/.hermes/scripts/$SCRIPT" ] || { echo "missing ~/.hermes/scripts/$SCRIPT (run make link)"; exit 1; }
ID=$(hermes cron list 2>/dev/null | awk -v n="$NAME" '/^ +[0-9a-f]{12} \[/{id=$1} $1=="Name:" && $2==n {print id; exit}')
if [ -n "$ID" ]; then
  hermes cron edit "$ID" --schedule "$SCHEDULE" --script "$SCRIPT" --no-agent --deliver telegram >/dev/null
  hermes cron resume "$ID" >/dev/null 2>&1 || true
  echo "updated cron job $NAME ($ID): $SCHEDULE -> $SCRIPT"
else
  hermes cron create "$SCHEDULE" --name "$NAME" --script "$SCRIPT" --no-agent --deliver telegram >/dev/null
  echo "created cron job $NAME: $SCHEDULE -> $SCRIPT"
fi
hermes cron list 2>/dev/null | awk -v n="$NAME" '/^ +[0-9a-f]{12} \[/{blk=""} {blk=blk"\n"$0} $1=="Name:" && $2==n {p=1} p && /Next run:/ {print "  " $0; exit}'
