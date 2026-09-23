#!/usr/bin/env bash
cd "$(dirname "$0")"

if [ -f "bot.pid" ]; then
    PID=$(cat bot.pid)
    echo "Stopping bot with PID $PID..."
    kill $PID 2>/dev/null
    rm bot.pid
    echo "[OK] Bot stopped."
else
    echo "bot.pid not found. Searching for process..."
    pkill -f "python3 bot.py"
    echo "[OK] Bot stopped (via pkill)."
fi
