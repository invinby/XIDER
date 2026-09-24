#!/usr/bin/env bash
# XIDER v2.0 - macOS App Builder
# Создает XIDER.app, который запускает агент в фоне (start_agent.sh)

set -euo pipefail
cd "$(dirname "$0")"

APP_NAME="XIDER"
APP_DIR="${APP_NAME}.app"
CONTENTS_DIR="${APP_DIR}/Contents"
MACOS_DIR="${CONTENTS_DIR}/MacOS"
RESOURCES_DIR="${CONTENTS_DIR}/Resources"

echo "🍎 Сборка $APP_DIR..."

# Удаляем старую сборку если есть
rm -rf "$APP_DIR"

# Создаем структуру
mkdir -p "$MACOS_DIR"
mkdir -p "$RESOURCES_DIR"

# Создаем Info.plist
cat > "${CONTENTS_DIR}/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>launcher</string>
    <key>CFBundleIdentifier</key>
    <string>com.xider.agent</string>
    <key>CFBundleName</key>
    <string>XIDER</string>
    <key>CFBundleIconFile</key>
    <string>icon</string>
    <key>CFBundleShortVersionString</key>
    <string>2.0</string>
    <key>CFBundleVersion</key>
    <string>3.1.0</string>
    <key>LSUIElement</key>
    <true/> <!-- Приложение работает в фоне, без иконки в Dock -->
    <key>NSAppleEventsUsageDescription</key>
    <string>XIDER requires access to run scripts.</string>
    <key>NSCameraUsageDescription</key>
    <string>XIDER requires camera access.</string>
    <key>NSMicrophoneUsageDescription</key>
    <string>XIDER requires microphone access.</string>
    <key>NSDesktopFolderUsageDescription</key>
    <string>XIDER requires access to your files.</string>
</dict>
</plist>
EOF

# Создаем скрипт-лаунчер
TARGET_DIR=$(pwd)
cat > "${MACOS_DIR}/launcher" <<EOF
#!/usr/bin/env bash
# Лаунчер XIDER (передает управление в оригинальную папку)
cd "$TARGET_DIR"
./start_agent.sh > /tmp/xider_mac_launcher.log 2>&1 &
EOF

chmod +x "${MACOS_DIR}/launcher"

echo "✅ $APP_DIR успешно собран!"
echo "💡 Чтобы запустить агента, просто дважды кликните на $APP_DIR в Finder."
echo "💡 (Если не работает, попробуйте Right Click -> Open)"
