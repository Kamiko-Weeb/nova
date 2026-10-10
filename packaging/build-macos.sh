#!/usr/bin/env bash
# Build Nova.app and package it into a .dmg. Must run on macOS - it uses
# iconutil and hdiutil, which do not exist elsewhere.
#
#   bash packaging/build-macos.sh
#
# Output: dist/Nova-<version>.dmg
set -euo pipefail

VERSION="${NOVA_VERSION:-0.1.0}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="$ROOT/build"
DIST="$ROOT/dist"
STAGE="$BUILD/dmg"
DMG="$DIST/Nova-$VERSION.dmg"

cd "$ROOT"

if [[ "$(uname)" != "Darwin" ]]; then
  echo "This script needs macOS (iconutil and hdiutil)." >&2
  exit 1
fi

echo "==> Cleaning"
rm -rf "$BUILD" "$DIST"
mkdir -p "$BUILD" "$DIST"

echo "==> Installing build dependencies"
python3 -m pip install --quiet --upgrade pip
python3 -m pip install --quiet -r requirements.txt pyinstaller pillow

echo "==> Building the icon"
python3 make_icon.py
ICONSET="$BUILD/nova.iconset"
mkdir -p "$ICONSET"
for size in 16 32 64 128 256 512; do
  sips -z $size $size nova-1024.png --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  double=$((size * 2))
  sips -z $double $double nova-1024.png \
       --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$BUILD/nova.icns"
echo "    $BUILD/nova.icns"

echo "==> Building Nova.app"
python3 -m PyInstaller \
  --noconfirm --clean --windowed \
  --name Nova \
  --icon "$BUILD/nova.icns" \
  --osx-bundle-identifier "com.nova.agent" \
  --add-data "$ROOT/.env.example:." \
  --hidden-import tkinter \
  --hidden-import PIL._tkinter_finder \
  --collect-submodules nova \
  --paths "$ROOT" \
  --distpath "$DIST" \
  --workpath "$BUILD/pyinstaller" \
  --specpath "$BUILD" \
  packaging/nova_app.py

APP="$DIST/Nova.app"
[[ -d "$APP" ]] || { echo "PyInstaller did not produce $APP" >&2; exit 1; }

# PyInstaller writes a generic version; set ours so Finder shows it.
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" \
  "$APP/Contents/Info.plist" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $VERSION" \
  "$APP/Contents/Info.plist" 2>/dev/null || true
# macOS needs a stated reason before it will even offer the permission prompts.
/usr/libexec/PlistBuddy -c "Add :NSAppleEventsUsageDescription string \
'Nova controls other applications to carry out the tasks you give it.'" \
  "$APP/Contents/Info.plist" 2>/dev/null || true

echo "==> Ad-hoc signing"
# Not a Developer ID signature - users still get Gatekeeper's warning on first
# launch - but it keeps the bundle internally consistent.
codesign --force --deep --sign - "$APP" 2>/dev/null || \
  echo "    (codesign unavailable, continuing unsigned)"

echo "==> Assembling the disk image"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
cp "$ROOT/packaging/INSTALL-MAC.txt" "$STAGE/Read Me First.txt" 2>/dev/null || true

hdiutil create \
  -volname "Nova $VERSION" \
  -srcfolder "$STAGE" \
  -ov -format UDZO \
  "$DMG"

echo ""
echo "Built: $DMG"
ls -lh "$DMG" | awk '{print "       " $5}'
