"""Хранилище известных устройств XGENT (JSON-файл рядом с bot.py)."""

import json
import threading
from pathlib import Path


class DeviceStore:
    """Потокобезопасное JSON-хранилище устройств.

    Данные перезаписываются на диск только при фактическом изменении,
    чтобы heartbeat-сообщения (раз в 60 с на устройство) не генерировали
    бессмысленные записи на диск.
    """

    FILE = Path(__file__).resolve().parent / "known_devices.json"

    def __init__(self) -> None:
        self._devices: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        try:
            self._devices = json.loads(self.FILE.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            self._devices = {}

    def _save(self) -> None:
        tmp = self.FILE.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self._devices, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.FILE)

    def upsert(self, device_id: str, info: dict) -> None:
        with self._lock:
            old = self._devices.get(device_id)
            merged = {**(old or {}), **info}
            if old == merged:
                return
            self._devices[device_id] = merged
            self._save()

    def all(self) -> dict:
        with self._lock:
            return dict(self._devices)

    def get(self, device_id: str) -> dict | None:
        with self._lock:
            return self._devices.get(device_id)

    def rename(self, device_id: str, name: str) -> bool:
        """Переименовать устройство (отображаемое имя в боте)."""
        with self._lock:
            if device_id not in self._devices:
                return False
            self._devices[device_id]["name"] = name
            self._save()
            return True

    def remove(self, device_id: str) -> bool:
        """Забыть устройство — удалить из хранилища."""
        with self._lock:
            if device_id not in self._devices:
                return False
            del self._devices[device_id]
            self._save()
            return True

    def get_favorites(self, device_id: str) -> list:
        """Список избранных команд устройства (кнопки быстрого доступа)."""
        with self._lock:
            info = self._devices.get(device_id) or {}
            return list(info.get("favorites", []))

    def toggle_favorite(self, device_id: str, action: str) -> bool:
        """Добавить/убрать команду из избранного. True — если добавлена."""
        with self._lock:
            info = self._devices.get(device_id)
            if info is None:
                return False
            favs = list(info.get("favorites", []))
            if action in favs:
                favs.remove(action)
                added = False
            else:
                favs.append(action)
                added = True
            info["favorites"] = favs
            self._save()
            return added

    def clear(self) -> None:
        """Полностью очистить список сохраненных устройств."""
        with self._lock:
            self._devices.clear()
            self._save()