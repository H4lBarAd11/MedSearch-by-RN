#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
#  MedSearch Menu Bar — macOS .app Builder
#  Creates "MedSearch Menu Bar.app" — a background status-bar app (no Dock icon)
#  that puts the MedSearch icon in your menu bar for quick searches.
#
#  Run this ONCE on your Mac. The resulting .app can be copied to /Applications
#  and added to System Settings ▸ General ▸ Login Items so it starts at login.
#
#  Requires: rumps  (pip3 install rumps)
# ═════════════════════════════════════════════════════════════════════════════
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

APP_NAME="MedSearch Menu Bar"
APP_DIR="$SCRIPT_DIR/$APP_NAME.app"

echo ""
echo "  🔍  Building $APP_NAME.app …"
echo "  ─────────────────────────────────────────────"

# ── 0. Sanity: we need a Python that has rumps AND can show a menu-bar icon ──
# Use the Python that's running when you invoke this script (via `python3`).
# IMPORTANT (macOS): a virtual-env Python usually canNOT display a status-bar
# item — it's not a "framework build". If you installed rumps only in a venv and
# the icon never appears, install it into your Homebrew/system Python instead:
#     /opt/homebrew/bin/python3 -m pip install rumps --break-system-packages
# and run this script with that same python3.
BUILD_PYTHON="$(command -v python3)"
if [ -z "$BUILD_PYTHON" ]; then
  echo "  ⚠  No python3 found on PATH."
  exit 1
fi
if ! "$BUILD_PYTHON" -c "import rumps" 2>/dev/null; then
  echo ""
  echo "  ⚠  'rumps' is not installed for: $BUILD_PYTHON"
  echo "        $BUILD_PYTHON -m pip install rumps"
  echo ""
  exit 1
fi
# Warn (don't block) if the building Python looks like a venv — the bundle may
# not show its icon. We bake in a fallback to Homebrew python at runtime too.
if "$BUILD_PYTHON" -c "import sys; sys.exit(0 if sys.prefix != sys.base_prefix else 1)" 2>/dev/null; then
  echo ""
  echo "  ⚠  NOTE: $BUILD_PYTHON looks like a virtual environment."
  echo "     macOS menu-bar items often DON'T appear under a venv Python."
  echo "     If the icon doesn't show, install rumps into Homebrew Python:"
  echo "        /opt/homebrew/bin/python3 -m pip install rumps --break-system-packages"
  echo "     then re-run this script with that python3."
  echo ""
fi
echo "  Using Python: $BUILD_PYTHON"

# ── 1. Create the .app bundle structure ──
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS"
mkdir -p "$APP_DIR/Contents/Resources"

# ── 2. Info.plist — LSUIElement=true makes it a background (menu-bar-only) app ──
cat > "$APP_DIR/Contents/Info.plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>            <string>MedSearch Menu Bar</string>
    <key>CFBundleDisplayName</key>     <string>MedSearch Menu Bar</string>
    <key>CFBundleIdentifier</key>      <string>com.riccardonevoso.medsearch.menubar</string>
    <key>CFBundleVersion</key>         <string>1.0</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundlePackageType</key>     <string>APPL</string>
    <key>CFBundleExecutable</key>      <string>launcher</string>
    <key>LSMinimumSystemVersion</key>  <string>10.13</string>
    <!-- Background app: no Dock icon, no app-switcher entry, just the menu bar -->
    <key>LSUIElement</key>             <true/>
</dict>
</plist>
PLIST

# ── 3. Launcher script ──
# Runs menubar.py from this project directory. We bake in the exact Python that
# built the app (BUILD_PYTHON), but prefer a framework-build Python that has
# rumps at runtime (these reliably show the status-bar icon).
cat > "$APP_DIR/Contents/MacOS/launcher" << LAUNCHER
#!/usr/bin/env bash
# Launch the MedSearch menu-bar app.
PROJECT_DIR="$SCRIPT_DIR"
cd "\$PROJECT_DIR"

# The Python that built this app.
BAKED_PYTHON="$BUILD_PYTHON"

# Prefer a framework-build Python that has rumps (reliably shows the icon);
# fall back to the baked one.
PYTHON=""
for candidate in \\
  /opt/homebrew/bin/python3 \\
  /usr/local/bin/python3 \\
  /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \\
  "\$BAKED_PYTHON"; do
  if [ -n "\$candidate" ] && command -v "\$candidate" >/dev/null 2>&1 \\
     && "\$candidate" -c "import rumps" >/dev/null 2>&1; then
    PYTHON="\$candidate"
    break
  fi
done
if [ -z "\$PYTHON" ]; then PYTHON="\$BAKED_PYTHON"; fi

if [ -z "\$PYTHON" ]; then
  osascript -e 'display alert "MedSearch Menu Bar" message "No Python with rumps was found. In Terminal run: /opt/homebrew/bin/python3 -m pip install rumps --break-system-packages"'
  exit 1
fi

exec "\$PYTHON" "\$PROJECT_DIR/menubar.py"
LAUNCHER
chmod +x "$APP_DIR/Contents/MacOS/launcher"

# ── 4. Bundle icon (the books+network mark with a menu-bar strip) ──
# Prefer the dedicated menu-bar app icon; fall back to the main app icon.
ICON_SRC=""
if   [ -f "$SCRIPT_DIR/menubar_app_icon.icns" ]; then ICON_SRC="$SCRIPT_DIR/menubar_app_icon.icns"
elif [ -f "$SCRIPT_DIR/icon.icns" ];             then ICON_SRC="$SCRIPT_DIR/icon.icns"
fi
if [ -n "$ICON_SRC" ]; then
  cp "$ICON_SRC" "$APP_DIR/Contents/Resources/appicon.icns"
  /usr/libexec/PlistBuddy -c "Add :CFBundleIconFile string appicon.icns" \
    "$APP_DIR/Contents/Info.plist" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Set :CFBundleIconFile appicon.icns" \
    "$APP_DIR/Contents/Info.plist" 2>/dev/null || true
fi

echo "  ✓ Built: $APP_DIR"
echo ""
echo "  Next steps:"
echo "   • Double-click \"$APP_NAME.app\" to start it — look for the MedSearch icon (books + network) in the menu bar."
echo "   • To start it automatically: System Settings ▸ General ▸ Login Items ▸ +"
echo "   • Move it to /Applications if you like."
echo ""
echo "  Note: this menu-bar app launches the main MedSearch app (app.py) from"
echo "        this same folder, so keep them together (or edit the path)."
echo ""
