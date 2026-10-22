"""Honest, local preview text for disruptive power actions."""

from __future__ import annotations

import html


_ACTIONS = {
    "sleep": (
        "Сон",
        "ОС переведёт устройство в сон. Агент может временно пропасть из сети "
        "и ответит снова после пробуждения.",
        "Если устройство разрядится во сне, несохранённые изменения в открытых приложениях могут быть потеряны.",
    ),
    "reboot": (
        "Перезапуск",
        "ОС завершит текущий сеанс и перезапустит устройство. Агент сможет выйти на связь после загрузки.",
        "Открытые приложения могут закрыться без сохранения последних изменений.",
    ),
    "shutdown": (
        "Выключение",
        "ОС завершит текущий сеанс и полностью выключит устройство. Агент останется офлайн, пока устройство не включат.",
        "Открытые приложения могут закрыться без сохранения последних изменений.",
    ),
}
_PLATFORMS = {"windows", "macos", "unknown"}


def render(action: str, target: str, *, platform: str) -> str | None:
    """Build an escaped preview; never claims to inspect unsaved application state."""
    if (not isinstance(action, str) or action not in _ACTIONS
            or not isinstance(target, str) or not target.strip()
            or len(target) > 256 or any(ord(char) < 32 or ord(char) == 127 for char in target)
            or not isinstance(platform, str) or platform not in _PLATFORMS):
        return None
    title, effect, risk = _ACTIONS[action]
    system = {"windows": "Windows", "macos": "macOS", "unknown": "ОС не определена"}[platform]
    escaped_target = html.escape(target.strip(), quote=True)
    return (
        f"⚠️ <b>Предпросмотр действия: {title}</b>\n"
        f"Устройство: <b>{escaped_target}</b> · {system}\n"
        f"Ожидаемый эффект: {effect}\n"
        f"Риск: {risk}\n"
        "Проверка сохранения: бот не может надёжно определить несохранённые документы "
        "или состояние приложений. Сохрани важную работу вручную перед подтверждением."
    )
