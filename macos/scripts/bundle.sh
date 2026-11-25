#!/bin/sh
# Builds build/Syncbaton.app (ad-hoc signed) and build/Syncbaton.zip.
# Usage: scripts/bundle.sh [version]   (default: contents of VERSION)
set -eu

cd "$(dirname "$0")/.."
VERSION="${1:-$(cat VERSION)}"
APP="build/Syncbaton.app"

swift build -c release
BIN="$(swift build -c release --show-bin-path)/Syncbaton"

rm -rf build
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/Syncbaton"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>Syncbaton</string>
    <key>CFBundleDisplayName</key><string>Syncbaton</string>
    <key>CFBundleIdentifier</key><string>io.github.syncbaton.app</string>
    <key>CFBundleExecutable</key><string>Syncbaton</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>${VERSION}</string>
    <key>CFBundleVersion</key><string>${VERSION}</string>
    <key>LSMinimumSystemVersion</key><string>15.0</string>
    <key>LSUIElement</key><true/>
    <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

codesign --force --sign - "$APP"
ditto -c -k --keepParent "$APP" build/Syncbaton.zip
echo "Built $APP and build/Syncbaton.zip (version $VERSION)"
