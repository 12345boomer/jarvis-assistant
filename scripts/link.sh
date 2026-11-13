#!/usr/bin/env bash
# Wire this repo into Hermes's home (~/.hermes) with symlinks, so the repo is the
# single source of truth. Safe to re-run. Originals are backed up once.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
BACKUP="$HERMES_HOME/backups/jarvis-link-$(date +%Y%m%d-%H%M%S)"

link() {  # link <repo path> <hermes path>
  local src="$1" dst="$2"
  if [ -L "$dst" ] && [ "$(readlink "$dst")" = "$src" ]; then
    echo "  ok      $dst"
    return
  fi
  if [ -e "$dst" ] && [ ! -L "$dst" ]; then
    mkdir -p "$BACKUP"
    mv "$dst" "$BACKUP/$(basename "$dst")"
    echo "  backup  $dst -> $BACKUP/"
  fi
  rm -f "$dst"
  ln -s "$src" "$dst"
  echo "  linked  $dst -> $src"
}

echo "Linking repo into $HERMES_HOME"
mkdir -p "$HERMES_HOME/plugins" "$HERMES_HOME/skills" "$HERMES_HOME/memories"

link "$REPO/config/hermes.yaml" "$HERMES_HOME/config.yaml"
link "$REPO/.env"               "$HERMES_HOME/.env"
link "$REPO/config/honcho.json"  "$HERMES_HOME/honcho.json"
link "$REPO/plugins/jarvis_guard" "$HERMES_HOME/plugins/jarvis-guard"
echo "$REPO" > "$REPO/plugins/jarvis_guard/.repo_root"   # lets the plugin find the repo even when copied

# Each skill folder in skills/ becomes ~/.hermes/skills/jarvis-<name>
shopt -s nullglob
for d in "$REPO"/skills/*/; do
  name="$(basename "$d")"
  link "${d%/}" "$HERMES_HOME/skills/jarvis-$name"
done

# Bundled Hermes skills that overlap with ours confuse the agent (it picked the bundled
# google-workspace over jarvis-google). `hermes update` re-seeds them, so prune on every link.
for d in productivity/google-workspace email/himalaya; do
  if [ -d "$HERMES_HOME/skills/$d" ]; then rm -rf "$HERMES_HOME/skills/$d"; echo "  pruned  bundled skill $d"; fi
done

# One-time migration: audit DB moved from repo data/ to ~/.hermes/jarvis-data (launchd can't read ~/Documents).
if [ -f "$REPO/data/audit.db" ] && [ ! -f "$HERMES_HOME/jarvis-data/audit.db" ]; then
  mkdir -p "$HERMES_HOME/jarvis-data" && mv "$REPO/data/audit.db"* "$HERMES_HOME/jarvis-data/" && echo "  moved   audit.db -> $HERMES_HOME/jarvis-data/"
fi

# Cron pre-run gates must live under ~/.hermes/scripts/ (Hermes containment rule); copy, don't link.
mkdir -p "$HERMES_HOME/scripts"
for f in "$REPO"/scripts/cron/*.py; do
  cp "$f" "$HERMES_HOME/scripts/$(basename "$f")" && echo "  copied  $HERMES_HOME/scripts/$(basename "$f")"
done

# Claude Code hook (backup call): copied outside ~/Documents; ~/.claude/settings.json points here (make cc-backup-install).
mkdir -p "$HERMES_HOME/jarvis-cc"
cp "$REPO/scripts/cc_hook.py" "$HERMES_HOME/jarvis-cc/cc_hook.py" && echo "  copied  $HERMES_HOME/jarvis-cc/cc_hook.py"

# HUD launcher: copied so launchd jobs (wake listener) can run it (launchd can't read ~/Documents).
cp "$REPO/scripts/jarvis-hud.sh" "$HERMES_HOME/jarvis-hud.sh" && chmod +x "$HERMES_HOME/jarvis-hud.sh" && echo "  copied  $HERMES_HOME/jarvis-hud.sh"

# SOUL.md is generated (persona + USER.md), not linked, because Hermes loads one file.
"$REPO/scripts/build_soul.sh"

chmod 600 "$REPO/.env" 2>/dev/null || true
echo "Done."

# Hermes misfiles Console "sk-ant-usr" keys as OAuth tokens; re-apply the one-line fix after every hermes update.
bash "$REPO/scripts/patch_hermes.sh"
