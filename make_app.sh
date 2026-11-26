#!/bin/bash
# make_app.sh - build ~/Applications/Zoey.app, a menu-bar app for Zoey.
#
# The app is a thin launcher: it runs app.py from THIS folder, so editing her
# code or personality never needs a rebuild. Rebuild only if you move the folder.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
APP="$HOME/Applications/Zoey.app"

[[ -x "$DIR/.venv/bin/python" ]] || { echo "No .venv found. See README, Step 4."; exit 1; }
"$DIR/.venv/bin/python" -c "import rumps" 2>/dev/null || "$DIR/.venv/bin/pip" install -q -r "$DIR/requirements-app.txt"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>                  <string>Zoey</string>
  <key>CFBundleIdentifier</key>            <string>com.zoey.app</string>
  <key>CFBundleExecutable</key>            <string>Zoey</string>
  <key>CFBundlePackageType</key>           <string>APPL</string>
  <key>CFBundleVersion</key>               <string>1</string>
  <!-- Menu-bar only: no Dock icon. -->
  <key>LSUIElement</key>                   <true/>
  <!-- macOS shows this when it asks you for microphone access. -->
  <key>NSMicrophoneUsageDescription</key>  <string>Zoey listens when you click Talk or say "Hey Zoey".</string>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/Zoey" <<LAUNCHER
#!/bin/bash
exec "$DIR/.venv/bin/python" "$DIR/app.py"
LAUNCHER
chmod +x "$APP/Contents/MacOS/Zoey"

codesign --force --deep --sign - "$APP" 2>/dev/null || true   # ad-hoc sign so macOS can track mic permission
echo "Built $APP. Open it from Spotlight (Zoey) or run: open -a Zoey"
