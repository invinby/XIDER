#!/usr/bin/env bash
# XIDER Agent Launcher v2.0 — macOS
# Убивает зомби, проверяет окружение, запускает в фоне с watchdog.
set -euo pipefail
cd "$(dirname "$0")"

ENTRY="xgent_mcs.py"
PIDFILE="agent.pid"
LOGFILE="agent.log"
VENV_DIR="venv"

# ──────────────────────────────────────────
# 0. Флаг --setup — мастер разрешений macOS
# ──────────────────────────────────────────
if [[ "${1:-}" == "--setup" ]]; then
    echo "🍎 Запуск мастера настройки разрешений macOS..."
    python3 setup_mac.py
    exit 0
fi

# Полная остановка: убрать LaunchAgent, чтобы macOS не подняла его снова.
if [[ "${1:-}" == "--stop" || "${1:-}" == "-x" ]]; then
    PLIST="$HOME/Library/LaunchAgents/com.xgent.agent.plist"
    launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
    rm -f "$PLIST"
    ./stop_agent.sh
    exit 0
fi

if [[ "${1:-}" == "--status" || "${1:-}" == "-s" ]]; then
    if command -v launchctl >/dev/null 2>&1; then
        LAUNCH_STATE="$(launchctl print "gui/$(id -u)/com.xgent.agent" 2>/dev/null || true)"
        if printf '%s\n' "$LAUNCH_STATE" | grep -Eq 'pid = [0-9]+|state = running'; then
            echo "[OK] XIDER Agent работает через LaunchAgent"
            echo "[LOG] $(pwd)/$LOGFILE"
            exit 0
        elif [ -n "$LAUNCH_STATE" ]; then
            echo "[STOPPED] LaunchAgent загружен, но процесс агента не запущен"
            echo "[LOG] $(pwd)/$LOGFILE"
            exit 1
        fi
    fi
    if [[ -f "$PIDFILE" ]]; then
        PID="$(cat "$PIDFILE" 2>/dev/null || true)"
        if [[ -n "$PID" ]] && ps -p "$PID" >/dev/null 2>&1; then
            echo "[OK] XIDER Agent работает (PID: $PID)"
            echo "[LOG] $(pwd)/$LOGFILE"
            exit 0
        fi
    fi
    echo "[STOPPED] XIDER Agent не запущен"
    exit 1
fi

# ──────────────────────────────────────────
# 1. Проверка Python
# ──────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "❌ [ERROR] python3 не найден в PATH"
    exit 1
fi

# ──────────────────────────────────────────
# 2. Создание/активация venv
# ──────────────────────────────────────────
if [ ! -d "$VENV_DIR" ]; then
    echo "📦 Создание виртуального окружения..."
    python3 -m venv "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"
pip install -q -r requirements.txt

# ──────────────────────────────────────────
# 3. Создание .env если нет
# ──────────────────────────────────────────
if [ ! -f ".env" ]; then
    echo "⚠️  Файл .env не найден. Копирую из .env.example..."
    cp .env.example .env
    echo "✏️  Заполните .env и перезапустите агент!"
    exit 1
fi

# ──────────────────────────────────────────
# 4. Убийство старых экземпляров (и зомби!)
# ──────────────────────────────────────────
# По PID-файлу
if [ -f "$PIDFILE" ]; then
    OLD_PID=$(cat "$PIDFILE" 2>/dev/null || echo "")
    if [ -n "$OLD_PID" ] && ps -p "$OLD_PID" >/dev/null 2>&1; then
        echo "🛑 Останавливаю предыдущий агент (PID: $OLD_PID)..."
        kill "$OLD_PID" 2>/dev/null || true
        sleep 1
        kill -9 "$OLD_PID" 2>/dev/null || true
    fi
    rm -f "$PIDFILE"
fi

# Убиваем зомби по имени файла (если PID-файл потерялся)
ZOMBIE_PIDS=$(pgrep -f "$ENTRY" 2>/dev/null || true)
if [ -n "$ZOMBIE_PIDS" ]; then
    echo "🧟 Убиваю зомби-процессы: $ZOMBIE_PIDS"
    echo "$ZOMBIE_PIDS" | xargs kill -9 2>/dev/null || true
    sleep 1
fi

# ──────────────────────────────────────────
# 5. Проверка разрешений на исполнение
# ──────────────────────────────────────────
chmod +x "$0"

# ──────────────────────────────────────────
# 6. Автозапуск: сначала регистрируем LaunchAgent,
#    затем отдаём запуск самому launchd (без второго процесса).
# ──────────────────────────────────────────
if [[ "${1:-}" != "--foreground" && "${1:-}" != "-f" && "${XIDER_NO_AUTOSTART:-0}" != "1" && -x "$VENV_DIR/bin/python3" ]]; then
    if "$VENV_DIR/bin/python3" -c 'from xgent_mcs import XgentClient; XgentClient()._do_autorun_enable({})' >/dev/null 2>&1 \
       && launchctl print "gui/$(id -u)/com.xgent.agent" >/dev/null 2>&1; then
        echo "🚀 LaunchAgent зарегистрирован; агент запускается через launchd."
        exit 0
    fi
fi

# ──────────────────────────────────────────
# 7. Запуск (--foreground или фон)
# ──────────────────────────────────────────
if [[ "${1:-}" == "--foreground" || "${1:-}" == "-f" ]]; then
    echo "🟢 XIDER Agent v2.0 — интерактивный режим (Ctrl+C для выхода)"
    python3 "$ENTRY"
else
    echo "🚀 Запускаю XIDER Agent v2.0 в фоне..."
    nohup python3 "$ENTRY" >"$LOGFILE" 2>&1 &
    PID=$!
    echo "$PID" >"$PIDFILE"

    sleep 1.5
    if ps -p "$PID" >/dev/null 2>&1; then
        echo "╔══════════════════════════════════════╗"
        echo "║  ✅  XIDER Agent запущен успешно!    ║"
        echo "╠══════════════════════════════════════╣"
        echo "║  PID:  $PID"
        echo "║  Лог:  $(pwd)/$LOGFILE"
        echo "║  Стоп: ./stop_agent.sh               ║"
        echo "╚══════════════════════════════════════╝"
        echo ""
        echo "💡 Если нет доступа к экрану/мику — запусти: ./start_agent.sh --setup"
    else
        echo "╔══════════════════════════════════════╗"
        echo "║  ❌  Агент завершился с ошибкой!     ║"
        echo "╚══════════════════════════════════════╝"
        echo ""
        echo "Последние строки лога:"
        tail -n 20 "$LOGFILE" 2>/dev/null || echo "(лог пустой)"
        exit 1
    fi
fi
