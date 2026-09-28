#!/usr/bin/env python3
"""X-Guard Keeper: прозрачный supervisor для XIDER-агента.

Запускает агент, перезапускает его после аварийного завершения с ограниченным
backoff и пишет понятный журнал. Это не скрытый процесс и не заменяет штатный
LaunchAgent/Task Scheduler: его можно остановить обычным SIGTERM/Ctrl+C.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def configure_logging(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


class Keeper:
    def __init__(self, command: list[str], log_path: Path, max_backoff: int = 60):
        if not command:
            raise ValueError("command must not be empty")
        self.command = command
        self.max_backoff = max(1, int(max_backoff))
        self.stop_requested = False
        self.child: subprocess.Popen | None = None
        configure_logging(log_path)

    def stop(self, *_args) -> None:
        self.stop_requested = True
        child = self.child
        if child and child.poll() is None:
            logging.info("stop requested; terminating child pid=%s", child.pid)
            child.terminate()

    def run(self) -> int:
        signal.signal(signal.SIGTERM, self.stop)
        if hasattr(signal, "SIGINT"):
            signal.signal(signal.SIGINT, self.stop)
        backoff = 1
        while not self.stop_requested:
            started = time.monotonic()
            logging.info("starting agent: %s", self.command)
            try:
                self.child = subprocess.Popen(self.command)
                code = self.child.wait()
            except OSError:
                logging.exception("failed to start agent")
                code = 127
            finally:
                self.child = None
            if self.stop_requested:
                break
            runtime = time.monotonic() - started
            logging.warning("agent exited code=%s runtime=%.1fs", code, runtime)
            # Долгий здоровый запуск сбрасывает backoff; быстрые падения замедляют
            # рестарты, чтобы не устроить бесконечный цикл и нагрузку на хост.
            if runtime >= 30:
                backoff = 1
            else:
                backoff = min(self.max_backoff, backoff * 2)
            logging.info("restart in %ss", backoff)
            time.sleep(backoff)
        logging.info("keeper stopped")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run XIDER agent under X-Guard Keeper")
    parser.add_argument("--log", type=Path, default=Path("guard-keeper.log"))
    parser.add_argument("--max-backoff", type=int, default=60)
    parser.add_argument("command", nargs=argparse.REMAINDER, help="-- command args")
    args = parser.parse_args()
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.error("provide command after --")
    return Keeper(command, args.log, args.max_backoff).run()


if __name__ == "__main__":
    raise SystemExit(main())
