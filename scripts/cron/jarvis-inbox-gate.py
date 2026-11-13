#!/usr/bin/env python3
"""Cron pre-run gate for the VIP inbox watch. Copied to ~/.hermes/scripts/ by `make link`.
Runs the real check inside the JARVIS venv (which has the jarvis package + PyYAML)."""
import os
import subprocess
import sys

REPO = os.environ.get("JARVIS_ROOT") or os.path.expanduser("~/Documents/JARVIS")
sys.exit(subprocess.call([os.path.join(REPO, ".venv", "bin", "jarvis"), "routine", "inbox"], env={**os.environ, "JARVIS_ROOT": REPO}))
