#!/usr/bin/env bash
cd "$(dirname "$0")"

# Record an intentional owner stop before terminating the worker, so the
# visible Guardian distinguishes it from a crash and does not relaunch it.
if [ -x "venv/bin/python3" ] && [ -f ".env" ]; then
    if ! "venv/bin/python3" -c 'from config import set_guardian_desired_running; set_guardian_desired_running(False)' >/dev/null 2>&1; then
        echo "⚠️ Не удалось передать Guardian намерение остановить агента; он может восстановить процесс." >&2
    fi
fi

if [ -f "agent.pid" ]; then
    PID=$(cat agent.pid)
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "🛑 Остановка XGENT Agent (PID: $PID)..."
        kill "$PID" 2>/dev/null
        sleep 1
        if ps -p "$PID" > /dev/null 2>&1; then
            kill -9 "$PID" 2>/dev/null
        fi
        echo "✅ [OK] Агент остановлен."
    else
        echo "ℹ️ Процесс с PID $PID уже не работает."
    fi
    rm -f agent.pid
else
    # Поиск по имени процесса
    PIDS=$(pgrep -f "xgent_mcs" | grep -v "$$")
    if [ -n "$PIDS" ]; then
        echo "🛑 Остановка найденных процессов xgent_mcs: $PIDS..."
        kill $PIDS 2>/dev/null
        echo "✅ [OK] Процессы остановлены."
    else
        echo "ℹ️ Агент XGENT не запущен."
    fi
fi
