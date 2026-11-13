#!/usr/bin/env bash
# Install the deck builder's Python env OUTSIDE ~/Documents (launchd processes can't read Documents):
# ~/.hermes/jarvis-deck/.venv (python-pptx, pypdfium2, Pillow, python-docx) + ~/.hermes/jarvis-deck/node (pptxgenjs, sharp,
# react-icons). LibreOffice renders slides for the visual check. Re-run any time; idempotent.
set -euo pipefail
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
DIR="$HERMES_HOME/jarvis-deck"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"
mkdir -p "$DIR" "$HERMES_HOME/jarvis-data/decks" "$HOME/Downloads/Jarvis"
[ -x "$DIR/.venv/bin/python" ] || uv venv -q -p 3.12 "$DIR/.venv"
uv pip install -q --python "$DIR/.venv/bin/python" "python-pptx==1.0.2" "pypdfium2>=4,<6" "Pillow>=10,<12" "python-docx>=1.1,<2"
# node side: pptxgenjs + sharp (gradients, icons) + react-icons, in Jarvis's own node folder (Hermes ships node)
export PATH="$HERMES_HOME/tools/node-26.7.0-darwin-arm64/bin:$PATH"
mkdir -p "$DIR/node" && cd "$DIR/node" && ([ -f package.json ] || npm init -y >/dev/null) && npm install --silent pptxgenjs sharp react-icons react react-dom
[ -d "/Applications/LibreOffice.app" ] || echo "LibreOffice missing (slide rendering for QA): brew install --cask libreoffice"
"$DIR/.venv/bin/python" -c "import pptx; print('deck builder ready: python-pptx', pptx.__version__, '->', '$DIR/.venv')"
