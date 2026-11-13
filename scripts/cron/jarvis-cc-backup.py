#!/usr/bin/env python3
"""No-agent cron job (every 5 min): call Ben when a Claude Code prompt has waited too long. Copied to ~/.hermes/scripts/
by `make link`. Prints nothing unless the Telegram fallback itself failed (then Hermes delivers stdout instead)."""
import os
import subprocess
import sys

REPO = os.environ.get("JARVIS_ROOT") or os.path.expanduser("~/Documents/JARVIS")
sys.exit(subprocess.call([os.path.join(REPO, ".venv", "bin", "jarvis"), "cc-backup", "check", "--cron"], env={**os.environ, "JARVIS_ROOT": REPO}))
