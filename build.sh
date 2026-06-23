#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  MedSearch — Build Script (macOS / Linux)
#  Creates a virtualenv automatically to avoid "externally managed" errors.
# ─────────────────────────────────────────────────────────────────────────────
set -e

echo ""
echo "  🔬  MedSearch Build Script (macOS / Linux)"
echo "  ─────────────────────────────────────────────"

# 1. Check Python
if ! command -v python3 &>/dev/null; then
  echo "  ✗  Python 3 not found. Install it from python.org"
  exit 1
fi
PY=$(python3 --version)
echo "  ✓  Found $PY"

# 2. Create virtualenv if it doesn't exist yet
if [ ! -d ".venv" ]; then
  echo "  →  Creating virtual environment (.venv)…"
  python3 -m venv .venv
  echo "  ✓  Virtual environment created"
else
  echo "  ✓  Virtual environment already exists"
fi

# 3. Activate it
source .venv/bin/activate
echo "  ✓  Virtual environment activated"

# 4. Install PyInstaller inside the venv (no system interference)
echo "  →  Installing PyInstaller…"
pip install --upgrade --quiet pyinstaller
echo "  ✓  PyInstaller ready"

# 5. Build
echo "  →  Building binary…"
python3 -m PyInstaller \
  --onefile \
  --name medsearch \
  --console \
  --clean \
  medsearch.py

# 6. Result
DIST="dist/medsearch"
if [ -f "$DIST" ]; then
  chmod +x "$DIST"
  echo ""
  echo "  ✓  Build complete!"
  echo "  ✓  Binary: $(pwd)/$DIST"
  echo ""
  echo "  To run immediately:"
  echo "    ./dist/medsearch"
  echo ""
  echo "  To install system-wide (optional):"
  echo "    sudo cp dist/medsearch /usr/local/bin/medsearch"
  echo "    medsearch   # from any terminal, anywhere"
  echo ""
else
  echo "  ✗  Build failed. Check output above."
  exit 1
fi

# 7. Deactivate venv (clean exit)
deactivate
