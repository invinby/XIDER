#!/usr/bin/env bash
# ==============================================================================
# XGENT-MCS — Сборка единого автономного бинарника для macOS (One-file executable)
# ==============================================================================
set -euo pipefail

cd "$(dirname "$0")"

echo "🍎 [XGENT-MCS] Запуск сборки автономного исполняемого файла для macOS..."

# 1. Проверяем наличие Python 3
if ! command -v python3 &>/dev/null; then
    echo "❌ Ошибка: python3 не найден. Установите Python 3 через Homebrew или python.org"
    exit 1
fi

# 2. Создаем или активируем виртуальное окружение
if [ ! -d "venv" ]; then
    echo "📦 Создание виртуального окружения venv..."
    python3 -m venv venv
fi

source venv/bin/activate

# 3. Устанавливаем зависимости и pyinstaller
echo "📥 Установка зависимостей..."
pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller

# 4. Собираем единый исполняемый файл
echo "⚙️ Сборка бинарника через PyInstaller..."
pyinstaller --clean XGENT-MCS.spec

# 5. Делаем бинарник исполняемым и перемещаем в корень
if [ -f "dist/XGENT-MCS" ]; then
    chmod +x dist/XGENT-MCS
    cp dist/XGENT-MCS ./XGENT-MCS
    echo "✅ [ГОТОВО] Автономный бинарник собран: ./XGENT-MCS"
    echo "💡 Запуск в 1 клик без зависимостей: ./XGENT-MCS &"
else
    echo "❌ Ошибка при сборке. Проверьте вывод PyInstaller выше."
    exit 1
fi
