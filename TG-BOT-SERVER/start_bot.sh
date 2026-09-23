#!/usr/bin/env bash
cd "$(dirname "$0")"

if ! command -v python3 &> /dev/null; then
    echo "[ERROR] python3 not found in PATH"
    exit 1
fi

if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate
pip install -q -r requirements.txt

if [ ! -f ".env" ]; then
    echo "[!] .env file not found. Copying from .env.example..."
    cp .env.example .env
fi

echo "Starting TG-BOT-SERVER in background..."
nohup python3 bot.py > bot.log 2>&1 &
echo $! > bot.pid
echo "[OK] Bot is running (PID: $(cat bot.pid)). You can safely close this terminal."
