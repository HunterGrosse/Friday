#!/bin/bash
# make_app.sh - build ~/Applications/Zoey.app.
#
# The app is a thin launcher: it runs app.py from THIS folder, so editing her
# code or personality never needs a rebuild. Rebuild only if you move the
# folder or change Python versions.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
APP="$HOME/Applications/Zoey.app"
VENV_PY="$DIR/.venv/bin/python"

[[ -x "$VENV_PY" ]] || { echo "No .venv found. See README, Step 4."; exit 1; }
"$VENV_PY" -c "import rumps" 2>/dev/null || "$DIR/.venv/bin/pip" install -q -r "$DIR/requirements-app.txt"

# The real Python interpreter, and the venv's packages for it.
REAL_PY="$("$VENV_PY" -c 'import sys, pathlib; print(pathlib.Path(sys.base_prefix, "Resources/Python.app/Contents/MacOS/Python"))')"
SITE="$("$VENV_PY" -c 'import site; print(site.getsitepackages()[0])')"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# A copy of Python inside the app, so macOS calls it "Zoey", not "Python".
cp "$REAL_PY" "$APP/Contents/MacOS/zoey-python"

# Her icon: the same pink Z the app draws in the Dock.
ICONSET="$(mktemp -d)/Zoey.iconset"
mkdir -p "$ICONSET"
"$VENV_PY" - "$ICONSET" "$DIR" <<'PY'
import sys
sys.path.insert(0, sys.argv[2])
from AppKit import NSBitmapImageRep, NSGraphicsContext, NSPNGFileType, NSMakeRect
from app import zoey_icon

icon = zoey_icon()
def png(px):
    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, px, px, 8, 4, True, False, "NSDeviceRGBColorSpace", 0, 0)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep))
    icon.drawInRect_(NSMakeRect(0, 0, px, px))
    NSGraphicsContext.restoreGraphicsState()
    return rep.representationUsingType_properties_(NSPNGFileType, {})

for size in (16, 32, 128, 256, 512):
    png(size).writeToFile_atomically_(f"{sys.argv[1]}/icon_{size}x{size}.png", True)
    png(size * 2).writeToFile_atomically_(f"{sys.argv[1]}/icon_{size}x{size}@2x.png", True)
PY
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/Zoey.icns"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>                  <string>Zoey</string>
  <key>CFBundleDisplayName</key>           <string>Zoey</string>
  <key>CFBundleIdentifier</key>            <string>com.zoey.app</string>
  <key>CFBundleExecutable</key>            <string>Zoey</string>
  <key>CFBundleIconFile</key>              <string>Zoey</string>
  <key>CFBundlePackageType</key>           <string>APPL</string>
  <key>CFBundleVersion</key>               <string>1</string>
  <!-- macOS shows this when it asks you for microphone access. -->
  <key>NSMicrophoneUsageDescription</key>  <string>Zoey listens when you click her or say "Hey Zoey".</string>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/Zoey" <<LAUNCHER
#!/bin/bash
export PYTHONPATH="$SITE"
exec "$APP/Contents/MacOS/zoey-python" "$DIR/app.py"
LAUNCHER
chmod +x "$APP/Contents/MacOS/Zoey"

codesign --force --deep --sign - "$APP" 2>/dev/null || true   # ad-hoc sign so macOS can track mic permission
touch "$APP"   # nudge Finder to pick up the new icon
echo "Built $APP. Open it from Spotlight (Zoey) or run: open -a Zoey"
