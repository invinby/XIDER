"""Local launcher. No Telegram imports, no process-name-based killing."""
import argparse
import asyncio
from collections import deque
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent
LOCK = ROOT / ".bot.lock"
STATE = ROOT / ".bot-runtime.json"
STOP = ROOT / ".bot-stop"
LOG = ROOT / "bot.log"


def _lock(stream):
    stream.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(stream):
    stream.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def instance_guard():
    """OS releases the lock even after a crash. Token prevents stale stop requests."""
    with LOCK.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        try:
            _lock(stream)
        except OSError as exc:
            raise RuntimeError("Этот сервер уже работает. Второй экземпляр нахуй не нужен.") from exc
        token = uuid.uuid4().hex
        try:
            STATE.write_text(json.dumps({"pid": os.getpid(), "token": token}), encoding="utf-8")
            yield token
        finally:
            STATE.unlink(missing_ok=True)
            _unlock(stream)


def is_running():
    if not LOCK.exists():
        return False
    with LOCK.open("r+b") as stream:
        try:
            _lock(stream)
        except OSError:
            return True
        _unlock(stream)
        return False


def request_stop():
    if not is_running():
        print("Сервер не запущен. Убивать тут некого.")
        return 0
    try:
        token = json.loads(STATE.read_text(encoding="utf-8"))["token"]
    except (OSError, ValueError, KeyError):
        print("Сервер ещё запускается. Повтори остановку через пару секунд.")
        return 1
    STOP.write_text(token, encoding="utf-8")
    print("Отправлен запрос остановки именно этому серверу. Чужие боты живут дальше.")
    return 0


async def watch_stop(dispatcher, token):
    while True:
        await asyncio.sleep(0.5)
        try:
            requested = STOP.read_text(encoding="utf-8").strip() == token
        except FileNotFoundError:
            requested = False
        if requested:
            try:
                await dispatcher.stop_polling()
            except RuntimeError:
                continue  # polling may not have started yet
            return


def show_logs():
    if not LOG.exists():
        print("Фонового лога пока нет. Сначала надо что-нибудь запустить, бля.")
        return 0
    with LOG.open(encoding="utf-8", errors="replace") as stream:
        print("".join(deque(stream, maxlen=60)))
    return 0


def start(background=False, tray=False):
    if is_running():
        print("Сервер уже работает. Логи: --logs; остановка: --stop.")
        return 1
    command = [sys.executable, "-u", str(ROOT / "bot.py"), "--serve"]
    if tray:
        command.append("--tray")
    if not background and not tray:
        try:
            return subprocess.call(command, cwd=ROOT)
        except KeyboardInterrupt:
            return 130
    options = {"cwd": ROOT, "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    with LOG.open("a", encoding="utf-8") as stream:
        stream.write("\n--- Запуск XIDER: " + time.strftime("%Y-%m-%d %H:%M:%S") + " ---\n")
        stream.flush()
        child = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT, **options)
    # Detect immediate errors, not pretend a spawned process is a connected bot.
    try:
        code = child.wait(timeout=1.5)
    except subprocess.TimeoutExpired:
        print(f"Процесс запущен в фоне, PID {child.pid}. Подключение к Telegram смотри в bot.log.")
        print("Терминал можно закрыть. Остановка: stop_bot.bat / stop_bot.sh.")
        return 0
    print(f"Сервер завершился при запуске, код {code}. Последние строки:")
    show_logs()
    return code or 1


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="XIDER: консоль, фон и управление локальным сервером")
    modes = parser.add_mutually_exclusive_group()
    for name in ("console", "background", "tray", "logs", "stop", "status"):
        modes.add_argument("--" + name, action="store_true")
    args = parser.parse_args(argv)
    mode = next((name for name, value in vars(args).items() if value), None)
    if mode is None:
        if not sys.stdin.isatty():
            parser.error("Без терминала укажи --console или --background: угадывать режим не буду.")
        print("\nXIDER — как запускаем эту хрень?\n")
        print("1 — С логами в этом окне. Ctrl+C остановит сервер.")
        print("2 — В фоне. Логи пишутся в bot.log, окно можно закрыть.")
        print("3 — В фоне со значком в трее.")
        print("4 — Последние 60 строк фонового лога.")
        print("5 — Остановить этот сервер.\n0 — Ничего не запускать.")
        choices = {"1": "console", "2": "background", "3": "tray", "4": "logs", "5": "stop"}
        while mode is None:
            try:
                choice = input("Выбор: ").strip()
            except (EOFError, KeyboardInterrupt):
                return 0
            if choice == "0":
                return 0
            mode = choices.get(choice)
            if mode is None:
                print("Нужна цифра от 0 до 5. Это меню, не экзамен по телепатии.")
    if mode == "stop":
        return request_stop()
    if mode == "logs":
        return show_logs()
    if mode == "status":
        print("Процесс работает; связь с Telegram проверяй по логам." if is_running() else "Сервер не запущен.")
        return 0
    return start(background=mode == "background", tray=mode == "tray")


if __name__ == "__main__":
    raise SystemExit(main())
