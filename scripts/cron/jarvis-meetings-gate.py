#!/usr/bin/env python3
"""Cron pre-run gate for meeting prep. Copied to ~/.hermes/scripts/ by `make link`."""
import os
import subprocess
import sys

REPO = os.environ.get("JARVIS_ROOT") or os.path.expanduser("~/Documents/JARVIS")
sys.exit(subprocess.call([os.path.join(REPO, ".venv", "bin", "jarvis"), "routine", "meetings"], env={**os.environ, "JARVIS_ROOT": REPO}))
