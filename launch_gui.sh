#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  MedSearch v4.0 — GUI Launcher
#  Run this to start the web interface. Opens automatically in your browser.
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Create venv if needed
if [ ! -d ".venv" ]; then
  echo "  Setting up environment (first run only)…"
  python3 -m venv .venv
fi

source .venv/bin/activate

# Install flask if needed
python3 -c "import flask" 2>/dev/null || pip install --quiet flask

echo ""
echo "  🔬  MedSearch v4.0"
echo "  Starting… opening http://localhost:5050"
echo "  Press Ctrl+C to stop."
echo ""

python3 app.py
