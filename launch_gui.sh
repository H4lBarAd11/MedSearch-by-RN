#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  MedSearch — Launcher (macOS / Linux)
#  Sets up dependencies on first run, then opens MedSearch in a desktop window.
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Create a virtual environment on first run (avoids "externally managed" errors)
if [ ! -d ".venv" ]; then
  echo "  Setting up environment (first run only)…"
  python3 -m venv .venv
fi

source .venv/bin/activate

# Install dependencies if anything is missing
if ! python3 -c "import flask, webview" 2>/dev/null; then
  echo "  Installing dependencies…"
  pip install --quiet -r requirements.txt
fi

echo ""
echo "  🔬  MedSearch"
echo "  Opening in a desktop window… (close the window to quit)"
echo ""

python3 app.py
