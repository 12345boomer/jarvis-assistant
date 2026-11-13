#!/usr/bin/env python3
"""No-agent cron job: the weekday brief call. Copied to ~/.hermes/scripts/ by `make link`.
Prints nothing unless the Telegram fallback itself failed (then Hermes delivers stdout to Telegram instead)."""
import os
import subprocess
import sys

REPO = os.environ.get("JARVIS_ROOT") or os.path.expanduser("~/Documents/JARVIS")
sys.exit(subprocess.call([os.path.join(REPO, ".venv", "bin", "jarvis"), "brief-call", "--cron"], env={**os.environ, "JARVIS_ROOT": REPO}))
