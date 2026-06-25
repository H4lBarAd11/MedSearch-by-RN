#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
#  MedSearch — macOS .app Builder  (native window, no Terminal)
#  Creates "MedSearch.app" — a double-clickable launcher with a custom icon.
#  Run this ONCE on your Mac. The resulting .app can be copied to /Applications
#  or dragged to the Dock.
# ═════════════════════════════════════════════════════════════════════════════
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

APP_NAME="MedSearch"
APP_DIR="$SCRIPT_DIR/$APP_NAME.app"

echo ""
echo "  🔬  Building $APP_NAME.app …"
echo "  ─────────────────────────────────────────────"

# ── 1. Create the .app bundle structure ──
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS"
mkdir -p "$APP_DIR/Contents/Resources"

# ── 2. Info.plist ──
cat > "$APP_DIR/Contents/Info.plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>            <string>MedSearch</string>
    <key>CFBundleDisplayName</key>     <string>MedSearch</string>
    <key>CFBundleIdentifier</key>      <string>com.riccardonevoso.medsearch</string>
    <key>CFBundleVersion</key>         <string>4.0</string>
    <key>CFBundleShortVersionString</key><string>4.0</string>
    <key>CFBundlePackageType</key>     <string>APPL</string>
    <key>CFBundleExecutable</key>      <string>launcher</string>
    <key>CFBundleIconFile</key>        <string>icon.icns</string>
    <key>LSMinimumSystemVersion</key>  <string>10.13</string>
    <key>NSHighResolutionCapable</key> <true/>
</dict>
</plist>
PLIST

# ── 3. The launcher executable ──
# Runs the app directly in the background — no Terminal window, no Finder windows.
# Uses the project's virtualenv and ensures flask + pywebview are present.
cat > "$APP_DIR/Contents/MacOS/launcher" << LAUNCHER
#!/usr/bin/env bash
PROJECT_DIR="$SCRIPT_DIR"
cd "\$PROJECT_DIR"

# Ensure virtualenv exists
if [ ! -d ".venv" ]; then
    /usr/bin/env python3 -m venv .venv
fi
source .venv/bin/activate

# Ensure dependencies (quiet; only installs the first time)
python3 -c "import flask"   2>/dev/null || pip install --quiet flask
python3 -c "import webview" 2>/dev/null || pip install --quiet pywebview

# Launch the app. PyWebView opens a native window; no browser, no Terminal.
# Logs go to a hidden file instead of a visible Terminal window.
python3 app.py > "\$PROJECT_DIR/.medsearch_app.log" 2>&1
LAUNCHER

chmod +x "$APP_DIR/Contents/MacOS/launcher"

# ── 4. Build the .icns icon from the PNG ──
if [ -f "icon_preview.png" ]; then
  echo "  →  Building icon…"
  ICONSET="$SCRIPT_DIR/icon.iconset"
  rm -rf "$ICONSET"
  mkdir -p "$ICONSET"

  # Generate all required sizes
  for size in 16 32 64 128 256 512; do
    sips -z $size $size icon_preview.png --out "$ICONSET/icon_${size}x${size}.png" >/dev/null 2>&1
    double=$((size*2))
    sips -z $double $double icon_preview.png --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null 2>&1
  done

  # Convert iconset → icns
  iconutil -c icns "$ICONSET" -o "$APP_DIR/Contents/Resources/icon.icns" 2>/dev/null
  rm -rf "$ICONSET"
  echo "  ✓  Icon built"
else
  echo "  ⚠  icon_preview.png not found — app will use default icon"
fi

# ── 5. Done ──
echo ""
echo "  ✓  Build complete!"
echo "  ✓  Created: $APP_DIR"
echo ""
echo "  Next steps:"
echo "    • Double-click MedSearch.app to test it"
echo "    • Drag it to /Applications to install system-wide"
echo "    • Drag it to your Dock for one-click access"
echo ""
echo "  Note: first launch installs pywebview (~10-15s), then the"
echo "  native window opens. Every launch after is instant."
echo ""
