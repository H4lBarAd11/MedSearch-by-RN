#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  MedSearch — double-clickable launcher (source / git-clone distribution)
#
#  For colleagues who CLONED the repo (so the in-app "Update" button works via
#  git pull). On first run it sets up a local virtual environment and installs
#  the dependencies; after that it just launches the app.
#
#  Usage: double-click this file in Finder, or run  ./MedSearch.command
#  (If macOS blocks it the first time: right-click ▸ Open ▸ Open.)
# ─────────────────────────────────────────────────────────────────────────────
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "──────────────────────────────────────────────"
echo "  MedSearch"
echo "──────────────────────────────────────────────"

# ── Need Python 3 ────────────────────────────────────────────────────────────
if ! command -v python3 >/dev/null 2>&1; then
  osascript -e 'display alert "MedSearch needs Python 3" message "Python 3 was not found. Install it from python.org or via Homebrew (brew install python), then double-click MedSearch.command again."' >/dev/null 2>&1 || true
  echo "  ✗ Python 3 not found. Install it and try again."
  exit 1
fi

# ── First-run setup: virtual environment + dependencies ──────────────────────
if [ ! -d ".venv" ]; then
  echo "  First run: setting up the environment (this happens once)…"
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# Install/refresh dependencies. requirements.txt lists flask + pywebview
# (rumps is optional and only needed for the separate menu-bar app).
NEED_INSTALL=0
python3 -c "import flask"   2>/dev/null || NEED_INSTALL=1
python3 -c "import webview" 2>/dev/null || NEED_INSTALL=1
if [ "$NEED_INSTALL" -eq 1 ]; then
  echo "  Installing dependencies…"
  pip install --quiet --upgrade pip
  if [ -f requirements.txt ]; then
    pip install --quiet -r requirements.txt
  else
    pip install --quiet flask pywebview
  fi
fi

# ── Launch ───────────────────────────────────────────────────────────────────
echo "  Starting MedSearch…"
echo "  (Close the app window to quit.)"
echo ""
exec python3 app.py
