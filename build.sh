#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  MedSearch — Standalone Build (macOS / Linux)
#  Bundles the app + its templates into a single executable using PyInstaller,
#  so colleagues can run MedSearch without installing Python.
#
#  On macOS, prefer  make_app.sh  for a double-clickable MedSearch.app.
#  Use this script for a portable single-file binary (or for Linux).
# ─────────────────────────────────────────────────────────────────────────────
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "  🔬  MedSearch — Standalone Build (macOS / Linux)"
echo "  ─────────────────────────────────────────────"

# 1. Check Python
if ! command -v python3 &>/dev/null; then
  echo "  ✗  Python 3 not found. Install it from python.org"
  exit 1
fi
echo "  ✓  Found $(python3 --version)"

# 2. Create / activate a virtualenv (keeps the build isolated)
if [ ! -d ".venv" ]; then
  echo "  →  Creating virtual environment (.venv)…"
  python3 -m venv .venv
fi
source .venv/bin/activate
echo "  ✓  Virtual environment active"

# 3. Install the app's dependencies + PyInstaller
echo "  →  Installing dependencies…"
pip install --upgrade --quiet pip
pip install --quiet -r requirements.txt
pip install --upgrade --quiet pyinstaller
echo "  ✓  Dependencies ready"

# 4. Build. The templates/ folder MUST be bundled or the UI won't load.
#    --windowed keeps it GUI-only (no console window).
echo "  →  Building MedSearch…"
EXTRA=""
[ -f "VERSION" ] && EXTRA="$EXTRA --add-data VERSION:."
[ -f "icon.svg" ] && EXTRA="$EXTRA --add-data icon.svg:."

python3 -m PyInstaller \
  --onefile \
  --windowed \
  --name MedSearch \
  --add-data "templates:templates" \
  $EXTRA \
  --clean \
  app.py

# 5. Result
DIST="dist/MedSearch"
if [ -e "$DIST" ] || [ -e "$DIST.app" ]; then
  echo ""
  echo "  ✓  Build complete!"
  echo "  ✓  Output is in: $(pwd)/dist/"
  echo ""
  echo "  Share the contents of the dist/ folder with colleagues."
  echo ""
else
  echo "  ✗  Build failed. Check the output above."
  exit 1
fi

deactivate
