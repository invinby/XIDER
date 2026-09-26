"""Telegram-бот XGENT: команды администратора -> MQTT -> клиенты."""

# Локальный запуск по умолчанию идёт через безопасный launcher. Серверный
# процесс должен явно использовать ``--serve``.
if __name__ == "__main__":
    import sys
    if "--serve" not in sys.argv:
        from launcher import main as launch
        raise SystemExit(launch())

import asyncio
import base64
import contextvars
import datetime
import html
import io
import logging
import os
import re
import socket
import sys
import threading
import time
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# --- Устойчивость DNS для Telegram API (обход проблемных IP и таймаутов провайдеров) ---
_orig_getaddrinfo = socket.getaddrinfo

def _telegram_dns_fallback(host, port, family=0, type=0, proto=0, flags=0):
    if host == "api.telegram.org":
        # Проверенные рабочие IP-адреса Telegram API
        working_ips = ["149.154.167.99", "149.154.167.220", "149.154.167.50"]
        results = []
        for ip in working_ips:
            results.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)))
        try:
            dns_results = _orig_getaddrinfo(host, port, family, type, proto, flags)
            for r in dns_results:
                if r not in results:
                    results.append(r)
        except Exception:
            pass
        return results
    return _orig_getaddrinfo(host, port, family, type, proto, flags)

socket.getaddrinfo = _telegram_dns_fallback

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

import bot_settings
import access_store
import text_store
from config import ADMIN_ID, BOT_TOKEN, ENCRYPT_PAYLOAD, MQTT_BROKER, MQTT_PORT, MQTT_PREFIX
from roles import AdminFilter, AnyAccessFilter, OwnerFilter, ReadOnlyFilter, Role, get_user_role
from crypto import verify_message
from device_store import DeviceStore
from transport import MQTTTransport
from wol import send_wol
from xgencrypto import decrypt_payload
from version import BUILD_CODE, BUILD_DATE, VERSION
import server_ops

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("xgent.bot")

# --- Audit-лог: отдельный rotating-файл, без секретов (redact) ---
_audit_handler = RotatingFileHandler(
    Path(__file__).resolve().parent / "audit.log",
    maxBytes=1_000_000,
    backupCount=3,
    encoding="utf-8",
)
_audit_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s %(message)s")
)
audit_log = logging.getLogger("xgent.audit")
audit_log.setLevel(logging.INFO)
audit_log.propagate = False
audit_log.addHandler(_audit_handler)


def audit(event: str, **fields) -> None:
    """Запись в audit.log. Значения-секреты вырезаются (redact)."""
    BLOCK = ("token", "password", "secret", "key", "bot_token", "admin_id")
    clean = {}
    for k, v in fields.items():
        if k.lower() in BLOCK:
            clean[k] = "[REDACTED]"
        else:
            clean[k] = v
    audit_log.info("%s | %s", event, " | ".join(f"{k}={v}" for k, v in clean.items()))


# =====================================================================
#  Фильтры и FSM
# =====================================================================


class Form(StatesGroup):
    wait_url = State()
    wait_text = State()
    wait_sound = State()
    wait_shell = State()
    wait_open_app = State()
    wait_rename = State()
    wait_clipset = State()
    wait_vol = State()
    wait_manual_add = State()
    wait_admin = State()
    wait_fileop = State()
    wait_fileput_path = State()
    wait_fileput_doc = State()
    wait_path = State()
    wait_find = State()
    wait_fun_text = State()
    wait_fun_hotkey = State()
    wait_fun_wallpaper = State()
    wait_fun_spam = State()
    wait_shout = State()
    wait_prockill = State()
    wait_brightness = State()
    wait_admin_message = State()
    wait_bot_text = State()
    wait_server_command = State()


# Диспетчер и роутер создаются ДО декораторов хендлеров (иначе NameError при импорте).
dp = Dispatcher()
router = Router()
dp.include_router(router)

CURRENT_TG_USER: contextvars.ContextVar[int] = contextvars.ContextVar(
    "xider_current_tg_user", default=ADMIN_ID
)


class SessionRegistry(dict):
    """Keep the selected device isolated per Telegram user.

    Older XIDER used one global ``SESSION['target']``.  That could send a
    command from one operator to a device selected by another operator.  The
    existing call sites keep working, while the target is now per account.
    """

    def __init__(self) -> None:
        super().__init__()
        self._targets: dict[int, str | None] = {}

    def _user_id(self) -> int:
        return int(CURRENT_TG_USER.get())

    def __getitem__(self, key):
        if key == "target":
            return self._targets.get(self._user_id())
        return super().__getitem__(key)

    def __setitem__(self, key, value):
        if key == "target":
            self._targets[self._user_id()] = value
            return
        return super().__setitem__(key, value)

    def get(self, key, default=None):
        if key == "target":
            return self._targets.get(self._user_id(), default)
        return super().get(key, default)

@dp.update.outer_middleware()
async def _live_log_middleware(handler, event, data):
    user = None
    kind = "update"
    detail = ""
    if event.message:
        m = event.message
        user = m.from_user
        kind = "telegram_message"
        detail = m.text or f"<{m.content_type}>"
    elif event.callback_query:
        cq = event.callback_query
        user = cq.from_user
        kind = "telegram_callback"
        detail = cq.data or ""
    token = CURRENT_TG_USER.set(int(user.id) if user else ADMIN_ID)
    try:
        if user:
            tag = f"@{user.username}" if user.username else f"id:{user.id}"
            log.info("[TG %s] %s: %s", kind, tag, detail)
            access_store.touch_user(user, kind, detail)
        return await handler(event, data)
    finally:
        CURRENT_TG_USER.reset(token)


@router.callback_query(AdminFilter(), F.data == "cmd:clipboard")
async def on_cmd_clipboard(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    await cq.answer("📋 Запрашиваю буфер обмена...")
    sent, command_id = publish_tracked("clipboard")
    if not sent:
        await _replace_callback_message(cq,
            "⚠️ Нет соединения с MQTT-брокером.",
            reply_markup=back_to_device_kb(),
        )
        return
    result = await clipboard_collector.wait_for(target, "clipboard", 10.0, command_id)
    if result is None:
        await _replace_callback_message(cq,
            "⏳ Ответ не получен за 10 сек — устройство офлайн.",
            reply_markup=back_to_device_kb(),
        )
        return
    text = result.get("text")
    if not text:
        await _replace_callback_message(cq,
            "⚠️ Буфер обмена пуст или недоступен.",
            reply_markup=back_to_device_kb(),
        )
    else:
        # Экранируем текст для безопасного вывода
        safe_text = html.escape(text)
        await _replace_callback_message(cq,
            f"📋 <b>Буфер обмена:</b>\n<pre>{safe_text}</pre>",
            reply_markup=back_to_device_kb(),
        )


@router.callback_query(AdminFilter(), F.data == "cmd:processes")
async def on_cmd_processes(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    await cq.answer("⚙️ Запрашиваю процессы (Топ-5)...")
    sent, command_id = publish_tracked("processes")
    if not sent:
        await _replace_callback_message(cq,
            "⚠️ Нет соединения с MQTT-брокером.",
            reply_markup=back_to_device_kb(),
        )
        return
    result = await processes_collector.wait_for(target, "processes", 15.0, command_id)
    if result is None:
        await _replace_callback_message(cq,
            "⏳ Ответ не получен за 15 сек — устройство офлайн.",
            reply_markup=back_to_device_kb(),
        )
        return
    lines = result.get("lines", [])
    if not lines:
        await _replace_callback_message(cq,
            "⚠️ Не удалось получить список процессов.",
            reply_markup=back_to_device_kb(),
        )
    else:
        text = "\n".join(html.escape(line) for line in lines)
        await _replace_callback_message(cq,
            f"⚙️ <b>Топ-5 процессов по памяти:</b>\n<pre>{text}</pre>",
            reply_markup=back_to_device_kb(),
        )


@router.callback_query(AdminFilter(), F.data == "cmd:mic")
async def on_cmd_mic(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    await cq.answer("🎙 Идет запись звука (5 сек)...")
    sent, command_id = publish_tracked("mic")
    if not sent:
        await _replace_callback_message(cq,
            "⚠️ Нет соединения с MQTT-брокером.",
            reply_markup=back_to_device_kb(),
        )
        return
    result = await mic_collector.wait_for(target, "mic", 20.0, command_id)
    if result is None:
        await _replace_callback_message(cq,
            "⏳ Звук не получен за 20 сек — устройство офлайн или нет микрофона.",
            reply_markup=back_to_device_kb(),
        )
        return
    audio_b64 = result.get("audio")
    if not audio_b64:
        await _replace_callback_message(cq,
            "⚠️ Устройство ответило ошибкой или микрофон недоступен.",
            reply_markup=back_to_device_kb(),
        )
        return
    try:
        audio_bytes = base64.b64decode(audio_b64)
        audio_file = BufferedInputFile(audio_bytes, filename="mic.ogg")
        await bot.send_voice(
            cq.from_user.id,
            voice=audio_file,
            caption=f"🎙 <b>{target_label(target)}</b>",
            reply_markup=back_to_device_kb(),
        )
    except Exception:
        log.exception("Ошибка декодирования аудио")
        await _replace_callback_message(cq,
            "⚠️ Ошибка обработки звука.",
            reply_markup=back_to_device_kb(),
        )


@router.callback_query(AdminFilter(), F.data == "cmd:shell")
async def on_cmd_shell(cq: CallbackQuery, state: FSMContext):
    if not SESSION.get("target"):
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    await state.set_state(Form.wait_shell)
    await cq.message.answer(
        "💻 Отправьте команду терминала, которую нужно выполнить.\n"
        "Например: <code>whoami</code> или <code>ls -la</code>",
        reply_markup=back_to_device_kb(),
    )
    await cq.answer()


@router.message(AdminFilter(), Form.wait_shell)
async def on_shell_input(message: Message, state: FSMContext):
    await state.clear()
    cmd = (message.text or "").strip()
    if not cmd:
        await message.answer("⚠️ Пустая команда.", reply_markup=back_to_device_kb())
        return
    target = SESSION.get("target")
    
    await message.answer(f"💻 Выполняю команду: <code>{cmd}</code>...")
    sent, command_id = publish_tracked("shell", command=cmd)
    if not sent:
        await message.answer(
            "⚠️ Нет соединения с MQTT-брокером.",
            reply_markup=back_to_device_kb(),
        )
        return
        
    result = await shell_collector.wait_for(target, "shell", 20.0, command_id)
    if result is None:
        await message.answer(
            "⏳ Ответ не получен за 20 сек — устройство зависло или офлайн.",
            reply_markup=back_to_device_kb(),
        )
        return
        
    output = result.get("output", "")
    safe_out = html.escape(output)
    if len(safe_out) > 3800:
        safe_out = safe_out[:3800] + "\n...[ОБРЕЗАНО]"
        
    await message.answer(
        f"💻 <b>Результат:</b>\n<pre>{safe_out}</pre>",
        reply_markup=back_to_device_kb(),
    )


@router.callback_query(AdminFilter(), F.data == "cmd:open_app")
async def on_cmd_open_app(cq: CallbackQuery, state: FSMContext):
    if not SESSION.get("target"):
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    await state.set_state(Form.wait_open_app)
    await cq.message.answer(
        "🚀 Отправьте название программы или путь к файлу для запуска.\n"
        "Например: <code>calc</code> или <code>notepad</code>",
        reply_markup=back_to_device_kb(),
    )
    await cq.answer()


@router.message(AdminFilter(), Form.wait_open_app)
async def on_open_app_input(message: Message, state: FSMContext):
    await state.clear()
    app = (message.text or "").strip()
    if not app:
        await message.answer("⚠️ Пустая команда.", reply_markup=back_to_device_kb())
        return
    if publish("open_app", app=app):
        await message.answer(
            f"✅ Программа <b>{app}</b> запущена на <b>{target_label(SESSION['target'])}</b>.",
            reply_markup=back_to_device_kb(),
        )
    else:
        await message.answer(
            "⚠️ Нет соединения с MQTT-брокером.",
            reply_markup=back_to_device_kb(),
        )


# =====================================================================
#  Хранилища
# =====================================================================

class ResponseCollector:
    """Ожидание ответа от устройства (статус, скриншот, sysinfo, текст)."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._data: dict | None = None
        self._waiters: dict[str, list] = {}
        self._recent: dict[tuple[str, str, str], tuple[float, dict]] = {}
        self._recent_ttl = 30.0

    def reset(self) -> None:
        self._event.clear()
        self._data = None

    def submit(self, device_id: str, info: dict) -> None:
        data = {"device_id": device_id, **info}
        self._data = data
        self._event.set()
        action_type = info.get("type", "")
        command_id = str(info.get("id") or "")
        if command_id:
            self._recent[(device_id, action_type, command_id)] = (time.monotonic(), data)
            cutoff = time.monotonic() - self._recent_ttl
            self._recent = {k: v for k, v in self._recent.items() if v[0] >= cutoff}
        loop = LOOP
        if loop and not loop.is_closed():
            keys = []
            if command_id:
                keys.append(f"{device_id}:{action_type}:{command_id}")
            keys.extend([f"{device_id}:{action_type}", f"{device_id}", f"*:{action_type}", "*"])
            for k in keys:
                waiters = self._waiters.get(k, [])
                while waiters:
                    fut = waiters.pop(0)
                    if not fut.done():
                        loop.call_soon_threadsafe(fut.set_result, data)

    async def wait(self, timeout: float = 12.0) -> dict | None:
        await asyncio.to_thread(self._event.wait, timeout)
        return self._data if self._event.is_set() else None

    async def wait_for(self, device_id: str = "*", action_type: str = "*", timeout: float = 12.0, command_id: str | None = None) -> dict | None:
        """Тикетное/точечное ожидание конкретного ответа от устройства без race condition."""
        if command_id:
            cached = self._recent.get((device_id, action_type, str(command_id)))
            if cached and time.monotonic() - cached[0] <= self._recent_ttl:
                return cached[1]
        key = f"{device_id}:{action_type}:{command_id}" if command_id else f"{device_id}:{action_type}"
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._waiters.setdefault(key, []).append(fut)
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            waiters = self._waiters.get(key, [])
            if fut in waiters:
                waiters.remove(fut)


class MultiCollector:
    """Собирает ответы от НЕСКОЛЬКИХ устройств за фиксированное окно времени."""

    def __init__(self) -> None:
        self._items: list[dict] = []
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._items.clear()

    def submit(self, device_id: str, info: dict) -> None:
        with self._lock:
            self._items.append({"device_id": device_id, **info})

    async def wait_window(self, timeout: float = 8.0) -> list[dict]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            await asyncio.sleep(0.2)
        with self._lock:
            return list(self._items)


# =====================================================================
#  Клавиатуры
# =====================================================================

ACTION_LABELS = {
    "screenshot": "📸 Скриншот",
    "webcam": "📷 Вебка",
    "mic": "🎙 Микрофон",
    "processes": "⚙️ Процессы",
    "disks": "🗂 Диски",
    "tts": "🗣 Синтез речи",
    "geo_location": "📍 Локация (IP)",
    "battery": "🔋 Батарея",
    "network": "🌐 Сеть",
    "services": "🛠 Службы",
    "sysinfo": "💻 Инфо о системе",
    "capabilities": "📊 Возможности",
    "clipboard": "📋 Прочитать буфер",
    "clipboard_set": "📥 Записать в буфер",
    "shell": "💻 Терминал",
    "open_app": "🚀 Запуск программы",
    "open_url": "🔗 Открыть ссылку",
    "notify": "📝 Показать текст",
    "sound": "🔊 Озвучить",
    "lock": "🔒 Заблокировать",
    "volume_toggle": "🔇 Mute/Unmute",
    "volume_set": "🎚 Громкость",
    "status_request": "ℹ️ Статус",
    "dir_list": "📂 Открыть папку",
    "file_get": "📥 Скачать файл",
    "file_put": "📤 Отправить файл",
    "file_del": "🗑 Удалить файл",
    "find_file": "🔎 Найти файл",
    "path_open": "🎯 Открыть путь",
    "ext_ip": "🌍 Внешний IP",
    "screen_off": "🧯 Погасить экран",
    "screensaver_on": "🖥 Заставка",
    "wallpaper_set": "🖼 Сменить обои",
    "msgbox_spam": "💬 Спам окнами",
    "type_text": "⌨️ Напечатать текст",
    "hotkey": "⌘ Горячая клавиша",
}

CALLBACK_BY_ACTION = {
    "screenshot": "cmd:screenshot",
    "webcam": "cmd:webcam",
    "mic": "mic:opts",
    "processes": "proc:0",
    "disks": "cmd:disks",
    "battery": "cmd:battery",
    "network": "cmd:network",
    "tts": "cmd:tts",
    "geo_location": "cmd:geo_location",
    "services": "cmd:services",
    "sysinfo": "cmd:sysinfo",
    "capabilities": "cmd:capabilities",
    "clipboard": "cmd:clipboard",
    "clipboard_set": "cmd:clipset",
    "shell": "cfm:shell",
    "open_app": "cmd:open_app",
    "open_url": "cmd:url",
    "notify": "cmd:text",
    "sound": "cmd:sound",
    "lock": "cmd:lock",
    "volume_toggle": "cmd:volume",
    "volume_set": "vol:opts",
    "status_request": "cmd:status",
    "dir_list": "cmd:dirlist",
    "file_get": "cmd:fileget",
    "file_put": "cmd:fileput",
    "file_del": "cmd:filedel",
    "find_file": "cmd:find",
    "path_open": "cmd:openpath",
    "ext_ip": "cmd:extip",
    "screen_off": "cmd:screenoff",
    "screensaver_on": "cmd:saveron",
    "wallpaper_set": "cmd:wallpaper",
    "msgbox_spam": "cmd:spam",
    "type_text": "cmd:typetxt",
    "hotkey": "cmd:hotkey",
}
FAVORITABLE = sorted(ACTION_LABELS)


def _status_dot(info: dict | None) -> str:
    """🟢 если онлайн, 🟡 если спит (standby), ⚪ если оффлайн."""
    if not info:
        return "⚪"
    ago = time.time() - float(info.get("last_seen", 0) or 0)
    # Устройство считается свежим в тот же момент, что и watchdog.
    # Раньше UI переключался в офлайн через 120 с, а watchdog через 150 с,
    # из-за чего при нормальном heartbeat в 60 с появлялось мигание статуса.
    freshness = _OFFLINE_TIMEOUT_SEC
    online = bool(info.get("online", True))
    if info.get("standby", False) and online and ago < freshness:
        return "🟡"
    return "🟢" if online and ago < freshness else "⚪"


def device_card(device_id: str) -> str:
    if device_id == "all":
        count = sum(1 for d in devices.all().values() if _status_dot(d) in ("🟢", "🟡"))
        total = len(devices.all())
        return (
            "🌐 <b>УПРАВЛЕНИЕ ВСЕМИ УСТРОЙСТВАМИ</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• В сети: <b>{count}</b> из <b>{total}</b>\n"
            "• Режим: Синхронная трансляция команд на все агенты\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "<i>Выберите категорию или действие:</i>"
        )
    info = devices.get(device_id) or {}
    name = html.escape(info.get("name") or device_id)
    online_dot = _status_dot(info)
    is_online = online_dot in ("🟢", "🟡")
    if online_dot == "🟢":
        status_str = "В сети"
    elif online_dot == "🟡":
        status_str = "Спит (Standby)"
    else:
        status_str = "Оффлайн"
    os_str = html.escape(info.get("os", "Неизвестно"))
    ver = html.escape(info.get("version", "1.0"))
    last_seen = info.get("last_seen")
    if last_seen and is_online:
        diff = max(0, int(time.time() - float(last_seen)))
        if diff < 5:
            seen_str = "прямо сейчас"
        elif diff < 60:
            seen_str = f"{diff} сек назад"
        elif diff < 3600:
            seen_str = f"{diff // 60} мин назад"
        else:
            seen_str = f"{diff // 3600} ч назад"
    else:
        seen_str = "не в сети"

    os_low = os_str.lower()
    os_icon = "🍏" if ("mac" in os_low or "darwin" in os_low) else ("🪟" if "win" in os_low else "🐧")

    return (
        f"{os_icon} <b>{name}</b> {online_dot} <code>[{status_str}]</code>\n"
        f"<code>ID: {device_id}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>ОС:</b> {os_str}\n"
        f"• <b>Агент:</b> v{ver}  •  <b>Пинг:</b> {seen_str}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Выберите категорию:</i>"
    )


# ====== XIDER System Constants ======
XIDER_VERSION = VERSION
XIDER_BUILD   = BUILD_DATE
XIDER_BUILD_CODE = BUILD_CODE
XIDER_AUTHOR  = bot_settings.developer_contact()
# =====================================



def get_server_autostart_status() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, "XIDER_BOT")
            return True
    except Exception:
        return False


def toggle_server_autostart() -> tuple[bool, str]:
    if sys.platform != "win32":
        return False, "Автозапуск поддерживается только на Windows."
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        currently_enabled = get_server_autostart_status()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS) as key:
            if not currently_enabled:
                cur_dir = Path(__file__).resolve().parent
                bat = cur_dir / "start_bot.bat"
                cmd = f'"{bat}"'
                winreg.SetValueEx(key, "XIDER_BOT", 0, winreg.REG_SZ, cmd)
                return True, "✅ Автозапуск сервера XIDER включен в реестре Windows!"
            else:
                try:
                    winreg.DeleteValue(key, "XIDER_BOT")
                except FileNotFoundError:
                    pass
                return False, "🛑 Автозапуск сервера XIDER отключен в реестре Windows."
    except Exception as exc:
        return False, f"⚠️ Ошибка реестра: {exc}"


def _technical_ui() -> bool:
    return bot_settings.get("ui_style", "technical") == "technical"


def _custom_ui() -> bool:
    return bot_settings.get("ui_style", "technical") == "custom"


def _ui_phrase(technical: str, conversational: str, custom: str) -> str:
    style = str(bot_settings.get("ui_style", "technical"))
    if style == "custom":
        return custom
    return technical if style == "technical" else conversational


def main_menu(user_id: int | None = None):
    user_id = int(user_id if user_id is not None else CURRENT_TG_USER.get())
    role = get_user_role(user_id)
    kb = InlineKeyboardBuilder()
    if role in (Role.OWNER, Role.COOWNER):
        kb.button(text=_ui_phrase("Устройства", "💻 Список устройств", "🧰 Мои машинки"), callback_data="menu:devices", style="primary")
        kb.button(text=_ui_phrase("Все устройства", "🌐 Все устройства", "🌍 Весь зоопарк"), callback_data="dev:all", style="primary")
        kb.button(text="Серверная", callback_data="menu:server", style="primary")
        kb.button(text=_ui_phrase("События и настройки", "🔔 Настройки & События", "🔔 Шум и настройки"), callback_data="ev:menu", style="primary")
    elif role == Role.USER:
        kb.button(text="Мои устройства", callback_data="menu:devices", style="primary")
        kb.button(text="Серверная: обзор", callback_data="menu:server", style="primary")
    else:
        kb.button(text="Обзор устройств", callback_data="menu:guest_devices", style="primary")
        kb.button(text="Серверная: обзор", callback_data="menu:server", style="primary")
    kb.button(text=_ui_phrase("О системе", "ℹ️ О системе XIDER", "🤖 Что за зверь XIDER"), callback_data="menu:about", style="primary")
    if role == Role.OWNER:
        kb.button(text="Администрирование", callback_data="menu:admin", style="danger")
    kb.adjust(1)
    return kb.as_markup()


def devices_menu():
    kb = InlineKeyboardBuilder()
    devs = devices.all()
    for device_id, info in sorted(devs.items()):
        name = info.get("name") or device_id
        dot = _status_dot(info)
        is_online = dot == "🟢"
        os_name = info.get("os", "").lower()
        if "mac" in os_name or "darwin" in os_name:
            icon = "🍏"
        elif "win" in os_name:
            icon = "🪟"
        elif "linux" in os_name:
            icon = "🐧"
        else:
            icon = "💻"
        kb.button(
            text=f"{dot} {icon} {name}",
            callback_data=f"dev:{device_id}",
            style="success" if is_online else "danger",
        )
    kb.button(text="➕ Добавить", callback_data="devmg:manualadd", style="primary")
    kb.button(text="🚫 Чёрный список", callback_data="devmg:blocked", style="danger")
    kb.button(text="🧹 Очистить список", callback_data="devmg:clear_all", style="danger")
    kb.button(text="🔄 Обновить", callback_data="menu:devices", style="success")
    kb.button(text="🏠 Главное меню", callback_data="menu:main", style="primary")
    dev_count = len(devs)
    adjust_pattern = [1] * dev_count + [2, 1, 2]
    kb.adjust(*adjust_pattern)
    return kb.as_markup()


def device_menu(device_id: str):
    if device_id == "all":
        return all_menu()
    kb = InlineKeyboardBuilder()

    # 9 Distinct Detailed Categories with Vibrant Colors!
    kb.button(text="📸 Медиа & Зрение", callback_data="cat:media", style="success")
    kb.button(text="🖥 Экран & Дисплей", callback_data="cat:screen", style="primary")

    kb.button(text="⌨️ Ввод & Мышь", callback_data="cat:input", style="primary")
    kb.button(text="📊 Система & Сенсоры", callback_data="cat:system", style="primary")

    kb.button(text="🌐 Сеть & Коннект", callback_data="cat:network", style="primary")
    kb.button(text="📁 Файлы & Диски", callback_data="cat:files", style="primary")

    kb.button(text="🛠 Терминал & Софт", callback_data="cat:terminal", style="primary")
    kb.button(text="🔒 Питание & Защита", callback_data="cat:power", style="danger")

    kb.button(text="🎭 Приколы & Розыгрыши", callback_data="cat:pranks", style="success")
    kb.button(text="⚙️ Настройки ПК", callback_data="cat:device", style="primary")

    # Favorite actions (if configured)
    favs = devices.get_favorites(device_id)[:4]
    for action in favs:
        kb.button(
            text=f"⭐ {ACTION_LABELS.get(action, action)}",
            callback_data=CALLBACK_BY_ACTION.get(action, "noop"),
            style="primary",
        )

    # Критичные действия должны быть доступны прямо из карточки устройства,
    # а не спрятаны только внутри «Настройки ПК».
    kb.button(text="🔄 Обновить агента", callback_data="cmd:check_update", style="success")
    kb.button(text="⏹ Остановить агента", callback_data="cfm:stop", style="danger")

    # Bottom row: Back
    kb.button(text="🔙 К списку устройств", callback_data="menu:target", style="danger")

    rows = [2, 2, 2, 2, 2]
    if favs:
        rows.extend([2] * (len(favs) // 2))
        if len(favs) % 2:
            rows.append(1)
    rows.extend([2, 1])
    kb.adjust(*rows)
    return kb.as_markup()


def all_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="📊 Сводка статусов", callback_data="all:status", style="primary")
    kb.button(text="📸 Скриншоты со всех", callback_data="all:screenshot", style="success")
    kb.button(text="📍 Локация (IP)", callback_data="cmd:geo_location", style="success")
    kb.button(text="🔒 Заблокировать все", callback_data="cmd:lock", style="danger")
    kb.button(text="🔇 Mute/Unmute звук", callback_data="cmd:volume", style="primary")
    kb.button(text="⏹ Остановить все клиенты", callback_data="cfm:stop_all", style="danger")
    kb.button(text="🔙 К списку устройств", callback_data="menu:target", style="primary")
    kb.button(text="🏠 Главное меню", callback_data="menu:main", style="primary")
    kb.adjust(2, 2, 1, 2)
    return kb.as_markup()


def back_to_device_kb():
    """Маленькая клавиатура «Назад» — появляется после каждой команды."""
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ К устройству", callback_data="back:device", style="primary")
    kb.button(text="🏠 Главное меню", callback_data="menu:main", style="primary")
    kb.adjust(2)
    return kb.as_markup()


def power_menu():
    """Подменю подтверждения выключения / перезагрузки."""
    kb = InlineKeyboardBuilder()
    kb.button(text="🔄 Перезагрузить", callback_data="power:reboot", style="danger")
    kb.button(text="⚡ Выключить", callback_data="power:shutdown", style="danger")
    kb.button(text="🔙 Назад", callback_data="back:device", style="primary")
    kb.adjust(2, 1)
    return kb.as_markup()


def confirm_power_menu(action: str):
    """Подтверждение опасного действия."""
    labels = {
        "shutdown": "ВЫКЛЮЧИТЬ",
        "reboot": "ПЕРЕЗАГРУЗИТЬ",
        "sleep": "ОТПРАВИТЬ В СОН",
    }
    label = labels.get(action, action.upper())
    kb = InlineKeyboardBuilder()
    kb.button(text=f"✅ Да, {label}!", callback_data=f"power_confirm:{action}", style="danger")
    kb.button(text="❌ Отмена", callback_data="back:device", style="primary")
    kb.adjust(1, 1)
    return kb.as_markup()


def rotate_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="⬆️ 0° (Стандарт)", callback_data="rotate:0", style="success")
    kb.button(text="➡️ 90° (Вправо)", callback_data="rotate:90", style="primary")
    kb.button(text="⬇️ 180° (Вверх дном)", callback_data="rotate:180", style="primary")
    kb.button(text="⬅️ 270° (Влево)", callback_data="rotate:270", style="primary")
    kb.button(text="⬅️ К дисплею", callback_data="cat:screen", style="danger")
    kb.button(text="🏠 Главное меню", callback_data="menu:main", style="primary")
    kb.adjust(2, 2, 2)
    return kb.as_markup()


def media_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="📸 Скриншот экрана", callback_data="cmd:screenshot", style="success")
    kb.button(text="📍 Локация (IP)", callback_data="cmd:geo_location", style="success")
    kb.button(text="📷 Снимок с вебки", callback_data="cmd:webcam", style="success")
    kb.button(text="🎙 Микрофон (запись)", callback_data="mic:opts", style="success")
    kb.button(text="🔊 Озвучить текст", callback_data="cmd:sound", style="primary")
    kb.button(text="🎚 Уровень громкости", callback_data="vol:opts", style="primary")
    kb.button(text="🔇 Mute / Unmute", callback_data="cmd:volume", style="primary")
    kb.button(text="⬅️ Назад к ПК", callback_data="back:device", style="danger")
    kb.button(text="🏠 Главное меню", callback_data="menu:main", style="primary")
    kb.adjust(2, 2, 2, 2)
    return kb.as_markup()


def screen_menu(target: str | None = None):
    target = target or SESSION.get("target") or ""
    is_nl = bool(SESSION.get(f"nightlight_{target}", False))
    kb = InlineKeyboardBuilder()
    kb.button(text="🧯 Погасить дисплей", callback_data="fun:screenoff", style="danger")
    kb.button(text="💤 Включить заставку", callback_data="fun:screensaver", style="primary")
    kb.button(text="🖼 Обои рабочего стола", callback_data="menu:wallpaper", style="success")
    kb.button(text="☀️ Яркость экрана", callback_data="cmd:brightness", style="success")
    if is_nl:
        kb.button(text="🔴 [ВКЛ] Ночной свет", callback_data="cmd:nightlight", style="danger")
    else:
        kb.button(text="🌙 Ночной свет", callback_data="cmd:nightlight", style="primary")
    kb.button(text="🔄 Переворот экрана", callback_data="cmd:rotate", style="primary")
    kb.button(text="⬅️ Назад к ПК", callback_data="back:device", style="danger")
    kb.button(text="🏠 Главное меню", callback_data="menu:main", style="primary")
    kb.adjust(2, 2, 2, 2)
    return kb.as_markup()


def wallpaper_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="🎲 Случайный мем из сети", callback_data="cmd:wallpaper_random_meme", style="success")
    kb.button(text="📸 Загрузить фото из чата", callback_data="cmd:wallpaper_photo_guide", style="primary")
    kb.button(text="🌐 Ввести ссылку (URL)", callback_data="fun:wallpaper", style="primary")
    kb.button(text="🔄 Восстановить прежние обои", callback_data="cmd:prank_restore_wallpaper", style="danger")
    kb.button(text="⬅️ К дисплею", callback_data="cat:screen", style="primary")
    kb.button(text="🏠 Главное меню", callback_data="menu:main", style="primary")
    kb.adjust(1, 1, 1, 1, 2)
    return kb.as_markup()


def input_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="📋 Буфер (прочитать)", callback_data="cmd:clipboard", style="success")
    kb.button(text="📥 Буфер (вставить)", callback_data="cmd:clipset", style="primary")
    kb.button(text="⌨️ Напечатать текст", callback_data="fun:type", style="primary")
    kb.button(text="⌘ Нажать клавиши", callback_data="fun:hotkey", style="primary")
    kb.button(text="🖱 Инверсия мыши", callback_data="prank:swapmouse", style="primary")
    kb.button(text="🌀 Пьяный курсор", callback_data="prank:crazycursor", style="primary")
    kb.button(text="⬅️ Назад к ПК", callback_data="back:device", style="danger")
    kb.button(text="🏠 Главное меню", callback_data="menu:main", style="primary")
    kb.adjust(2, 2, 2, 2)
    return kb.as_markup()


def system_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="💻 Инфо о системе", callback_data="cmd:sysinfo", style="primary")
    kb.button(text="⏱ Аптайм системы", callback_data="cmd:sys_uptime", style="success")
    kb.button(text="🔋 Заряд батареи", callback_data="cmd:battery", style="success")
    kb.button(text="🗂 Диски и память", callback_data="cmd:disks", style="primary")
    kb.button(text="🩺 SMART дисков", callback_data="cmd:smart", style="success")
    kb.button(text="🧹 Очистить TEMP кэш", callback_data="cmd:sys_clean_temp", style="danger")
    kb.button(text="📦 Установленный софт", callback_data="cmd:apps", style="primary")
    kb.button(text="📊 Возможности", callback_data="cmd:capabilities", style="primary")
    kb.button(text="🔄 Обновить статус", callback_data="cmd:status", style="success")
    kb.button(text="⬅️ Назад к ПК", callback_data="back:device", style="danger")
    kb.adjust(2, 2, 2, 2, 1, 1)
    return kb.as_markup()


def network_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="🌍 Внешний IP адрес", callback_data="fun:extip", style="success")
    kb.button(text="🏓 Ping узла", callback_data="cmd:net_ping", style="success")
    kb.button(text="📡 Сеть и адаптеры", callback_data="cmd:network", style="primary")
    kb.button(text="📶 Wi-Fi сети и пароли", callback_data="cmd:wifi", style="primary")
    kb.button(text="🔌 USB-устройства", callback_data="cmd:usb", style="primary")
    kb.button(text="🔵 Bluetooth устройства", callback_data="cmd:bluetooth", style="primary")
    kb.button(text="🔗 Порты (Netstat)", callback_data="cmd:netstat", style="primary")
    kb.button(text="⬅️ Назад к ПК", callback_data="back:device", style="danger")
    kb.button(text="🏠 Главное меню", callback_data="menu:main", style="primary")
    kb.adjust(2, 2, 2, 1, 2)
    return kb.as_markup()


def files_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="📂 Список файлов", callback_data="files:list", style="primary")
    kb.button(text="🔍 Найти файл", callback_data="files:find", style="primary")
    kb.button(text="📥 Скачать с ПК", callback_data="files:get", style="success")
    kb.button(text="📤 Загрузить на ПК", callback_data="files:put", style="success")
    kb.button(text="🖥 Открыть путь на ПК", callback_data="files:open", style="primary")
    kb.button(text="🗑 Удалить файл", callback_data="files:del", style="danger")
    kb.button(text="⬅️ Назад к ПК", callback_data="back:device", style="danger")
    kb.button(text="🏠 Главное меню", callback_data="menu:main", style="primary")
    kb.adjust(2, 2, 2, 2)
    return kb.as_markup()


def terminal_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="⚙️ Диспетчер процессов", callback_data="proc:0", style="primary")
    kb.button(text="🔪 Убить процесс", callback_data="cmd:prockillname", style="danger")
    kb.button(text="💻 Терминал (Shell)", callback_data="cfm:shell", style="danger")
    kb.button(text="🚀 Запуск программы", callback_data="cmd:open_app", style="primary")
    kb.button(text="🚀 Список автозагрузки", callback_data="cmd:startup", style="primary")
    kb.button(text="🛠 Фоновые службы", callback_data="cmd:services", style="primary")
    kb.button(text="🕘 История консоли", callback_data="cmd:cmdhistory", style="primary")
    kb.button(text="⬅️ Назад к ПК", callback_data="back:device", style="danger")
    kb.adjust(2, 2, 2, 1, 1)
    return kb.as_markup()


# Псевдонимы обратной совместимости
control_menu = input_menu
fun_menu = terminal_menu


def power_menu_new():
    kb = InlineKeyboardBuilder()
    target = SESSION.get("target") or ""
    dev_info = devices.get(target) or {}
    is_sleeping = dev_info.get("standby", False)
    if is_sleeping:
        kb.button(text="☀️ Пробудить агента", callback_data="cmd:wake", style="success")
    else:
        kb.button(text="💤 Усыпить агента (Standby)", callback_data="cmd:standby_sleep", style="primary")

    kb.button(text="🔒 Заблокировать экран", callback_data="cmd:lock", style="danger")
    kb.button(text="😴 Усыпить ПК (Sleep)", callback_data="power:sleep", style="danger")
    kb.button(text="🔄 Перезагрузить ПК", callback_data="power:reboot", style="danger")
    kb.button(text="⚡ Выключить ПК", callback_data="power:shutdown", style="danger")
    kb.button(text="🚀 Автозапуск: Статус", callback_data="cmd:autorun_status", style="primary")
    kb.button(text="✅ Вкл автозапуск", callback_data="cmd:autorun_enable", style="success")
    kb.button(text="🛑 Выкл автозапуск", callback_data="cmd:autorun_disable", style="danger")
    kb.button(text="🛡 Guardian", callback_data="cmd:guardian_menu", style="primary")
    kb.button(text="🌐 Wake-on-LAN", callback_data="cmd:wol", style="primary")
    kb.button(text="⏹ Стоп процесса агента", callback_data="cfm:stop", style="danger")
    kb.button(text="⬅️ Назад к ПК", callback_data="back:device", style="primary")
    kb.button(text="🏠 Главное меню", callback_data="menu:main", style="primary")
    kb.adjust(1, 2, 2, 1, 2, 2, 2, 1)
    return kb.as_markup()


def guardian_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="📊 Статус Guardian", callback_data="cmd:guardian_status", style="primary")
    kb.button(text="▶️ Запустить агента", callback_data="cmd:guardian_start", style="success")
    kb.button(text="⏹ Остановить агента", callback_data="cmd:guardian_stop", style="danger")
    kb.button(text="🔄 Перезапустить агента", callback_data="cmd:guardian_restart", style="primary")
    kb.button(text="🛡 Вкл. автовосстановление", callback_data="cmd:guardian_auto_on", style="success")
    kb.button(text="⏸ Выкл. автовосстановление", callback_data="cmd:guardian_auto_off", style="danger")
    kb.button(text="⬅️ Назад к питанию", callback_data="cat:power", style="primary")
    kb.adjust(2, 2, 2, 1)
    return kb.as_markup()


def pranks_menu(page: int = 1, target: str | None = None):
    target = target or SESSION.get("target") or ""
    mouse_swapped = bool(SESSION.get(f"mouse_swapped_{target}", False))
    desktop_hidden = bool(SESSION.get(f"desktop_hidden_{target}", False))
    kb = InlineKeyboardBuilder()

    # Главная кнопка экстренной отмены любых приколов на каждой странице
    kb.button(text="🛑 СТОП ВСЕ ПРИКОЛЫ", callback_data="prank:stop_all", style="danger")

    if page == 1:
        # Страница 1: Экраны & Визуал (10)
        kb.button(text="🎬 Скример (full)", callback_data="prank:screamer", style="danger")
        kb.button(text="🎵 Рикролл ×50", callback_data="prank:rickroll50", style="success")
        kb.button(text="🌈 Матрица (full)", callback_data="prank:matrix", style="success")
        kb.button(text="💀 Экран BSOD", callback_data="cmd:prank_bsod", style="danger")
        kb.button(text="⏳ Fake Update", callback_data="cmd:prank_fake_update", style="primary")
        kb.button(text="🙃 Инверт 180°", callback_data="cmd:prank_invert_screen", style="primary")
        if desktop_hidden:
            kb.button(text="🔴 [ВКЛ] Иконки скрыты", callback_data="prank:hidedesktop", style="danger")
        else:
            kb.button(text="🫥 Скрыть иконки", callback_data="prank:hidedesktop", style="primary")
        kb.button(text="🪟 Танцы окон", callback_data="prank:dancewin", style="primary")
        kb.button(text="⬛ Чёрный экран", callback_data="prank:blackscreen", style="danger")
        kb.button(text="🫨 Тряска окна", callback_data="cmd:prank_shake_window", style="primary")
    elif page == 2:
        # Страница 2: Звуки & Голос (10)
        kb.button(text="🔊 Сирена тревоги", callback_data="prank:siren", style="danger")
        kb.button(text="📢 Орать текстом", callback_data="prank:shout", style="success")
        kb.button(text="📻 Морзе SOS", callback_data="cmd:prank_beep_morse", style="primary")
        kb.button(text="👻 Жуткие звуки", callback_data="cmd:prank_sound_spooky", style="danger")
        kb.button(text="💨 Смешной пук", callback_data="cmd:prank_sound_fart", style="primary")
        kb.button(text="🔊 Скачки громкости", callback_data="cmd:prank_volume_jump", style="primary")
        kb.button(text="🗣 Говорящие часы", callback_data="cmd:prank_speak_time", style="success")
        kb.button(text="👂 Шёпот: Обернись", callback_data="cmd:prank_say_whisper", style="danger")
        kb.button(text="🤣 Смех ситкома", callback_data="cmd:prank_laugh_track", style="primary")
        kb.button(text="📟 Рандомные пики", callback_data="cmd:prank_random_beeps", style="primary")
    elif page == 3:
        # Страница 3: Мышь & Клавиатура (10)
        if mouse_swapped:
            kb.button(text="🔴 [ВКЛ] Инверсия мыши", callback_data="prank:swapmouse", style="danger")
        else:
            kb.button(text="🖱 Инверсия мыши", callback_data="prank:swapmouse", style="primary")
        kb.button(text="🌀 Пьяный курсор", callback_data="prank:crazycursor", style="primary")
        kb.button(text="🐌 Черепашья мышь", callback_data="cmd:prank_slow_mouse", style="primary")
        kb.button(text="🌀 Глючный курсор", callback_data="cmd:prank_glitch_cursor", style="primary")
        kb.button(text="🤹 Случайные клики", callback_data="cmd:prank_random_clicks", style="primary")
        kb.button(text="💃 Диско клавиатуры", callback_data="cmd:prank_keyboard_disco", style="success")
        kb.button(text="🚨 CapsLock Диско", callback_data="cmd:prank_caps_disco", style="primary")
        kb.button(text="⭕ Курсор по кругу", callback_data="cmd:prank_cursor_circle", style="primary")
        kb.button(text="📝 Печать в блокнот", callback_data="cmd:prank_open_notepad_type", style="primary")
        kb.button(text="🧑‍💻 Hacker Typer", callback_data="cmd:prank_hacker_typer", style="success")
    elif page == 4:
        # Страница 4: Фейки & Системный хаос (10)
        kb.button(text="💬 Спам окнами", callback_data="fun:spam", style="danger")
        kb.button(text="🔢 5 калькуляторов", callback_data="cmd:prank_open_calc_spam", style="primary")
        kb.button(text="🦠 Вирус Pivko", callback_data="cmd:prank_fake_virus", style="danger")
        kb.button(text="❓ Удалить Интернет", callback_data="cmd:prank_alert_loop", style="primary")
        kb.button(text="📋 Взлом буфера", callback_data="cmd:prank_paste_clipboard_spam", style="primary")
        kb.button(text="🔄 Инверт буфера", callback_data="cmd:prank_type_reversed", style="primary")
        kb.button(text="⚠️ Спам ошибок (10x)", callback_data="cmd:prank_fake_error_spam", style="danger")
        kb.button(text="💀 Удаление System32", callback_data="cmd:prank_fake_delete_sys32", style="danger")
        kb.button(text="👻 Призрак печати", callback_data="cmd:prank_ghost_typer", style="primary")
        kb.button(text="🌋 Землетрясение", callback_data="cmd:prank_earthquake", style="danger")
    else:
        # Страница 5: Мемы & Ультра-Троллинг (10)
        kb.button(text="🖼 Мемные обои", callback_data="cmd:prank_meme_wallpaper", style="success")
        kb.button(text="🐱 Атака котиков", callback_data="cmd:prank_cat_invaders", style="primary")
        kb.button(text="🐈 Выкуп котиками", callback_data="cmd:prank_fake_ransom_cats", style="danger")
        kb.button(text="🌈 Nyan Cat стрим", callback_data="cmd:prank_nyan_stream", style="success")
        kb.button(text="🎉 Выигрыш iPhone!", callback_data="cmd:prank_confetti_winner", style="success")
        kb.button(text="🪫 Разряд батареи 1%", callback_data="cmd:prank_low_battery_fake", style="danger")
        kb.button(text="🚨 Блокировка FBI/ФСБ", callback_data="cmd:prank_fbi_lock", style="danger")
        kb.button(text="🌐 Мемы в браузере", callback_data="cmd:prank_open_browser_memes", style="success")
        kb.button(text="🕺 ASCII Рикролл", callback_data="cmd:prank_rickroll_terminal", style="success")
        kb.button(text="🎲 Случайный сайт", callback_data="prank:randomsite", style="primary")

    # Панель вкладок категорий приколов (5 страниц)
    kb.button(text="🖥 Визуал" if page != 1 else "🔘 [Визуал]", callback_data="prankpage:1", style="primary")
    kb.button(text="🔊 Звук" if page != 2 else "🔘 [Звук]", callback_data="prankpage:2", style="primary")
    kb.button(text="🖱 Ввод" if page != 3 else "🔘 [Ввод]", callback_data="prankpage:3", style="primary")
    kb.button(text="💣 Хаос" if page != 4 else "🔘 [Хаос]", callback_data="prankpage:4", style="primary")
    kb.button(text="🐱 Мемы" if page != 5 else "🔘 [Мемы]", callback_data="prankpage:5", style="primary")

    kb.button(text="⬅️ Назад к ПК", callback_data="back:device", style="danger")
    kb.button(text="🏠 Главное меню", callback_data="menu:main", style="primary")

    kb.adjust(1, 2, 2, 2, 2, 2, 5, 2)
    return kb.as_markup()


def device_settings_menu():
    device_id = SESSION.get("target") or ""
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Переименовать", callback_data=f"devmg:rename:{device_id}", style="primary")
    kb.button(text="⭐ Избранные кнопки", callback_data="fav:menu", style="primary")
    kb.button(text="🕘 История команд", callback_data="hist:0", style="primary")
    kb.button(text="🔄 Проверить обновление", callback_data="cmd:check_update", style="success")
    kb.button(text="🚀 Автозапуск: Статус", callback_data="cmd:autorun_status", style="primary")
    kb.button(text="✅ Вкл автозапуск ПК", callback_data="cmd:autorun_enable", style="success")
    kb.button(text="🛑 Выкл автозапуск ПК", callback_data="cmd:autorun_disable", style="danger")
    kb.button(text="🗑 Удалить из списка", callback_data=f"devmg:delete:{device_id}", style="danger")
    kb.button(text="⛔ Заблокировать устройство", callback_data=f"devmg:block:{device_id}", style="danger")
    kb.button(text="🛑 Полное удаление агента с ПК", callback_data=f"devmg:uninstall:{device_id}", style="danger")
    kb.button(text="⬅️ К устройству", callback_data="back:device", style="primary")
    kb.button(text="🏠 Главное меню", callback_data="menu:main", style="primary")
    kb.adjust(2, 2, 1, 2, 2, 1, 2)
    return kb.as_markup()


def mic_options_menu():
    kb = InlineKeyboardBuilder()
    for sec in (5, 15, 30, 60):
        kb.button(text=f"🎙 {sec} сек", callback_data=f"micdur:{sec}", style="success")
    kb.button(text="⬅️ Медиа", callback_data="cat:media", style="danger")
    kb.button(text="🏠 Главное", callback_data="menu:main", style="primary")
    kb.adjust(2, 2, 2)
    return kb.as_markup()


def vol_options_menu():
    kb = InlineKeyboardBuilder()
    for lvl in (0, 25, 50, 75, 100):
        kb.button(text=f"🎚 {lvl}%", callback_data=f"volq:{lvl}", style="primary")
    kb.button(text="⬅️ Медиа", callback_data="cat:media", style="danger")
    kb.adjust(3, 2, 1)
    return kb.as_markup()


def confirm_kb(yes_cb: str, yes_text: str = "✅ Да, выполнить!", no_cb: str = "back:device"):
    kb = InlineKeyboardBuilder()
    kb.button(text=yes_text, callback_data=yes_cb, style="danger")
    kb.button(text="❌ Отмена", callback_data=no_cb, style="primary")
    kb.adjust(1, 1)
    return kb.as_markup()


def events_menu():
    s = bot_settings.all_settings()
    kb = InlineKeyboardBuilder()
    kb.button(
        text=f"{'✅' if s.get('notify_online') else '❌'} «Вернулся онлайн»",
        callback_data="ev:toggle:notify_online",
        style="success" if s.get("notify_online") else "danger",
    )
    kb.button(
        text=f"{'✅' if s.get('notify_offline') else '❌'} «Ушёл в оффлайн»",
        callback_data="ev:toggle:notify_offline",
        style="success" if s.get("notify_offline") else "danger",
    )
    kb.button(
        text=f"{'✅' if s.get('notify_battery_low') else '❌'} Низкая батарея (<20%)",
        callback_data="ev:toggle:notify_battery_low",
        style="success" if s.get("notify_battery_low") else "danger",
    )
    kb.button(text="🌙 Тихие часы", callback_data="ev:quiet", style="primary")
    kb.button(text="🗞 Ежедневный дайджест", callback_data="ev:digest", style="primary")
    kb.button(text="👥 Администраторы", callback_data="ev:admins", style="primary")
    auto_on = get_server_autostart_status()
    kb.button(
        text=f"🚀 Автозапуск Сервера: {'ВКЛ ✅' if auto_on else 'ВЫКЛ ❌'}",
        callback_data="ev:server_autostart:toggle",
        style="success" if auto_on else "danger",
    )
    kb.button(text="🏠 Главное меню", callback_data="menu:main", style="primary")
    kb.adjust(1)
    return kb.as_markup()


def quiet_hours_menu():
    s = bot_settings.all_settings()
    cur_f = str(s.get("quiet_from") or "")
    cur_t = str(s.get("quiet_to") or "")
    kb = InlineKeyboardBuilder()
    for f, t, label in (
        ("", "", "➖ Выключить"),
        ("23", "8", "🌙 23:00 – 08:00"),
        ("22", "7", "🌙 22:00 – 07:00"),
        ("0", "6", "🌙 00:00 – 06:00"),
    ):
        active = cur_f == f and cur_t == t
        mark = "✅" if active else ""
        kb.button(
            text=f"{mark} {label}".strip(),
            callback_data=f"quiet:{f}:{t}",
            style="success" if active else "primary",
        )
    kb.button(text="⬅️ События", callback_data="ev:menu", style="primary")
    kb.adjust(1)
    return kb.as_markup()


def digest_menu():
    s = bot_settings.all_settings()
    cur = str(s.get("report_hour") or "")
    kb = InlineKeyboardBuilder()
    for val, label in (
        ("", "➖ Выключить"),
        ("9", "🗞 Отчёт в 09:00"),
        ("21", "🗞 Отчёт в 21:00"),
    ):
        active = cur == val
        mark = "✅" if active else ""
        kb.button(
            text=f"{mark} {label}".strip(),
            callback_data=f"digest:{val}",
            style="success" if active else "primary",
        )
    kb.button(text="⬅️ События", callback_data="ev:menu", style="primary")
    kb.adjust(1)
    return kb.as_markup()


def admins_menu():
    s = bot_settings.all_settings()
    kb = InlineKeyboardBuilder()
    kb.button(text=f"⭐ {ADMIN_ID} (владелец)", callback_data="noop", style="primary")
    for a in s.get("admins") or []:
        kb.button(text=f"➖ {a}", callback_data=f"adm:rm:{a}", style="danger")
    kb.button(text="➕ Добавить по Telegram ID", callback_data="adm:add", style="success")
    kb.button(text="⬅️ События", callback_data="ev:menu", style="primary")
    kb.adjust(1)
    return kb.as_markup()


ROLE_LABELS = {
    Role.OWNER: "Владелец",
    Role.COOWNER: "Со-владелец",
    Role.USER: "Пользователь",
    Role.GUEST: "Гость",
    Role.BLOCKED: "Заблокирован",
}

USER_PERMISSION_CHOICES = {
    "cmd:status": "Статус устройства",
    "cmd:sysinfo": "Сведения о системе",
    "cmd:battery": "Батарея",
    "cmd:screenshot": "Скриншот",
    "cmd:lock": "Блокировка экрана",
    "full_device": "Полное управление выданными устройствами",
}


def _user_label(record: dict) -> str:
    username = str(record.get("username") or "").strip()
    name = str(record.get("display_name") or "").strip()
    if username:
        return f"@{username}"
    if name:
        return name
    return f"id:{record.get('id', '?')}"


def _role_label(role: str) -> str:
    return ROLE_LABELS.get(role, role)


def admin_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="Пользователи", callback_data="admin:users", style="primary")
    kb.button(text="Журнал действий", callback_data="admin:audit", style="primary")
    kb.button(text="Тексты бота", callback_data="admin:texts", style="primary")
    mode = bot_settings.get("ui_style", "technical")
    labels = {"technical": "технический", "conversational": "разговорный", "custom": "кастомный"}
    kb.button(
        text=f"Текст: {labels.get(mode, 'технический')}",
        callback_data="admin:style",
        style="success" if mode == "custom" else "primary",
    )
    kb.button(text="Серверная", callback_data="menu:server", style="primary")
    kb.button(text="Главное меню", callback_data="menu:main", style="primary")
    kb.adjust(1)
    return kb.as_markup()


TEXT_LABELS = {
    "start_guest": "Приветствие гостя",
    "start_user": "Приветствие пользователя",
    "start_owner": "Приветствие владельца",
    "blocked": "Сообщение заблокированному",
    "custom_start_guest": "Кастомное приветствие гостя",
    "custom_start_user": "Кастомное приветствие пользователя",
    "custom_start_owner": "Кастомное приветствие владельца",
    "custom_blocked": "Кастомное сообщение блокировки",
}


def admin_texts_menu():
    kb = InlineKeyboardBuilder()
    for key, label in TEXT_LABELS.items():
        preview = text_store.get(key).replace("\n", " ")[:34]
        kb.button(text=f"{label}: {preview}", callback_data=f"admin:text:{key}", style="primary")
    kb.button(text="Назад", callback_data="menu:admin", style="primary")
    kb.adjust(1)
    return kb.as_markup()


def admin_users_menu():
    kb = InlineKeyboardBuilder()
    users = access_store.list_users()
    if not users:
        kb.button(text="Пока никто не запускал бота", callback_data="noop", style="primary")
    for record in users[:30]:
        uid = int(record.get("id") or 0)
        role = access_store.get_role(uid, ADMIN_ID)
        blocked = role == Role.BLOCKED
        text = f"{'Заблокирован: ' if blocked else ''}{_user_label(record)} — {_role_label(role)}"
        kb.button(text=text[:62], callback_data=f"admin:user:{uid}", style="danger" if blocked else "primary")
    kb.button(text="Назад", callback_data="menu:admin", style="primary")
    kb.adjust(1)
    return kb.as_markup()


def admin_user_menu(user_id: int):
    record = access_store.get_user(user_id) or {"id": user_id, "role": Role.GUEST, "permissions": {}}
    role = access_store.get_role(user_id, ADMIN_ID)
    blocked = role == Role.BLOCKED
    kb = InlineKeyboardBuilder()
    if user_id != ADMIN_ID:
        kb.button(text="Сделать пользователем", callback_data=f"admin:role:{user_id}:user", style="success")
        kb.button(text="Сделать гостем", callback_data=f"admin:role:{user_id}:guest", style="primary")
        kb.button(
            text="Разблокировать" if blocked else "Заблокировать",
            callback_data=f"admin:block:{user_id}",
            style="success" if blocked else "danger",
        )
        kb.button(text="Выдать устройства", callback_data=f"admin:devices:{user_id}", style="primary")
        kb.button(text="Выдать кнопки", callback_data=f"admin:perms:{user_id}", style="primary")
        kb.button(text="Написать пользователю", callback_data=f"admin:message:{user_id}", style="primary")
    kb.button(text="К пользователям", callback_data="admin:users", style="primary")
    kb.adjust(1)
    return kb.as_markup()


def admin_devices_menu(user_id: int):
    record = access_store.get_user(user_id) or {}
    granted = set((record.get("permissions") or {}).get("devices") or [])
    kb = InlineKeyboardBuilder()
    for device_id, info in sorted(devices.all().items()):
        mark = "Выдано: " if device_id in granted else "Выдать: "
        name = str(info.get("name") or device_id)
        kb.button(
            text=f"{mark}{name}"[:62],
            callback_data=f"admin:device:{user_id}:{device_id}",
            style="success" if device_id in granted else "primary",
        )
    kb.button(text="К пользователю", callback_data=f"admin:user:{user_id}", style="primary")
    kb.adjust(1)
    return kb.as_markup()


def admin_permissions_menu(user_id: int):
    record = access_store.get_user(user_id) or {}
    granted = set((record.get("permissions") or {}).get("callbacks") or [])
    kb = InlineKeyboardBuilder()
    for callback, label in USER_PERMISSION_CHOICES.items():
        enabled = callback in granted
        kb.button(
            text=("Выдано: " if enabled else "Выдать: ") + label,
            callback_data=f"admin:perm:{user_id}:{callback.replace(':', '_')}",
            style="success" if enabled else "primary",
        )
    kb.button(text="К пользователю", callback_data=f"admin:user:{user_id}", style="primary")
    kb.adjust(1)
    return kb.as_markup()


def guest_devices_menu():
    kb = InlineKeyboardBuilder()
    for _, info in sorted(devices.all().items()):
        name = str(info.get("name") or "Устройство")
        status = "онлайн" if _status_dot(info) in ("🟢", "🟡") else "офлайн"
        kb.button(text=f"{name}: {status}"[:62], callback_data="guest:readonly", style="primary")
    kb.button(text="Главное меню", callback_data="menu:main", style="primary")
    kb.adjust(1)
    return kb.as_markup()


def server_menu(user_id: int | None = None):
    user_id = int(user_id if user_id is not None else CURRENT_TG_USER.get())
    role = get_user_role(user_id)
    kb = InlineKeyboardBuilder()
    if role == Role.OWNER:
        approval = bool(bot_settings.get("require_device_approval", True))
        kb.button(text="📊 Статус сервиса", callback_data="server:status", style="primary")
        kb.button(text="📈 Нагрузка сейчас", callback_data="server:metrics", style="primary")
        kb.button(text="🖼 График нагрузки", callback_data="server:chart", style="primary")
        kb.button(text="📜 Последние логи", callback_data="server:logs", style="primary")
        kb.button(text="🧪 SSH-команды", callback_data="server:terminal", style="primary")
        kb.button(text="🔄 Перезапустить", callback_data="server:restart", style="danger")
        kb.button(text="⬆️ Обновить из подготовленного пакета", callback_data="server:update", style="primary")
        kb.button(text="↩️ Откатить последнюю версию", callback_data="server:rollback", style="danger")
        kb.button(
            text=f"Подтверждение новых устройств: {'включено' if approval else 'выключено'}",
            callback_data="server:approval",
            style="success" if approval else "danger",
        )
    kb.button(text="Главное меню", callback_data="menu:main", style="primary")
    kb.adjust(1)
    return kb.as_markup()


def server_confirm_menu(action: str):
    labels = {
        "restart": "перезапустить сервис",
        "update": "установить подготовленный пакет и проверить здоровье",
        "rollback": "откатить последнюю резервную копию",
    }
    kb = InlineKeyboardBuilder()
    kb.button(text=f"✅ Подтвердить: {labels.get(action, action)}", callback_data=f"server_confirm:{action}", style="danger")
    kb.button(text="Отмена", callback_data="menu:server", style="primary")
    kb.adjust(1)
    return kb.as_markup()


def blocked_menu():
    s = bot_settings.all_settings()
    kb = InlineKeyboardBuilder()
    for bid in s.get("blocked_ids") or []:
        kb.button(text=f"➖ Разблокировать {bid}", callback_data=f"devmg:unblock:{bid}", style="success")
    kb.button(text="⬅️ К списку", callback_data="menu:target", style="primary")
    kb.adjust(1)
    return kb.as_markup()


def _favorites_kb(device_id: str, favs: list):
    kb = InlineKeyboardBuilder()
    for action in FAVORITABLE:
        is_fav = action in favs
        mark = "✅" if is_fav else "➕"
        kb.button(
            text=f"{mark} {ACTION_LABELS[action]}",
            callback_data=f"fav:toggle:{action}",
            style="success" if is_fav else "primary",
        )
    kb.button(text="⬅️ К устройству", callback_data="back:device", style="primary")
    kb.adjust(1)
    return kb.as_markup()


# =====================================================================
#  Утилиты
# =====================================================================

def target_label(device_id: str) -> str:
    if device_id == "all":
        return "все устройства"
    info = devices.get(device_id)
    return info.get("name", device_id) if info else device_id

def publish(action: str, **kwargs) -> bool:
    """Отправить команду текущей цели через MQTT."""
    target = SESSION.get("target")
    if not target:
        log.warning("⚠️ Попытка отправки '%s' без выбранной цели", action)
        return False
    audit("cmd_publish", action=action, target=target, kwargs_keys=",".join(kwargs.keys()))
    args_str = f" args={kwargs}" if kwargs else ""
    log.info("🚀 [CMD] -> %s (%s) | action='%s'%s", target, target_label(target), action, args_str)
    HISTORY.setdefault(target, []).append((action, dict(kwargs), time.time()))
    del HISTORY[target][:-20]
    return transport.publish_command(target, action, **kwargs)


def publish_tracked(action: str, **kwargs) -> tuple[bool, str]:
    """Отправить команду с известным correlation id для точного ожидания ответа."""
    command_id = uuid.uuid4().hex[:12]
    ok = publish(action, id=command_id, **kwargs)
    return ok, command_id

def _no_target_text() -> str:
    return "⚠️ Цель не выбрана. Нажмите «Назад» и выберите устройство."


async def _replace_callback_message(cq: CallbackQuery, text: str, reply_markup=None):
    """Обновить текущую карточку; при запрете редактирования убрать старую."""
    try:
        await cq.message.edit_text(text, reply_markup=reply_markup)
    except Exception as exc:
        # Telegram возвращает эту ошибку, когда текст уже такой же. В этом
        # случае нельзя удалять карточку и создавать дубль.
        if "not modified" in str(exc).lower():
            return
        try:
            await cq.message.delete()
        except Exception:
            pass
        await cq.message.answer(text, reply_markup=reply_markup)

# =====================================================================
#  Глобальные объекты
# =====================================================================

devices = DeviceStore()
status_collector = ResponseCollector()
screenshot_collector = ResponseCollector()
sysinfo_collector = ResponseCollector()
webcam_collector = ResponseCollector()
clipboard_collector = ResponseCollector()
processes_collector = ResponseCollector()
shell_collector = ResponseCollector()
mic_collector = ResponseCollector()
battery_collector = ResponseCollector()
network_collector = ResponseCollector()
services_collector = ResponseCollector()
capabilities_collector = ResponseCollector()
disks_collector = ResponseCollector()
multi_status = MultiCollector()
multi_screenshot = MultiCollector()
# Защита от двойного клика: одна Telegram-карточка не должна одновременно
# обслуживаться несколькими долгими командами.
_ACTIVE_UI_COMMANDS: set[tuple[int, int, int, str]] = set()

# Волна новых команд: текстовые ответы приходят в общий коллектор.
# ВАЖНО: здесь перечислены ВСЕ типы ответов, которые публикуются агентом
# в отдельные топики и обрабатываются через fun_text_collector.
FUN_RESPONSE_TYPES = {
    # Файлы и папки
    "dir_list", "find_file", "path_open", "file_put", "file_del",
    # Сеть и система
    "ext_ip", "wifi_info", "usb_devices", "netstat", "startup_list",
    "env_get", "net_wifi_passwords", "net_bluetooth_list",
    "storage_smart", "sys_installed_apps", "sys_history_cmd",
    # Управление вводом и дисплей
    "hotkey", "type_text", "msgbox_spam",
    "screen_off", "screensaver_on", "wallpaper_set",
    "display_brightness", "display_night_light", "display_rotate",
    # Загрузка и прочее
    "download_url", "proc_kill_name",
    # Режим ожидания (Watchdog) и автозапуск
    "standby_sleep", "wake", "autorun_status", "autorun_enable", "autorun_disable",
    # Системные утилиты и диагностика
    "sys_uptime", "sys_clean_temp", "net_ping",
    # Приколы и розыгрыши (40 приколов)
    "prank_screamer", "prank_rickroll", "prank_matrix", "prank_siren",
    "prank_shout_tts", "prank_swap_mouse", "prank_crazy_cursor",
    "prank_hide_desktop", "prank_dancing_windows", "prank_black_screen",
    "prank_random_site", "prank_bsod", "prank_fake_update", "prank_toast_spam",
    "prank_keyboard_disco", "prank_beep_morse", "prank_open_notepad_type",
    "prank_hacker_typer", "prank_sound_spooky", "prank_sound_fart",
    "prank_minimize_all", "prank_open_calc_spam", "prank_invert_screen",
    "prank_slow_mouse", "prank_random_clicks", "prank_paste_clipboard_spam",
    "prank_type_reversed", "prank_volume_jump", "prank_say_whisper",
    "prank_fake_virus", "prank_open_cd", "prank_change_wallpaper",
    "prank_restore_wallpaper", "prank_speak_time", "prank_rickroll_terminal",
    "prank_screen_off_brief", "prank_alert_loop", "prank_open_browser_memes",
    "prank_glitch_cursor", "prank_shake_window",
    # Новые мега-приколы
    "prank_meme_wallpaper", "prank_caps_disco", "prank_cursor_circle",
    "prank_fake_error_spam", "prank_fake_delete_sys32", "prank_ghost_typer",
    "prank_fbi_lock", "prank_cat_invaders", "prank_fake_ransom_cats",
    "prank_nyan_stream", "prank_random_beeps", "prank_confetti_winner",
    "prank_low_battery_fake", "prank_earthquake", "prank_laugh_track",
    "prank_stop_all",
    # Обновление, питание, локация и удаление
    "power", "agent_update", "uninstall_agent", "geo_location",
    "guardian",
}
fun_text_collector = ResponseCollector()
file_collector = ResponseCollector()

# Watchdog-кэши: история команд, последние процессы, алерты по батарее.
HISTORY: dict[str, list] = {}
LAST_PROCESSES: dict[str, dict] = {}
LAST_BATTERY_ALERT: dict[str, float] = {}
# Дебаунс онлайн/офлайн уведомлений: не слать спам при флипе публичного брокера.
# Ключ: device_id, значение: (last_status: bool, last_notify_time: float)
_NOTIFY_DEBOUNCE: dict[str, tuple] = {}  # {device_id: (was_online, ts)}
_NOTIFY_DEBOUNCE_SEC = 120  # не спамить при серии перезапусков/флипов агента
_START_NOTIFY_LAST: dict[int, float] = {}
_OFFLINE_TIMEOUT_SEC = 150  # два пропущенных heartbeat-а считаем офлайном

LOOP: asyncio.AbstractEventLoop | None = None
SESSION: SessionRegistry = SessionRegistry()

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

# =====================================================================
#  MQTT → бот
# =====================================================================

def _notification_recipients() -> list[int]:
    """Владелец и добавленные администраторы получают watchdog-уведомления."""
    ids = [int(ADMIN_ID)]
    for value in bot_settings.get("admins", []) or []:
        try:
            uid = int(value)
        except (TypeError, ValueError):
            continue
        if uid not in ids:
            ids.append(uid)
    return ids


async def _notify_admins(text: str, reply_markup=None) -> None:
    for user_id in _notification_recipients():
        try:
            await bot.send_message(user_id, text, reply_markup=reply_markup)
        except Exception:
            log.exception("Не удалось отправить уведомление администратору %s", user_id)


def _schedule_admin_notice(text: str, reply_markup=None) -> None:
    """Безопасно отправить уведомление из MQTT-потока в asyncio-цикл."""
    if LOOP is None or LOOP.is_closed():
        log.warning("Уведомление не отправлено: asyncio loop ещё не запущен")
        return
    future = asyncio.run_coroutine_threadsafe(_notify_admins(text, reply_markup), LOOP)
    future.add_done_callback(
        lambda done: done.exception() if not done.cancelled() else None
    )

def _maybe_battery_alert(device_id: str, payload: dict) -> None:
    """Watchdog по батарее: алерт при заряде <=20% не чаще раза в 30 минут."""
    batt = payload.get("battery") if isinstance(payload.get("battery"), dict) else payload
    if not batt.get("available"):
        return
    try:
        percent = float(batt.get("percent", 100))
    except (TypeError, ValueError):
        return
    if percent > 20:
        return
    now = time.time()
    if now - LAST_BATTERY_ALERT.get(device_id, 0) < 1800:
        return
    LAST_BATTERY_ALERT[device_id] = now
    if not bot_settings.get("notify_battery_low", True):
        return
    if bot_settings.quiet_active():
        return
    name = target_label(device_id)
    if LOOP is None:
        return
    _schedule_admin_notice(
        f"🔋 <b>{html.escape(name)}</b>: низкий заряд батареи — {percent:.0f}%",
    )


def on_mqtt_message(topic: str, data: dict) -> None:
    """Вызывается из потока MQTT при получении сообщения от клиента."""
    payload = verify_message(data)
    if payload is None:
        log.warning("Сообщение с неверной подписью в топике %s", topic)
        return
    # ENCRYPT_PAYLOAD: содержимое лежит внутри enc, расшифровываем до маршрутизации.
    if ENCRYPT_PAYLOAD or ("enc" in payload):
        inner = decrypt_payload(payload)
        if inner is None:
            log.warning("Не удалось расшифровать payload из %s", topic)
            return
        payload = inner
    parts = topic.split("/")
    prefix_len = len(MQTT_PREFIX.split("/"))
    if len(parts) != prefix_len + 2:
        return
    device_id = parts[prefix_len]
    msg_type = parts[-1]

    # Блок-лист: устройства в нём не принимаем и не показываем.
    if msg_type == "status" and bot_settings.is_blocked(device_id):
        log.info("Статус от заблокированного устройства %s — игнорируем", device_id)
        return

    if msg_type == "status" and payload.get("type") == "status":
        raw_status = str(payload.get("status", "online")).lower()
        online = raw_status != "offline"
        is_standby = (raw_status == "standby")
        old = devices.get(device_id) or {}
        # Для LWT нельзя вычислять прошлое состояние через _status_dot():
        # старый online-агент может быть уже старше 120 секунд, но событие
        # «ушёл офлайн» всё равно должно прийти владельцу.
        was_online = bool(old.get("online", False))
        info = {
            "name": str(payload.get("name") or device_id),
            "os": str(payload.get("os") or "unknown"),
            "version": str(payload.get("version") or "?"),
            "online": online,
            "standby": is_standby,
        }
        if online:
            info["last_seen"] = time.time()
        is_new = device_id not in devices.all()
        devices.upsert(device_id, info)
        status_collector.submit(device_id, info)
        multi_status.submit(device_id, info)
        audit("device_online" if online else "device_offline",
              device_id=device_id, name=info["name"], is_new=is_new)
        quiet = bot_settings.quiet_active()
        # Watchdog-уведомления: вернулся онлайн / ушёл в оффлайн (LWT)
        # Дебаунс: не слать уведомление если статус не изменился или изменился
        # слишком быстро (флип публичного MQTT-брокера при кратком разрыве).
        _prev = _NOTIFY_DEBOUNCE.get(device_id)
        _now = time.time()
        _debounce_ok = True
        if _prev is not None:
            _prev_online, _prev_ts = _prev
            if _prev_online == online:
                _debounce_ok = False  # статус не изменился
            elif _now - _prev_ts < _NOTIFY_DEBOUNCE_SEC:
                _debounce_ok = False  # изменился слишком быстро — флип брокера
        _NOTIFY_DEBOUNCE[device_id] = (online, _now)

        if not is_new and online and not was_online \
                and bot_settings.get("notify_online", True) and not quiet and _debounce_ok:
            _schedule_admin_notice(
                f"🟢 <b>{html.escape(info['name'])}</b> вернулся онлайн",
            )
        if not online and was_online \
                and bot_settings.get("notify_offline", True) and not quiet and _debounce_ok:
            _schedule_admin_notice(
                f"🔴 <b>{html.escape(info['name'])}</b> ушёл в оффлайн",
            )
        if is_new and LOOP is not None and bot_settings.get("require_device_approval", True):
            kb = InlineKeyboardBuilder()
            kb.button(text="✅ Добавить", callback_data=f"devmg:allow:{device_id}", style="success")
            kb.button(text="⛔ Заблокировать", callback_data=f"devmg:block:{device_id}", style="danger")
            kb.adjust(1)
            _schedule_admin_notice(
                f"🆕 Новое устройство: <b>{html.escape(info['name'])}</b> "
                f"(<code>{device_id}</code>)\nПодтверди, чтобы работать с ним:",
                reply_markup=kb.as_markup(),
            )

    elif msg_type == "screenshot" and payload.get("type") == "screenshot":
        screenshot_collector.submit(device_id, payload)
        multi_screenshot.submit(device_id, payload)

    elif msg_type == "webcam" and payload.get("type") == "webcam":
        webcam_collector.submit(device_id, payload)

    elif msg_type == "sysinfo" and payload.get("type") == "sysinfo":
        sysinfo_collector.submit(device_id, payload)
        _maybe_battery_alert(device_id, payload)

    elif msg_type == "clipboard" and payload.get("type") == "clipboard":
        clipboard_collector.submit(device_id, payload)

    elif msg_type == "processes" and payload.get("type") == "processes":
        processes_collector.submit(device_id, payload)

    elif msg_type == "shell" and payload.get("type") == "shell":
        shell_collector.submit(device_id, payload)

    elif msg_type == "mic" and payload.get("type") == "mic":
        mic_collector.submit(device_id, payload)
    elif msg_type == "battery" and payload.get("type") == "battery":
        battery_collector.submit(device_id, payload)
        _maybe_battery_alert(device_id, payload)
    elif msg_type == "network" and payload.get("type") == "network":
        network_collector.submit(device_id, payload)
    elif msg_type == "services" and payload.get("type") == "services":
        services_collector.submit(device_id, payload)
    elif msg_type == "disks" and payload.get("type") == "disks":
        disks_collector.submit(device_id, payload)
    elif msg_type == "capabilities" and payload.get("type") == "capabilities":
        capabilities_collector.submit(device_id, payload)
    elif msg_type == "ack" and payload.get("type") == "ack":
        status = payload.get("status")
        action = payload.get("action")
        log.info(f"ACK from {device_id}: {action} -> {status}")
    elif ((msg_type in FUN_RESPONSE_TYPES and payload.get("type") == msg_type)
          or (msg_type == "output" and payload.get("type") in FUN_RESPONSE_TYPES)):
        # Волна новых команд: ответы с текстом → общий текст-коллектор,
        # файлы (file_get) → файловый коллектор.
        if payload.get("type") == "file_get":
            file_collector.submit(device_id, payload)
        elif "text" in payload or "error" in payload or not payload.get("ok", True):
            fun_text_collector.submit(device_id, payload)


async def _device_offline_watchdog() -> None:
    """Страховка на случай, если брокер не доставил MQTT Last Will."""
    while True:
        await asyncio.sleep(30)
        now = time.time()
        quiet = bot_settings.quiet_active()
        for device_id, info in devices.all().items():
            if not info.get("online"):
                continue
            last_seen = float(info.get("last_seen", 0) or 0)
            if not last_seen or now - last_seen < _OFFLINE_TIMEOUT_SEC:
                continue
            devices.upsert(device_id, {"online": False, "standby": False})
            _NOTIFY_DEBOUNCE[device_id] = (False, now)
            audit("device_offline_watchdog", device_id=device_id, name=info.get("name", device_id))
            if bot_settings.get("notify_offline", True) and not quiet:
                _schedule_admin_notice(
                    f"🔴 <b>{html.escape(str(info.get('name') or device_id))}</b> "
                    "не выходит на связь (heartbeat просрочен)"
                )

transport = MQTTTransport(on_mqtt_message)

# =====================================================================
#  Хендлеры: текстовые сообщения
# =====================================================================

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    SESSION["target"] = None
    record, first_start = access_store.register_start(message.from_user)
    role = get_user_role(message.from_user.id)
    online_count = sum(
        1 for v in devices.all().values() if _status_dot(v) in ("🟢", "🟡")
    )
    total_count = len(devices.all())
    now = time.time()
    last_notice = _START_NOTIFY_LAST.get(message.from_user.id, 0.0)
    # Нового человека уведомляем всегда; повторный /start от гостя — не чаще
    # раза в 10 минут, чтобы случайный спам не засыпал владельца.
    should_notify_start = (
        message.from_user.id != ADMIN_ID
        and (first_start or now - last_notice >= 600)
    )
    if should_notify_start:
        try:
            kb = InlineKeyboardBuilder()
            kb.button(text="Открыть пользователя", callback_data=f"admin:user:{message.from_user.id}", style="primary")
            kb.adjust(1)
            await _notify_admins(
                "Новый запуск бота: "
                f"<b>{html.escape(_user_label(record))}</b> "
                f"(<code>{message.from_user.id}</code>). Роль по умолчанию: гость.",
                reply_markup=kb.as_markup(),
            )
            _START_NOTIFY_LAST[message.from_user.id] = now
        except Exception:
            log.exception("Не удалось уведомить владельца о новом пользователе")
    if role == Role.BLOCKED:
        await message.answer(text_store.get("custom_blocked" if _custom_ui() else "blocked"))
        return
    if role == Role.GUEST:
        intro = text_store.get("custom_start_guest" if _custom_ui() else "start_guest")
    elif role == Role.USER:
        intro = text_store.get("custom_start_user" if _custom_ui() else "start_user")
    else:
        intro = text_store.get("custom_start_owner" if _custom_ui() else "start_owner")
    await message.answer(
        f"<b>XIDER {XIDER_BUILD_CODE}</b>\n"
        f"Роль: <b>{html.escape(_role_label(role))}</b>\n"
        f"Устройств онлайн: <b>{online_count}/{total_count}</b>\n\n"
        f"{html.escape(intro)}",
        reply_markup=main_menu(message.from_user.id),
    )


@router.message(AdminFilter(), Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    cur = await state.get_state()
    await state.clear()
    if cur:
        await message.answer(
            "🚫 Ввод отменён.",
            reply_markup=back_to_device_kb() if SESSION.get("target") else main_menu(),
        )
    else:
        await message.answer("Нечего отменять.", reply_markup=main_menu())


@router.callback_query(ReadOnlyFilter(), F.data == "menu:about")
async def on_menu_about(cq: CallbackQuery):
    """Раздел 'О системе XIDER' — полная инфо-карточка."""
    await cq.answer()
    devs   = devices.all()
    online = sum(1 for v in devs.values() if _status_dot(v) in ("🟢", "🟡"))
    total  = len(devs)

    text = (
        f"⚡ <b>XIDER</b> — Remote Control System\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"\n"
        f"<b>Версия:</b> {XIDER_BUILD_CODE}\n"
        f"<b>Сборка:</b>  {XIDER_BUILD}\n"
        f"<b>👨‍💻 Автор:</b>   {XIDER_AUTHOR}\n"
        f"\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>🏗 Архитектура</b>\n"
        f"┌ Бот:      Python 3.10+ / aiogram 3.x\n"
        f"├ Агент Win: Python + pystray + WinAPI\n"
        f"└ Агент Mac: Python + pyobjc + osascript\n"
        f"\n"
        f"<b>📡 Транспорт</b>\n"
        f"┌ Протокол: MQTT v3.1.1\n"
        f"├ Брокер:   EMQX Cloud Serverless\n"
        f"├ TLS:      ✅ 8883 (SSL/TLS)\n"
        f"└ Топик:    xgent/v1/{{device_id}}/cmd\n"
        f"\n"
        f"<b>🔐 Безопасность</b>\n"
        f"┌ Подпись:  HMAC-SHA256 (каждое сообщение)\n"
        f"├ Антирепл: Nonce + временное окно 120с\n"
        f"├ Шифровка: AES-256-GCM (опционально)\n"
        f"└ Доступ:   whitelist по Telegram ID\n"
        f"\n"
        f"<b>📊 Статус прямо сейчас</b>\n"
        f"└ Устройств онлайн: <b>{online} / {total}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="🏠 Главное меню", callback_data="menu:main", style="primary")
    kb.adjust(1)
    await cq.message.answer(text, reply_markup=kb.as_markup())


@router.message(AdminFilter(), Form.wait_url)
async def on_url_input(message: Message, state: FSMContext):
    await state.clear()
    url = (message.text or "").strip()
    if not url:
        await message.answer("⚠️ Пустая ссылка.", reply_markup=back_to_device_kb())
        return
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "https://" + url
    if publish("open_url", url=url):
        await message.answer(
            f"✅ Открываю на <b>{target_label(SESSION['target'])}</b>:\n{url}",
            reply_markup=back_to_device_kb(),
        )
    else:
        await message.answer(
            "⚠️ Нет соединения с MQTT-брокером.",
            reply_markup=back_to_device_kb(),
        )

@router.message(AdminFilter(), Form.wait_text)
async def on_text_input(message: Message, state: FSMContext):
    await state.clear()
    text = (message.text or "").strip()
    if not text:
        await message.answer("⚠️ Пустой текст.", reply_markup=back_to_device_kb())
        return
    if publish("notify", text=text):
        await message.answer(
            f"✅ Текст отправлен на <b>{target_label(SESSION['target'])}</b>.",
            reply_markup=back_to_device_kb(),
        )
    else:
        await message.answer(
            "⚠️ Нет соединения с MQTT-брокером.",
            reply_markup=back_to_device_kb(),
        )

@router.message(AdminFilter(), Form.wait_sound)
async def on_sound_input(message: Message, state: FSMContext):
    await state.clear()
    text = (message.text or "").strip()
    if not text:
        await message.answer("⚠️ Пустой текст.", reply_markup=back_to_device_kb())
        return
    if publish("sound", text=text):
        await message.answer(
            f"✅ Звук отправлен на <b>{target_label(SESSION['target'])}</b>.",
            reply_markup=back_to_device_kb(),
        )
    else:
        await message.answer(
            "⚠️ Нет соединения с MQTT-брокером.",
            reply_markup=back_to_device_kb(),
        )

@router.message(F.from_user.id != ADMIN_ID)
async def on_denied(message: Message):
    if get_user_role(message.from_user.id) == Role.BLOCKED:
        await message.answer("Доступ для этого аккаунта заблокирован.")
    else:
        await message.answer("Используй /start, чтобы открыть доступные разделы.")


# ---------- XIDER 3: доступ, администрирование и серверная ----------

@router.callback_query(ReadOnlyFilter(), F.data == "menu:server")
async def on_menu_server(cq: CallbackQuery):
    broker = "подключён" if transport.connected.is_set() else "ожидает подключения"
    approval = "включено" if bot_settings.get("require_device_approval", True) else "выключено"
    await cq.message.edit_text(
        "<b>Серверная</b>\n"
        f"Сборка: <code>{XIDER_BUILD_CODE}</code>\n"
        f"MQTT: <b>{broker}</b>\n"
        f"Новые устройства: подтверждение {approval}\n"
        "Секреты, токены и ключи никогда не показываются в этом разделе.",
        reply_markup=server_menu(cq.from_user.id),
    )
    await cq.answer()


@router.callback_query(OwnerFilter(), F.data == "server:approval")
async def on_server_approval(cq: CallbackQuery):
    value = bot_settings.toggle("require_device_approval")
    access_store.append_audit("device_approval_policy", actor_id=cq.from_user.id, detail=str(value))
    broker = "подключён" if transport.connected.is_set() else "ожидает подключения"
    await cq.message.edit_text(
        "<b>Серверная</b>\n"
        f"Сборка: <code>{XIDER_BUILD_CODE}</code>\n"
        f"MQTT: <b>{broker}</b>\n"
        f"Новые устройства: подтверждение {'включено' if value else 'выключено'}\n"
        "Секреты, токены и ключи никогда не показываются в этом разделе.",
        reply_markup=server_menu(cq.from_user.id),
    )
    await cq.answer("Подтверждение включено" if value else "Автодобавление включено")


@router.callback_query(OwnerFilter(), F.data.in_({"server:restart", "server:update", "server:rollback"}))
async def on_server_dangerous_request(cq: CallbackQuery):
    action = cq.data.split(":", 1)[1]
    await cq.message.edit_text(
        "<b>Подтверждение серверной операции</b>\n"
        "Операция затрагивает работающий VPS и может временно прервать бота.\n"
        f"Действие: <code>{html.escape(action)}</code>",
        reply_markup=server_confirm_menu(action),
    )
    await cq.answer()


@router.callback_query(OwnerFilter(), F.data == "server:status")
async def on_server_status(cq: CallbackQuery):
    result = await asyncio.to_thread(server_ops.status)
    access_store.append_audit("server_status", actor_id=cq.from_user.id, detail=f"ok={result.ok}")
    await cq.message.edit_text(
        f"<b>Статус сервиса</b>\n<pre>{html.escape(result.text)}</pre>",
        reply_markup=server_menu(cq.from_user.id),
    )
    await cq.answer("Готово" if result.ok else "Сервис ответил с ошибкой", show_alert=not result.ok)


@router.callback_query(OwnerFilter(), F.data == "server:logs")
async def on_server_logs(cq: CallbackQuery):
    result = await asyncio.to_thread(server_ops.logs, 45)
    access_store.append_audit("server_logs", actor_id=cq.from_user.id, detail=f"ok={result.ok}")
    await cq.message.edit_text(
        f"<b>Последние логи xider-bot</b>\n<pre>{html.escape(result.text[-3600:])}</pre>",
        reply_markup=server_menu(cq.from_user.id),
    )
    await cq.answer("Готово" if result.ok else "Не удалось получить логи", show_alert=not result.ok)


@router.callback_query(OwnerFilter(), F.data == "server:metrics")
async def on_server_metrics(cq: CallbackQuery):
    snapshot = await asyncio.to_thread(server_ops.metrics)
    access_store.append_audit("server_metrics", actor_id=cq.from_user.id, detail="snapshot")
    await cq.message.edit_text(
        "<b>Нагрузка VPS</b>\n"
        f"CPU: <b>{snapshot['load']:.1f}%</b>\n"
        f"RAM: <b>{snapshot['memory']:.1f}%</b>\n"
        f"Диск /: <b>{snapshot['disk']:.1f}%</b>\n\n"
        "Нажми «График нагрузки», чтобы увидеть историю последних замеров.",
        reply_markup=server_menu(cq.from_user.id),
    )
    await cq.answer("Снял показатели")


@router.callback_query(OwnerFilter(), F.data == "server:chart")
async def on_server_chart(cq: CallbackQuery):
    snapshot = await asyncio.to_thread(server_ops.metrics)
    image = await asyncio.to_thread(server_ops.render_metrics_chart, snapshot)
    access_store.append_audit("server_chart", actor_id=cq.from_user.id, detail="snapshot")
    await cq.message.edit_text(
        "<b>График нагрузки VPS</b>\nЗамер сохранён. Кнопки ниже возвращают в серверную.",
        reply_markup=server_menu(cq.from_user.id),
    )
    await cq.message.answer_photo(
        BufferedInputFile(image, filename="xider-server-load.png"),
        caption="CPU / RAM / диск за последние замеры",
    )
    await cq.answer("График готов")


@router.callback_query(OwnerFilter(), F.data == "server:terminal")
async def on_server_terminal(cq: CallbackQuery, state: FSMContext):
    await state.set_state(Form.wait_server_command)
    await cq.message.edit_text(
        "<b>SSH-команды сервера</b>\n"
        "Бот работает на этом VPS, поэтому отдельный SSH-ключ здесь не нужен.\n"
        "Разрешены только безопасные диагностические команды:\n"
        "<code>uptime</code>, <code>memory</code>, <code>disk</code>, "
        "<code>processes</code>, <code>service</code>, <code>logs</code>\n\n"
        "Пришли одно слово или нажми /cancel.",
        reply_markup=server_menu(cq.from_user.id),
    )
    await cq.answer()


@router.message(OwnerFilter(), Form.wait_server_command)
async def on_server_terminal_command(message: Message, state: FSMContext):
    await state.clear()
    command = (message.text or "").strip()
    result = await asyncio.to_thread(server_ops.run_terminal, command)
    access_store.append_audit(
        "server_terminal", actor_id=message.from_user.id,
        detail=f"command={command[:32]!r}; ok={result.ok}",
    )
    await message.answer(
        f"<b>Результат серверной команды</b>\n<pre>{html.escape(result.text[-3600:])}</pre>",
        reply_markup=server_menu(message.from_user.id),
    )


@router.callback_query(OwnerFilter(), F.data.startswith("server_confirm:"))
async def on_server_confirm(cq: CallbackQuery):
    action = cq.data.split(":", 1)[1]
    operations = {"restart": server_ops.restart, "update": server_ops.update, "rollback": server_ops.rollback}
    operation = operations.get(action)
    if operation is None:
        await cq.answer("Неизвестная операция", show_alert=True)
        return
    await cq.message.edit_text("⏳ Выполняю операцию. Это может занять до нескольких минут…")
    result = await asyncio.to_thread(operation)
    access_store.append_audit("server_operation", actor_id=cq.from_user.id, detail=f"action={action}; ok={result.ok}; code={result.code}")
    await cq.message.edit_text(
        f"<b>Серверная операция: {html.escape(action)}</b>\n"
        f"Результат: {'успешно' if result.ok else 'ошибка'}\n"
        f"<pre>{html.escape(result.text[-3500:])}</pre>",
        reply_markup=server_menu(cq.from_user.id),
    )
    await cq.answer("Готово" if result.ok else "Операция завершилась ошибкой", show_alert=not result.ok)


@router.callback_query(ReadOnlyFilter(), F.data == "menu:guest_devices")
async def on_guest_devices(cq: CallbackQuery):
    devs = devices.all()
    online = sum(1 for info in devs.values() if _status_dot(info) in ("🟢", "🟡"))
    await cq.message.edit_text(
        f"<b>Обзор устройств</b>\nОнлайн: <b>{online}/{len(devs)}</b>\n"
        "Это режим просмотра: управляющие кнопки отключены.",
        reply_markup=guest_devices_menu(),
    )
    await cq.answer()


@router.callback_query(ReadOnlyFilter(), F.data == "guest:readonly")
async def on_guest_readonly(cq: CallbackQuery):
    await cq.answer("Режим просмотра: эта кнопка недоступна гостю.", show_alert=True)


@router.callback_query(OwnerFilter(), F.data == "menu:admin")
async def on_menu_admin(cq: CallbackQuery):
    await cq.message.edit_text(
        "<b>Администрирование</b>\n"
        "Пользователи, роли, выданные права и журнал действий.\n"
        "Владелец защищён: его нельзя заблокировать, понизить или удалить.",
        reply_markup=admin_menu(),
    )
    await cq.answer()


@router.callback_query(OwnerFilter(), F.data == "admin:users")
async def on_admin_users(cq: CallbackQuery):
    users = access_store.list_users()
    await cq.message.edit_text(
        f"<b>Пользователи</b>\nВсего запусков: <b>{len(users)}</b>\n"
        "Открой пользователя, чтобы изменить роль, блокировку или разрешения.",
        reply_markup=admin_users_menu(),
    )
    await cq.answer()


@router.callback_query(OwnerFilter(), F.data == "admin:texts")
async def on_admin_texts(cq: CallbackQuery):
    await cq.message.edit_text(
        "<b>Тексты бота</b>\nВыбери сообщение, которое нужно заменить. Секреты сюда не сохраняются.",
        reply_markup=admin_texts_menu(),
    )
    await cq.answer()


@router.callback_query(OwnerFilter(), F.data.startswith("admin:text:"))
async def on_admin_text_edit(cq: CallbackQuery, state: FSMContext):
    key = cq.data.split(":", 2)[2]
    if key not in TEXT_LABELS:
        await cq.answer("Неизвестный текст", show_alert=True)
        return
    await state.set_state(Form.wait_bot_text)
    await state.update_data(bot_text_key=key)
    await cq.message.answer(
        f"Отправь новый текст для «{TEXT_LABELS[key]}».\n"
        f"Текущий: <code>{html.escape(text_store.get(key))}</code>\n"
        "Ограничение: 1–1000 символов. /cancel — отмена.",
        reply_markup=admin_texts_menu(),
    )
    await cq.answer()


@router.message(OwnerFilter(), Form.wait_bot_text)
async def on_admin_text_value(message: Message, state: FSMContext):
    data = await state.get_data()
    key = str(data.get("bot_text_key") or "")
    await state.clear()
    if key not in TEXT_LABELS:
        await message.answer("Редактирование устарело.", reply_markup=admin_menu())
        return
    try:
        text_store.set_text(key, message.text or "")
    except (KeyError, ValueError) as exc:
        await message.answer(str(exc), reply_markup=admin_texts_menu())
        return
    access_store.append_audit("bot_text_updated", actor_id=message.from_user.id, detail=key)
    await message.answer("Текст сохранён.", reply_markup=admin_texts_menu())


@router.callback_query(OwnerFilter(), F.data.startswith("admin:user:"))
async def on_admin_user(cq: CallbackQuery):
    raw = cq.data.rsplit(":", 1)[-1]
    if not raw.isdigit():
        await cq.answer("Некорректный пользователь", show_alert=True)
        return
    user_id = int(raw)
    record = access_store.get_user(user_id)
    if not record:
        await cq.answer("Пользователь не найден", show_alert=True)
        return
    role = access_store.get_role(user_id, ADMIN_ID)
    perms = record.get("permissions") or {}
    device_count = len(perms.get("devices") or [])
    button_count = len(perms.get("callbacks") or [])
    await cq.message.edit_text(
        f"<b>{html.escape(_user_label(record))}</b>\n"
        f"Telegram ID: <code>{user_id}</code>\n"
        f"Роль: <b>{html.escape(_role_label(role))}</b>\n"
        f"Устройств выдано: {device_count}; кнопок выдано: {button_count}.",
        reply_markup=admin_user_menu(user_id),
    )
    await cq.answer()


@router.callback_query(OwnerFilter(), F.data.startswith("admin:role:"))
async def on_admin_role(cq: CallbackQuery):
    parts = cq.data.split(":")
    if len(parts) != 4 or not parts[2].isdigit():
        await cq.answer("Некорректная роль", show_alert=True)
        return
    user_id, role = int(parts[2]), parts[3]
    if not access_store.set_role(cq.from_user.id, user_id, role, ADMIN_ID):
        await cq.answer("Владельца менять нельзя", show_alert=True)
        return
    await cq.answer(f"Роль: {_role_label(role)}")
    await on_admin_user(cq)


@router.callback_query(OwnerFilter(), F.data.startswith("admin:block:"))
async def on_admin_block(cq: CallbackQuery):
    raw = cq.data.rsplit(":", 1)[-1]
    if not raw.isdigit():
        await cq.answer("Некорректный пользователь", show_alert=True)
        return
    user_id = int(raw)
    blocked = access_store.get_role(user_id, ADMIN_ID) != Role.BLOCKED
    if not access_store.set_blocked(cq.from_user.id, user_id, blocked, ADMIN_ID):
        await cq.answer("Владельца блокировать нельзя", show_alert=True)
        return
    await cq.answer("Пользователь заблокирован" if blocked else "Пользователь разблокирован")
    await on_admin_user(cq)


@router.callback_query(OwnerFilter(), F.data.startswith("admin:devices:"))
async def on_admin_devices(cq: CallbackQuery):
    raw = cq.data.rsplit(":", 1)[-1]
    if not raw.isdigit():
        await cq.answer("Некорректный пользователь", show_alert=True)
        return
    await cq.message.edit_text(
        "<b>Доступ к устройствам</b>\n"
        "Зелёная кнопка означает, что устройство уже выдано пользователю.",
        reply_markup=admin_devices_menu(int(raw)),
    )
    await cq.answer()


@router.callback_query(OwnerFilter(), F.data.startswith("admin:device:"))
async def on_admin_device_toggle(cq: CallbackQuery):
    parts = cq.data.split(":", 3)
    if len(parts) != 4 or not parts[2].isdigit():
        await cq.answer("Некорректные данные", show_alert=True)
        return
    user_id, device_id = int(parts[2]), parts[3]
    if device_id not in devices.all():
        await cq.answer("Устройство уже не существует", show_alert=True)
        return
    enabled = access_store.toggle_device(cq.from_user.id, user_id, device_id, ADMIN_ID)
    await cq.answer("Устройство выдано" if enabled else "Доступ к устройству отозван")
    await cq.message.edit_text(
        "<b>Доступ к устройствам</b>\n"
        "Зелёная кнопка означает, что устройство уже выдано пользователю.",
        reply_markup=admin_devices_menu(user_id),
    )


@router.callback_query(OwnerFilter(), F.data.startswith("admin:perms:"))
async def on_admin_permissions(cq: CallbackQuery):
    raw = cq.data.rsplit(":", 1)[-1]
    if not raw.isdigit():
        await cq.answer("Некорректный пользователь", show_alert=True)
        return
    await cq.message.edit_text(
        "<b>Разрешения кнопок</b>\n"
        "Выдавай только нужные кнопки. «Полное управление» действует только на выданные устройства.",
        reply_markup=admin_permissions_menu(int(raw)),
    )
    await cq.answer()


@router.callback_query(OwnerFilter(), F.data.startswith("admin:perm:"))
async def on_admin_permission_toggle(cq: CallbackQuery):
    parts = cq.data.split(":", 3)
    if len(parts) != 4 or not parts[2].isdigit():
        await cq.answer("Некорректные данные", show_alert=True)
        return
    user_id, encoded = int(parts[2]), parts[3]
    callback = next((item for item in USER_PERMISSION_CHOICES if item.replace(":", "_") == encoded), None)
    if not callback:
        await cq.answer("Неизвестная кнопка", show_alert=True)
        return
    enabled = access_store.toggle_callback(cq.from_user.id, user_id, callback, ADMIN_ID)
    await cq.answer("Кнопка выдана" if enabled else "Кнопка отозвана")
    await cq.message.edit_text(
        "<b>Разрешения кнопок</b>\n"
        "Выдавай только нужные кнопки. «Полное управление» действует только на выданные устройства.",
        reply_markup=admin_permissions_menu(user_id),
    )


@router.callback_query(OwnerFilter(), F.data.startswith("admin:message:"))
async def on_admin_message(cq: CallbackQuery, state: FSMContext):
    raw = cq.data.rsplit(":", 1)[-1]
    if not raw.isdigit() or not access_store.get_user(int(raw)):
        await cq.answer("Пользователь не найден", show_alert=True)
        return
    await state.set_state(Form.wait_admin_message)
    await state.update_data(admin_message_user=int(raw))
    await cq.message.answer("Пришли текст сообщения. Он будет отправлен только выбранному пользователю.")
    await cq.answer()


@router.message(OwnerFilter(), Form.wait_admin_message)
async def on_admin_message_text(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    target_id = int(data.get("admin_message_user") or 0)
    text = (message.text or "").strip()
    if not target_id or not text:
        await message.answer("Сообщение не отправлено: пустой текст или пользователь не выбран.", reply_markup=admin_menu())
        return
    try:
        await bot.send_message(target_id, html.escape(text))
        access_store.append_audit("admin_message_sent", actor_id=message.from_user.id, target_id=target_id, detail=text)
        await message.answer("Сообщение отправлено.", reply_markup=admin_menu())
    except Exception:
        log.exception("Не удалось отправить администраторское сообщение")
        await message.answer("Telegram не принял сообщение: пользователь мог не запускать бота или заблокировать его.", reply_markup=admin_menu())


@router.callback_query(OwnerFilter(), F.data == "admin:audit")
async def on_admin_audit(cq: CallbackQuery):
    rows = access_store.recent_audit(12)
    if not rows:
        text = "<b>Журнал действий</b>\nЗаписей пока нет."
    else:
        lines = []
        for row in rows:
            moment = datetime.datetime.fromtimestamp(int(row.get("at") or 0)).strftime("%d.%m %H:%M")
            actor = row.get("actor_id") or "system"
            detail = html.escape(str(row.get("detail") or ""))
            lines.append(f"<code>{moment}</code> {html.escape(str(row.get('kind') or 'event'))} [{actor}] {detail}")
        text = "<b>Журнал действий</b>\n" + "\n".join(lines)
    await cq.message.edit_text(text[:3900], reply_markup=admin_menu())
    await cq.answer()


@router.callback_query(OwnerFilter(), F.data == "admin:style")
async def on_admin_style(cq: CallbackQuery):
    current = str(bot_settings.get("ui_style", "technical"))
    new_style = {"technical": "conversational", "conversational": "custom", "custom": "technical"}.get(current, "technical")
    labels = {"technical": "Технический режим", "conversational": "Разговорный режим", "custom": "Кастомный режим"}
    bot_settings.set_key("ui_style", new_style)
    access_store.append_audit("ui_style_set", actor_id=cq.from_user.id, detail=new_style)
    await cq.message.edit_text(
        "<b>Администрирование</b>\n"
        "Пользователи, роли, выданные права и журнал действий.\n"
        "Владелец защищён: его нельзя заблокировать, понизить или удалить.",
        reply_markup=admin_menu(),
    )
    await cq.answer(labels[new_style])

# =====================================================================
#  Хендлеры: навигация
# =====================================================================

@router.callback_query(AdminFilter(), F.data == "noop")
async def on_noop(cq: CallbackQuery):
    await cq.answer()


# ---------- Управление устройствами: переименование / удаление ----------

@router.callback_query(AdminFilter(), F.data.startswith("devmg:rename:"))
async def on_devmg_rename(cq: CallbackQuery, state: FSMContext):
    device_id = cq.data.split(":", 2)[2]
    await state.set_state(Form.wait_rename)
    await state.update_data(rename_device=device_id)
    await cq.message.answer(
        f"✏️ Пришлите новое имя для <b>{target_label(device_id)}</b> "
        f"(<code>{device_id}</code>).",
        reply_markup=back_to_device_kb(),
    )
    await cq.answer()


@router.message(AdminFilter(), Form.wait_rename)
async def on_rename_input(message: Message, state: FSMContext):
    data = await state.get_data()
    device_id = data.get("rename_device")
    name = (message.text or "").strip()
    await state.clear()
    if not device_id:
        await message.answer("⚠️ Не выбрано устройство.", reply_markup=main_menu())
        return
    if not name:
        await message.answer("⚠️ Пустое имя.", reply_markup=back_to_device_kb())
        return
    if devices.rename(device_id, name):
        audit("device_rename", device_id=device_id, name=name)
        await message.answer(
            f"✅ Устройство переименовано: <b>{html.escape(name)}</b>.",
            reply_markup=device_menu(device_id),
        )
    else:
        await message.answer("⚠️ Устройство не найдено.", reply_markup=main_menu())


@router.callback_query(AdminFilter(), F.data.startswith("devmg:delete:"))
async def on_devmg_delete(cq: CallbackQuery):
    device_id = cq.data.split(":", 2)[2]
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да, удалить!", callback_data=f"devmg:delok:{device_id}", style="danger")
    kb.button(text="❌ Отмена", callback_data="back:device", style="primary")
    kb.adjust(1)
    await cq.message.answer(
        f"🗑 Удалить <b>{target_label(device_id)}</b> из списка устройств? "
        "Оно появится снова при следующем подключении клиента.",
        reply_markup=kb.as_markup(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.startswith("devmg:delok:"))
async def on_devmg_delete_ok(cq: CallbackQuery):
    device_id = cq.data.split(":", 2)[2]
    name = target_label(device_id)
    if devices.remove(device_id):
        audit("device_remove", device_id=device_id)
        if SESSION.get("target") == device_id:
            SESSION["target"] = None
        await cq.message.answer(
            f"🗑 Устройство <b>{html.escape(name)}</b> удалено из списка.",
            reply_markup=devices_menu(),
        )
    else:
        await cq.message.answer(
            "⚠️ Устройство не найдено.", reply_markup=devices_menu()
        )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.startswith("devmg:uninstall:"))
async def on_devmg_uninstall(cq: CallbackQuery):
    device_id = cq.data.split(":", 2)[2]
    kb = InlineKeyboardBuilder()
    kb.button(text="🛑 Да, полностью удалить агент!", callback_data=f"devmg:uninstok:{device_id}", style="danger")
    kb.button(text="❌ Отмена", callback_data="back:device", style="primary")
    kb.adjust(1)
    await cq.message.answer(
        f"🛑 <b>Полное удаление агента с ПК {target_label(device_id)}!</b>\n\n"
        "⚠️ <b>Внимание:</b> Агент удалит свои ключи реестра автозагрузки, "
        "ярлыки из автозапуска, рабочую директорию, отправит финальное подтверждение и завершит работу.\n\n"
        "Вы уверены, что хотите деинсталлировать агент?",
        reply_markup=kb.as_markup(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.startswith("devmg:uninstok:"))
async def on_devmg_uninstall_ok(cq: CallbackQuery):
    device_id = cq.data.split(":", 2)[2]
    name = target_label(device_id)
    await cq.answer("🛑 Отправляю команду на удаление агента...")
    old_target = SESSION.get("target")
    SESSION["target"] = device_id
    fun_text_collector.reset()
    publish("uninstall_agent")
    result = await fun_text_collector.wait(10.0)

    devices.remove(device_id)
    if SESSION.get("target") == device_id or old_target == device_id:
        SESSION["target"] = None

    res_text = (result or {}).get("text") or "Команда отправлена. Агент удален с ПК и завершил процесс."
    audit("agent_uninstall", device_id=device_id)
    await cq.message.answer(
        f"🛑 <b>Агент удален с ПК:</b> {html.escape(name)}\n\n"
        f"<pre>{html.escape(str(res_text))}</pre>",
        reply_markup=devices_menu(),
    )


# ---------- Регистрация устройств: подтвердить / заблокировать ----------

@router.callback_query(AdminFilter(), F.data.startswith("devmg:allow:"))
async def on_devmg_allow(cq: CallbackQuery):
    device_id = cq.data.split(":", 2)[2]
    audit("device_approved", device_id=device_id)
    await cq.message.answer(
        f"✅ Устройство <b>{html.escape(target_label(device_id))}</b> подтверждено.\n"
        "Оно уже в списке — можно выбирать его целью.",
        reply_markup=main_menu(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.startswith("devmg:block:"))
async def on_devmg_block(cq: CallbackQuery):
    device_id = cq.data.split(":", 2)[2]
    kb = InlineKeyboardBuilder()
    kb.button(text="⛔ Да, заблокировать!", callback_data=f"devmg:blockok:{device_id}", style="danger")
    kb.button(text="❌ Отмена", callback_data="back:device", style="primary")
    kb.adjust(1)
    await cq.message.answer(
        f"⛔ Заблокировать <b>{html.escape(target_label(device_id))}</b>? "
        "Оно больше не будет появляться в списке и присылать статусы.",
        reply_markup=kb.as_markup(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.startswith("devmg:blockok:"))
async def on_devmg_blockok(cq: CallbackQuery):
    device_id = cq.data.split(":", 2)[2]
    name = target_label(device_id)
    bot_settings.block(device_id)
    devices.remove(device_id)
    if SESSION.get("target") == device_id:
        SESSION["target"] = None
    audit("device_blocked", device_id=device_id)
    await cq.message.answer(
        f"⛔ <b>{html.escape(name)}</b> заблокировано.",
        reply_markup=devices_menu(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.startswith("devmg:unblock:"))
async def on_devmg_unblock(cq: CallbackQuery):
    device_id = cq.data.split(":", 2)[2]
    if bot_settings.unblock(device_id):
        audit("device_unblocked", device_id=device_id)
        await cq.message.answer(
            f"➖ Устройство <code>{html.escape(device_id)}</code> разблокировано. "
            "Появится при следующем подключении.",
            reply_markup=blocked_menu(),
        )
    else:
        await cq.message.answer("⚠️ Не было в блок-листе.", reply_markup=blocked_menu())
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "devmg:blocked")
async def on_devmg_blocked(cq: CallbackQuery):
    s = bot_settings.all_settings()
    blocked = s.get("blocked_ids") or []
    if not blocked:
        text = "🚫 Нет заблокированных устройств."
    else:
        text = "🚫 <b>Заблокированные:</b>\n" + "\n".join(f"• <code>{html.escape(b)}</code>" for b in blocked)
    await cq.message.answer(text, reply_markup=blocked_menu())
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "devmg:clear_all")
async def on_devmg_clear_all(cq: CallbackQuery):
    kb = InlineKeyboardBuilder()
    kb.button(text="🧹 Да, очистить всё!", callback_data="devmg:clear_all_confirm", style="danger")
    kb.button(text="⬅️ Отмена", callback_data="menu:devices", style="primary")
    kb.adjust(1, 1)
    await cq.message.answer(
        "⚠️ <b>Внимание!</b> Вы действительно хотите очистить всю базу сохранённых устройств?\n"
        "Все оффлайн устройства будут удалены из базы (новые устройства добавятся при первом выходе в сеть).",
        reply_markup=kb.as_markup(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "devmg:clear_all_confirm")
async def on_devmg_clear_all_confirm(cq: CallbackQuery):
    devices.clear()
    SESSION["target"] = None
    await cq.message.answer("🧹 <b>База устройств полностью очищена!</b>", reply_markup=devices_menu())
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "devmg:manualadd")
async def on_devmg_manualadd(cq: CallbackQuery, state: FSMContext):
    await state.set_state(Form.wait_manual_add)
    await cq.message.answer(
        "➕ Пришлите ID устройства (12 hex-символов).\n"
        "ID виден в списке устройств или в логе клиента (~/.xgent/config.json).",
        reply_markup=back_to_device_kb(),
    )
    await cq.answer()


@router.message(AdminFilter(), Form.wait_manual_add)
async def on_manualadd_input(message: Message, state: FSMContext):
    await state.clear()
    device_id = (message.text or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{12}", device_id):
        await message.answer(
            "⚠️ ID должен быть 12 hex-символов (0-9, a-f).",
            reply_markup=back_to_device_kb(),
        )
        return
    info = devices.get(device_id)
    if info is None:
        devices.upsert(device_id, {
            "name": device_id, "os": "?", "version": "?",
            "online": False, "last_seen": 0, "approved": True,
        })
        audit("device_manual_add", device_id=device_id)
        await message.answer(
            f"➕ Устройство <code>{html.escape(device_id)}</code> добавлено вручную. "
            "Появится в списке, когда клиент подключится.",
            reply_markup=devices_menu(),
        )
    else:
        await message.answer(
            "ℹ️ Такое устройство уже есть в списке.",
            reply_markup=device_menu(device_id),
        )

@router.callback_query(ReadOnlyFilter(), F.data == "menu:main")
async def on_menu_main(cq: CallbackQuery):
    SESSION["target"] = None
    await cq.message.edit_text("Главное меню", reply_markup=main_menu(cq.from_user.id))
    await cq.answer()

@router.callback_query(AdminFilter(), F.data == "menu:devices")
async def on_menu_devices(cq: CallbackQuery):
    lines = []
    for device_id, info in sorted(devices.all().items()):
        ago = time.time() - float(info.get("last_seen", 0) or 0)
        state = "🟢 онлайн" if _status_dot(info) == "🟢" else "⚪ офлайн"
        os_name = info.get("os", "?")
        name = info.get("name", device_id)
        lines.append(
            f"{state} <b>{name}</b> ({device_id})\n"
            f"    ОС: {os_name}"
        )
    if lines:
        text = "💻 <b>Устройства</b>\n\n" + "\n".join(lines)
    else:
        text = "💻 Пока нет ни одного устройства.\nЗапустите клиент — оно появится само."
    await _replace_callback_message(cq, text, reply_markup=devices_menu())
    await cq.answer()

@router.callback_query(AdminFilter(), F.data == "menu:target")
async def on_menu_target(cq: CallbackQuery):
    if not devices.all():
        kb = InlineKeyboardBuilder()
        kb.button(text="🏠 Главное меню", callback_data="menu:main", style="primary")
        await cq.message.edit_text(
            "Устройств пока нет.\nЗапустите клиент — оно появится здесь.",
            reply_markup=kb.as_markup(),
        )
        await cq.answer()
        return
    await cq.message.edit_text("Выберите устройство:", reply_markup=devices_menu())
    await cq.answer()

@router.callback_query(AdminFilter(), F.data.startswith("dev:"))
async def on_dev(cq: CallbackQuery):
    device_id = cq.data.split(":", 1)[1]
    SESSION["target"] = device_id
    await cq.message.edit_text(
        device_card(device_id),
        reply_markup=device_menu(device_id),
    )
    await cq.answer()

@router.callback_query(AdminFilter(), F.data == "back:device")
async def on_back_to_device(cq: CallbackQuery):
    """Универсальная кнопка «Назад» — возврат в меню устройства."""
    target = SESSION.get("target")
    if not target:
        await cq.message.edit_text("🎛 <b>Главное меню</b>", reply_markup=main_menu())
    else:
        await cq.message.edit_text(
            device_card(target),
            reply_markup=device_menu(target),
        )
    await cq.answer()

# =====================================================================
#  Хендлеры: команды устройству
# =====================================================================

@router.callback_query(AdminFilter(), F.data == "cmd:url")
async def on_cmd_url(cq: CallbackQuery, state: FSMContext):
    if not SESSION.get("target"):
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    await state.set_state(Form.wait_url)
    await cq.message.answer(
        "🔗 Отправьте ссылку, например:\n<code>https://example.com</code>",
        reply_markup=back_to_device_kb(),
    )
    await cq.answer()

@router.callback_query(AdminFilter(), F.data == "cmd:text")
async def on_cmd_text(cq: CallbackQuery, state: FSMContext):
    if not SESSION.get("target"):
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    await state.set_state(Form.wait_text)
    await cq.message.answer(
        "📝 Отправьте текст — он появится на экране устройства.",
        reply_markup=back_to_device_kb(),
    )
    await cq.answer()

@router.callback_query(AdminFilter(), F.data == "cmd:sound")
async def on_cmd_sound(cq: CallbackQuery, state: FSMContext):
    if not SESSION.get("target"):
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    await state.set_state(Form.wait_sound)
    await cq.message.answer(
        "🔊 Отправьте текст для озвучки.\n"
        "Отправьте <code>beep</code> — прозвучит системный сигнал.",
        reply_markup=back_to_device_kb(),
    )
    await cq.answer()

@router.callback_query(AdminFilter(), F.data == "cmd:screenshot")
async def on_cmd_screenshot(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    if target == "all":
        await cq.answer("Скриншот — только для одного устройства", show_alert=True)
        return
    await cq.answer("📸 Делаю скриншот...")
    sent, command_id = publish_tracked("screenshot")
    if not sent:
        await _replace_callback_message(
            cq,
            "⚠️ Нет соединения с MQTT-брокером.",
            reply_markup=back_to_device_kb(),
        )
        return
    result = await screenshot_collector.wait_for(target, "screenshot", 15.0, command_id)
    if result is None:
        await _replace_callback_message(
            cq,
            "⏳ Скриншот не получен за 15 сек — устройство офлайн.",
            reply_markup=back_to_device_kb(),
        )
        return
    img_b64 = result.get("image")
    if not img_b64:
        await _replace_callback_message(
            cq,
            "⚠️ Устройство ответило, но скриншот пустой.",
            reply_markup=back_to_device_kb(),
        )
        return
    try:
        img_bytes = base64.b64decode(img_b64)
        photo = BufferedInputFile(img_bytes, filename="screenshot.png")
        await bot.send_photo(
            cq.from_user.id,
            photo=photo,
            caption=f"📸 <b>{target_label(target)}</b>",
            reply_markup=back_to_device_kb(),
        )
    except Exception:
        log.exception("Ошибка декодирования скриншота")
        await _replace_callback_message(
            cq,
            "⚠️ Ошибка обработки скриншота.",
            reply_markup=back_to_device_kb(),
        )


@router.callback_query(AdminFilter(), F.data == "cmd:webcam")
async def on_cmd_webcam(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    if target == "all":
        await cq.answer("Вебкамера — только для одного устройства", show_alert=True)
        return
    await cq.answer("📷 Делаю снимок с вебки (может занять пару секунд)...")
    sent, command_id = publish_tracked("webcam")
    if not sent:
        await _replace_callback_message(
            cq,
            "⚠️ Нет соединения с MQTT-брокером.",
            reply_markup=back_to_device_kb(),
        )
        return
    result = await webcam_collector.wait_for(target, "webcam", 20.0, command_id)
    if result is None:
        await _replace_callback_message(
            cq,
            "⏳ Снимок не получен за 20 сек — устройство офлайн или нет вебки.",
            reply_markup=back_to_device_kb(),
        )
        return
    img_b64 = result.get("image")
    if not img_b64:
        await _replace_callback_message(
            cq,
            "⚠️ Устройство ответило ошибкой или камера недоступна.",
            reply_markup=back_to_device_kb(),
        )
        return
    try:
        img_bytes = base64.b64decode(img_b64)
        photo = BufferedInputFile(img_bytes, filename="webcam.jpg")
        await bot.send_photo(
            cq.from_user.id,
            photo=photo,
            caption=f"📷 <b>{target_label(target)}</b>",
            reply_markup=back_to_device_kb(),
        )
    except Exception:
        log.exception("Ошибка декодирования снимка вебки")
        await _replace_callback_message(
            cq,
            "⚠️ Ошибка обработки снимка вебки.",
            reply_markup=back_to_device_kb(),
        )


@router.callback_query(AdminFilter(), F.data == "cmd:battery")
async def on_cmd_battery(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    await cq.answer("🔋 Запрашиваю батарею...")
    sent, command_id = publish_tracked("battery")
    if not sent:
        await _replace_callback_message(cq, "⚠️ Нет соединения с MQTT-брокером.", reply_markup=back_to_device_kb())
        return
    res = await battery_collector.wait_for(target, "battery", 15.0, command_id)
    if not res:
        await _replace_callback_message(cq, "⏳ Нет ответа.", reply_markup=back_to_device_kb())
        return
    if not res.get("available"):
        await _replace_callback_message(cq, "🔋 Батарея отсутствует на устройстве.", reply_markup=back_to_device_kb())
        return
    pct = res.get("percent", "?")
    state = res.get("state", "?")
    tl = res.get("time_left") or "Неизвестно"
    await _replace_callback_message(cq, f"🔋 <b>Батарея:</b> {pct}%\nСтатус: {state}\nОсталось: {tl}", reply_markup=back_to_device_kb())

@router.callback_query(AdminFilter(), F.data == "cmd:network")
async def on_cmd_network(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    await cq.answer("🌐 Запрашиваю сеть...")
    sent, command_id = publish_tracked("network")
    if not sent:
        await _replace_callback_message(cq, "⚠️ Нет соединения с MQTT-брокером.", reply_markup=back_to_device_kb())
        return
    res = await network_collector.wait_for(target, "network", 15.0, command_id)
    if not res:
        await _replace_callback_message(cq, "⏳ Нет ответа.", reply_markup=back_to_device_kb())
        return
    lines = []
    for i in res.get("interfaces", []):
        if i.get("up"):
            ip = i.get("ipv4") or i.get("ipv6") or "No IP"
            lines.append(f"• <b>{i['name']}</b>: {ip}")
    await _replace_callback_message(cq, "🌐 <b>Сеть:</b>\n" + ("\n".join(lines) if lines else "Нет активных интерфейсов"), reply_markup=back_to_device_kb())

@router.callback_query(AdminFilter(), F.data == "cmd:services")
async def on_cmd_services(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    await cq.answer("🛠 Запрашиваю службы...")
    sent, command_id = publish_tracked("services")
    if not sent:
        await _replace_callback_message(cq, "⚠️ Нет соединения с MQTT-брокером.", reply_markup=back_to_device_kb())
        return
    res = await services_collector.wait_for(target, "services", 15.0, command_id)
    if not res:
        await _replace_callback_message(cq, "⏳ Нет ответа.", reply_markup=back_to_device_kb())
        return
    tot = res.get("total", "?")
    run = res.get("running", "?")
    await _replace_callback_message(cq, f"🛠 <b>Службы:</b>\nВсего: {tot}\nЗапущено: {run}", reply_markup=back_to_device_kb())

@router.callback_query(AdminFilter(), F.data == "cmd:capabilities")
async def on_cmd_capabilities(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    await cq.answer("📊 Запрашиваю возможности...")
    sent, command_id = publish_tracked("capabilities")
    if not sent:
        await _replace_callback_message(cq, "⚠️ Нет соединения с MQTT-брокером.", reply_markup=back_to_device_kb())
        return
    res = await capabilities_collector.wait_for(target, "capabilities", 15.0, command_id)
    if not res:
        await _replace_callback_message(cq, "⏳ Нет ответа.", reply_markup=back_to_device_kb())
        return
    cmds = res.get("commands", [])
    await _replace_callback_message(cq, f"📊 <b>Поддерживаемые команды:</b>\n" + ", ".join(cmds), reply_markup=back_to_device_kb())

@router.callback_query(AdminFilter(), F.data == "cmd:sysinfo")
async def on_cmd_sysinfo(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    if target == "all":
        await cq.answer("Инфо — только для одного устройства", show_alert=True)
        return
    await cq.answer("💻 Запрашиваю...")
    sent, command_id = publish_tracked("sysinfo")
    if not sent:
        await _replace_callback_message(
            cq,
            "⚠️ Нет соединения с MQTT-брокером.",
            reply_markup=back_to_device_kb(),
        )
        return
    result = await sysinfo_collector.wait_for(target, "sysinfo", 12.0, command_id)
    if result is None:
        await _replace_callback_message(
            cq,
            "⏳ Ответ не получен за 12 сек — устройство офлайн.",
            reply_markup=back_to_device_kb(),
        )
        return
    info = devices.get(result.get("device_id")) or {}
    cpu = result.get("cpu_percent", "?")
    ram_used = result.get("ram_used_gb", "?")
    ram_total = result.get("ram_total_gb", "?")
    ram_pct = result.get("ram_percent", "?")
    disk_used = result.get("disk_used_gb", "?")
    disk_total = result.get("disk_total_gb", "?")
    disk_pct = result.get("disk_percent", "?")
    uptime_str = result.get("uptime", "?")
    await _replace_callback_message(
        cq,
        f"💻 <b>{info.get('name', '?')}</b>\n\n"
        f"🔲 CPU: {cpu}%\n"
        f"🧠 RAM: {ram_used} / {ram_total} ГБ ({ram_pct}%)\n"
        f"💾 Диск: {disk_used} / {disk_total} ГБ ({disk_pct}%)\n"
        f"⏱ Аптайм: {uptime_str}",
        reply_markup=back_to_device_kb(),
    )


@router.callback_query(AdminFilter(), F.data == "cmd:lock")
async def on_cmd_lock(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    if publish("lock"):
        await _replace_callback_message(
            cq,
            f"🔒 Экран заблокирован: <b>{target_label(target)}</b>",
            reply_markup=back_to_device_kb(),
        )
    else:
        await _replace_callback_message(
            cq,
            "⚠️ Нет соединения с MQTT-брокером.",
            reply_markup=back_to_device_kb(),
        )


@router.callback_query(AdminFilter(), F.data == "cmd:volume")
async def on_cmd_volume(cq: CallbackQuery):
    await simple_command(cq, "volume_toggle", "🔇", "Mute/Unmute звука", timeout=8.0)


@router.callback_query(AdminFilter(), F.data == "cmd:status")
async def on_cmd_status(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    await cq.answer("Запрашиваю статус...")
    if target == "all":
        if transport.publish_command("all", "status_request"):
            await _replace_callback_message(
                cq,
                "📡 Запрос отправлен всем устройствам.\n"
                "Обновлённый список — в «Список устройств».",
                reply_markup=system_menu(),
            )
        else:
            await _replace_callback_message(
                cq,
                "⚠️ Нет соединения с MQTT-брокером.",
                reply_markup=system_menu(),
            )
        return
    sent, command_id = publish_tracked("status_request")
    if not sent:
        await _replace_callback_message(
            cq,
            "⚠️ Нет соединения с MQTT-брокером.",
            reply_markup=system_menu(),
        )
        return
    try:
        await cq.message.edit_text(
            f"⏳ <b>Статус {html.escape(target_label(target))}</b>\n"
            "<code>Запрашиваю свежие данные у агента...</code>",
            reply_markup=system_menu(),
        )
    except Exception:
        pass
    status = await status_collector.wait_for(target, "status", 12.0, command_id)
    if status is None:
        await _replace_callback_message(
            cq,
            "⏳ Ответ не получен — устройство офлайн.",
            reply_markup=system_menu(),
        )
        return
    await _replace_callback_message(
        cq,
        device_card(target),
        reply_markup=system_menu(),
    )


@router.callback_query(AdminFilter(), F.data == "cmd:power_menu")
async def on_cmd_power_menu(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    await cq.message.edit_text(
        f"⚡ <b>Питание: {target_label(target)}</b>\n\n"
        "⚠️ Действие необратимо!",
        reply_markup=power_menu_new(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.startswith("power:"))
async def on_power_action(cq: CallbackQuery):
    action = cq.data.split(":", 1)[1]
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    labels = {"shutdown": "ВЫКЛЮЧИТЬ", "reboot": "ПЕРЕЗАГРУЗИТЬ", "sleep": "ОТПРАВИТЬ В СОН"}
    label = labels.get(action, action.upper())
    await cq.message.edit_text(
        f"⚠️ Вы уверены?\n\n"
        f"<b>{label}</b> устройство <b>{target_label(target)}</b>?",
        reply_markup=confirm_power_menu(action),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.startswith("power_confirm:"))
async def on_power_confirm(cq: CallbackQuery):
    action = cq.data.split(":", 1)[1]
    target = SESSION.get("target")
    if not target:
        await cq.answer("Цель не выбрана", show_alert=True)
        return
    fun_text_collector.reset()
    if publish("power", action=action):
        emojis = {"reboot": "🔄", "shutdown": "⚡", "sleep": "😴"}
        labels = {"reboot": "Перезагрузка", "shutdown": "Выключение", "sleep": "Сон"}
        emoji = emojis.get(action, "⚡")
        label = labels.get(action, action)
        await cq.answer(f"{emoji} {label} запущена...")
        res = await fun_text_collector.wait(5.0)
        msg = (res or {}).get("text") or f"{emoji} {label} отправлена: <b>{target_label(target)}</b>"
        await cq.message.edit_text(
            f"<b>{msg}</b>",
            reply_markup=back_to_device_kb(),
        )
    else:
        await cq.message.edit_text(
            "⚠️ Нет соединения с MQTT-брокером.",
            reply_markup=back_to_device_kb(),
        )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "cmd:stop")
async def on_cmd_stop(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    if publish("stop"):
        await _replace_callback_message(
            cq,
            f"⏹ Клиент остановлен: <b>{target_label(target)}</b>",
            reply_markup=back_to_device_kb(),
        )
    else:
        await _replace_callback_message(
            cq,
            "⚠️ Нет соединения с MQTT-брокером.",
            reply_markup=back_to_device_kb(),
        )
    await cq.answer()


# =====================================================================
#  Хендлеры: категории нового меню (9 разделов)
# =====================================================================

@router.callback_query(AdminFilter(), F.data == "cat:media")
async def on_cat_media(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите устройство", show_alert=True)
        return
    await cq.message.edit_text(
        f"📸 <b>Медиа & Зрение</b> · <b>{target_label(target)}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Съемка экрана, веб-камера, микрофон и управление звуком:",
        reply_markup=media_menu(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "cat:screen")
async def on_cat_screen(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите устройство", show_alert=True)
        return
    await cq.message.edit_text(
        f"🖥 <b>Экран & Дисплей</b> · <b>{target_label(target)}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Управление монитором, яркость, ночной режим, заставка и обои:",
        reply_markup=screen_menu(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.in_({"cat:input", "cat:control"}))
async def on_cat_input(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите устройство", show_alert=True)
        return
    await cq.message.edit_text(
        f"⌨️ <b>Ввод & Мышь</b> · <b>{target_label(target)}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Буфер обмена, удаленный ввод текста, горячие клавиши и манипуляции курсором:",
        reply_markup=input_menu(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "cat:system")
async def on_cat_system(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите устройство", show_alert=True)
        return
    await cq.message.edit_text(
        f"📊 <b>Система & Сенсоры</b> · <b>{target_label(target)}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Мониторинг ресурсов, статус батареи, здоровье SMART накопителей и софт:",
        reply_markup=system_menu(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "cat:network")
async def on_cat_network(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите устройство", show_alert=True)
        return
    await cq.message.edit_text(
        f"🌐 <b>Сеть & Коннект</b> · <b>{target_label(target)}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Внешний IP, интерфейсы, сохранённые сети Wi-Fi, USB и Bluetooth:",
        reply_markup=network_menu(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.in_({"cat:files", "files:menu"}))
async def on_cat_files(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target or target == "all":
        await cq.answer("Файлы доступны только для конкретного устройства", show_alert=True)
        return
    await cq.message.edit_text(
        f"📁 <b>Файлы & Диски</b> · <b>{target_label(target)}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Просмотр папок, поиск, скачивание и загрузка файлов:",
        reply_markup=files_menu(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.in_({"cat:terminal", "cat:fun", "fun:menu"}))
async def on_cat_terminal(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите устройство", show_alert=True)
        return
    await cq.message.edit_text(
        f"🛠 <b>Терминал & Процессы</b> · <b>{target_label(target)}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Диспетчер задач, убийство процессов, shell-команды, автозагрузка и службы:",
        reply_markup=terminal_menu(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "cat:power")
async def on_cat_power(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите устройство", show_alert=True)
        return
    await cq.message.edit_text(
        f"🔒 <b>Питание & Защита</b> · <b>{target_label(target)}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Блокировка экрана, сон, перезагрузка, выключение и Wake-on-LAN:",
        reply_markup=power_menu_new(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "cat:pranks")
async def on_cat_pranks(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите устройство", show_alert=True)
        return
    await cq.message.edit_text(
        f"🎭 <b>Приколы & Розыгрыши</b> · <b>{target_label(target)}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Скримеры, рикролл x50, матрица, сирена, танцующие окна и фейковые экраны:",
        reply_markup=pranks_menu(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "cat:device")
async def on_cat_device(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите устройство", show_alert=True)
        return
    await cq.message.edit_text(
        f"⚙️ <b>Настройки ПК</b> · <b>{target_label(target)}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Переименование, избранные кнопки, история и блокировка:",
        reply_markup=device_settings_menu(),
    )

    await cq.answer()


# ---------- Процессы: пагинация + kill по кнопке ----------

@router.callback_query(AdminFilter(), F.data.startswith("proc:"))
async def on_proc_page(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target or target == "all":
        await cq.answer("Процессы — только для одного устройства", show_alert=True)
        return
    refresh = cq.data == "proc:refresh"
    page = 0 if refresh else int(cq.data.split(":", 1)[1] or 0)
    cache = None if refresh else LAST_PROCESSES.get(target)
    if not cache or time.time() - cache["ts"] > 30:
        await cq.answer("⚙️ Запрашиваю процессы...")
        processes_collector.reset()
        if not transport.publish_command(target, "processes", top_n=15):
            await cq.message.answer("⚠️ Нет соединения с MQTT-брокером.")
            return
        res = await processes_collector.wait(15.0)
        if not res:
            await cq.message.answer(
                "⏳ Процессы не получены — устройство офлайн.",
                reply_markup=back_to_device_kb(),
            )
            await cq.answer()
            return
        cache = {"ts": time.time(), "items": res.get("top", [])}
        LAST_PROCESSES[target] = cache
    items = cache.get("items", [])
    per = 8
    pages = max(1, (len(items) + per - 1) // per)
    page = max(0, min(page, pages - 1))
    chunk = items[page * per:(page + 1) * per]
    kb = InlineKeyboardBuilder()
    for it in chunk:
        label = f"🔪 {it.get('name', '?')} · {it.get('mem_percent', 0)}%"
        kb.button(text=label, callback_data=f"killq:{it.get('pid')}", style="danger")
    if pages > 1:
        if page > 0:
            kb.button(text="◀️", callback_data=f"proc:{page - 1}", style="primary")
        kb.button(text=f"{page + 1}/{pages}", callback_data="noop", style="primary")
        if page < pages - 1:
            kb.button(text="▶️", callback_data=f"proc:{page + 1}", style="primary")
    kb.button(text="🔄 Обновить", callback_data="proc:refresh", style="success")
    kb.button(text="⬅️ К устройству", callback_data="back:device", style="primary")
    kb.adjust(1)
    text = f"⚙️ <b>Процессы</b> · {html.escape(target_label(target))} (топ-{len(items)})"
    try:
        await cq.message.edit_text(text, reply_markup=kb.as_markup())
    except Exception:
        await cq.message.answer(text, reply_markup=kb.as_markup())
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.startswith("killq:"))
async def on_kill_confirm(cq: CallbackQuery):
    pid = cq.data.split(":", 1)[1]
    await cq.message.answer(
        f"🔪 Убить процесс <code>{html.escape(pid)}</code>?",
        reply_markup=confirm_kb(f"kill:{pid}", yes_text="✅ Да, убить!"),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.startswith("kill:"))
async def on_kill(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target or target == "all":
        await cq.answer("Цель не выбрана", show_alert=True)
        return
    pid = cq.data.split(":", 1)[1]
    if publish("kill_process", pid=pid):
        await cq.message.answer(f"🔪 Команда kill отправлена (pid {pid}).")
    else:
        await cq.message.answer("⚠️ Нет соединения с MQTT-брокером.")
    await cq.answer()


# ---------- Массовые команды (все устройства) ----------

@router.callback_query(AdminFilter(), F.data == "all:status")
async def on_all_status(cq: CallbackQuery):
    multi_status.reset()
    if not transport.publish_command("all", "status_request"):
        await cq.message.answer("⚠️ Нет соединения с MQTT-брокером.")
        await cq.answer()
        return
    await cq.answer("📡 Опрашиваю все устройства...")
    results = await multi_status.wait_window(6.0)
    known = devices.all()
    lines = []
    answered = 0
    for dev_id, info in sorted(known.items()):
        r = next((x for x in results if x.get("device_id") == dev_id), None)
        name = html.escape(info.get("name") or dev_id)
        if r:
            answered += 1
            lines.append(f"{_status_dot(info)} {name} · ✅ ответил · v{r.get('version', '?')}")
        else:
            lines.append(f"{_status_dot(info)} {name} · ⛔ молчит")
    text = (
        f"🖥 <b>Сводка</b> — ответили {answered}/{len(known)}\n\n"
        + "\n".join(lines if lines else ["Нет известных устройств."])
    )
    await cq.message.answer(text, reply_markup=devices_menu())
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "all:screenshot")
async def on_all_screenshot(cq: CallbackQuery):
    multi_screenshot.reset()
    if not transport.publish_command("all", "screenshot"):
        await cq.message.answer("⚠️ Нет соединения с MQTT-брокером.")
        await cq.answer()
        return
    await cq.answer("📸 Собираю скриншоты...")
    results = await multi_screenshot.wait_window(14.0)
    sent = 0
    for r in results[:6]:
        img = r.get("image")
        if not img:
            continue
        try:
            photo = BufferedInputFile(base64.b64decode(img), filename="screenshot.jpg")
            await bot.send_photo(
                ADMIN_ID,
                photo=photo,
                caption=f"📸 {html.escape(target_label(r.get('device_id', '')))}",
            )
            sent += 1
        except Exception:
            log.exception("Не удалось отправить скриншот")
    await cq.message.answer(f"📸 Скриншоты собраны: {sent}.", reply_markup=devices_menu())
    await cq.answer()


# ---------- Wake-on-LAN, диски, громкость, микрофон, буфер ----------

@router.callback_query(AdminFilter(), F.data == "cmd:wol")
async def on_cmd_wol(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target or target == "all":
        await cq.answer("Разбудить можно только одно устройство", show_alert=True)
        return
    info = devices.get(target) or {}
    mac = info.get("mac")
    if not mac:
        await cq.answer("MAC неизвестен — запрашиваю у устройства...")
        sysinfo_collector.reset()
        if not transport.publish_command(target, "sysinfo"):
            await cq.message.answer("⚠️ Нет соединения с MQTT-брокером.")
            await cq.answer()
            return
        res = await sysinfo_collector.wait(12.0)
        mac = (res or {}).get("mac")
        if mac:
            devices.upsert(target, {"mac": mac})
    if not mac:
        await cq.message.answer("❌ MAC-адрес неизвестен: устройство ни разу не отвечало.")
        await cq.answer()
        return
    try:
        send_wol(mac)
    except ValueError:
        await cq.message.answer(f"❌ Некорректный MAC: <code>{html.escape(str(mac))}</code>")
        await cq.answer()
        return
    audit("wol", device_id=target, mac=mac)
    await cq.message.answer(
        f"🌐 Magic packet отправлен на <b>{html.escape(target_label(target))}</b> ({mac})."
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "cmd:disks")
async def on_cmd_disks(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target or target == "all":
        await cq.answer("Только для одного устройства", show_alert=True)
        return
    await cq.answer("🗂 Запрашиваю диски...")
    sent, command_id = publish_tracked("disks")
    if not sent:
        await cq.message.answer("⚠️ Нет соединения с MQTT-брокером.")
        await cq.answer()
        return
    res = await disks_collector.wait_for(target, "disks", 15.0, command_id)
    if not res:
        await cq.message.answer("⏳ Нет ответа.", reply_markup=back_to_device_kb())
        await cq.answer()
        return
    lines = res.get("lines", [])
    text = "🗂 <b>Диски</b>:\n" + "\n".join(html.escape(l) for l in lines)
    await cq.message.answer(text, reply_markup=back_to_device_kb())
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "vol:opts")
async def on_vol_opts(cq: CallbackQuery):
    await cq.message.edit_text("🎚 <b>Громкость</b>", reply_markup=vol_options_menu())
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.startswith("volq:"))
async def on_vol_set(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target or target == "all":
        await cq.answer("Только для одного устройства", show_alert=True)
        return
    level = int(cq.data.split(":", 1)[1] or 50)
    await simple_command(cq, "volume_set", "🎚", f"Громкость {level}%", timeout=8.0, level=level)


@router.callback_query(AdminFilter(), F.data == "mic:opts")
async def on_mic_opts(cq: CallbackQuery):
    await cq.message.edit_text(
        "🎙 <b>Микрофон</b> — выбери длительность:",
        reply_markup=mic_options_menu(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.startswith("micdur:"))
async def on_mic_dur(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target or target == "all":
        await cq.answer("Только для одного устройства", show_alert=True)
        return
    dur = int(cq.data.split(":", 1)[1] or 5)
    await cq.answer(f"🎙 Запись {dur} сек...")
    sent, command_id = publish_tracked("mic", duration=dur)
    if not sent:
        await cq.message.answer("⚠️ Нет соединения с MQTT-брокером.")
        await cq.answer()
        return
    res = await mic_collector.wait_for(target, "mic", dur + 12.0, command_id)
    if not res or not res.get("audio"):
        await cq.message.answer("⏳ Звук не получен — офлайн или нет микрофона.")
        await cq.answer()
        return
    try:
        audio = BufferedInputFile(base64.b64decode(res.get("audio")), filename="mic.ogg")
        await bot.send_voice(
            cq.from_user.id,
            voice=audio,
            caption=f"🎙 {html.escape(target_label(target))} · {dur}с",
        )
    except Exception:
        log.exception("Не удалось отправить аудио")
        await cq.message.answer("⚠️ Ошибка отправки звука.")
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "cmd:clipset")
async def on_cmd_clipset(cq: CallbackQuery, state: FSMContext):
    if not SESSION.get("target"):
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    await state.set_state(Form.wait_clipset)
    await cq.message.answer(
        "📥 Отправь текст — он попадёт в буфер обмена устройства.",
        reply_markup=back_to_device_kb(),
    )
    await cq.answer()


@router.message(AdminFilter(), Form.wait_clipset)
async def on_clipset_input(message: Message, state: FSMContext):
    await state.clear()
    text = (message.text or "").strip()
    if not text:
        await message.answer("⚠️ Пустой текст.", reply_markup=back_to_device_kb())
        return
    if publish("clipboard_set", text=text):
        await message.answer("📥 Текст записан в буфер обмена устройства.")
    else:
        await message.answer("⚠️ Нет соединения с MQTT-брокером.")


# ---------- События (уведомления) ----------

@router.callback_query(AdminFilter(), F.data == "ev:server_autostart:toggle")
async def on_server_autostart_toggle(cq: CallbackQuery):
    is_on, msg = toggle_server_autostart()
    await cq.answer(msg, show_alert=True)
    await cq.message.edit_text(
        "🔔 <b>События & Настройки</b> — что присылать тебе в TG:",
        reply_markup=events_menu(),
    )


@router.callback_query(AdminFilter(), F.data == "ev:menu")
async def on_ev_menu(cq: CallbackQuery):
    await cq.message.edit_text(
        "🔔 <b>События</b> — что присылать тебе в TG:",
        reply_markup=events_menu(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.startswith("ev:toggle:"))
async def on_ev_toggle(cq: CallbackQuery):
    key = cq.data.split(":", 2)[2]
    new_val = bot_settings.toggle(key)
    audit("settings_toggle", key=key, value=new_val)
    await cq.message.edit_text(
        "🔔 <b>События</b> — что присылать тебе в TG:",
        reply_markup=events_menu(),
    )
    await cq.answer("Включено ✅" if new_val else "Выключено ❌")


# ---------- Тихие часы / дайджест / администраторы ----------

@router.callback_query(AdminFilter(), F.data == "ev:quiet")
async def on_ev_quiet(cq: CallbackQuery):
    await cq.message.edit_text(
        "🌙 <b>Тихие часы</b> — уведомления не приходят в это окно:",
        reply_markup=quiet_hours_menu(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.startswith("quiet:"))
async def on_quiet_set(cq: CallbackQuery):
    parts = cq.data.split(":")
    fr = parts[1] if len(parts) > 1 else ""
    to = parts[2] if len(parts) > 2 else ""
    bot_settings.set_key("quiet_from", fr)
    bot_settings.set_key("quiet_to", to)
    audit("quiet_hours_set", frm=fr, to=to)
    if fr == "" or to == "":
        await cq.answer("🌙 Тихие часы выключены")
    else:
        await cq.answer(f"🌙 Тихие часы: {fr}:00 – {to}:00")
    await cq.message.edit_text(
        "🌙 <b>Тихие часы</b> — уведомления не приходят в это окно:",
        reply_markup=quiet_hours_menu(),
    )


@router.callback_query(AdminFilter(), F.data == "ev:digest")
async def on_ev_digest(cq: CallbackQuery):
    await cq.message.edit_text(
        "🗞 <b>Ежедневный дайджест</b> — сводка по всем устройствам:",
        reply_markup=digest_menu(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.startswith("digest:"))
async def on_digest_set(cq: CallbackQuery):
    val = cq.data.split(":", 1)[1]
    bot_settings.set_key("report_hour", val)
    audit("digest_hour_set", hour=val)
    await cq.answer("🗞 Дайджест установлен" if val else "🗞 Дайджест выключен")
    await cq.message.edit_text(
        "🗞 <b>Ежедневный дайджест</b> — сводка по всем устройствам:",
        reply_markup=digest_menu(),
    )


@router.callback_query(AdminFilter(), F.data == "ev:admins")
async def on_ev_admins(cq: CallbackQuery):
    await cq.message.edit_text(
        "👥 <b>Администраторы</b> — кто может управлять через бота:",
        reply_markup=admins_menu(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "adm:add")
async def on_adm_add(cq: CallbackQuery, state: FSMContext):
    await state.set_state(Form.wait_admin)
    await cq.message.answer(
        "➕ Пришлите Telegram ID нового администратора.\n"
        "Узнать свой ID: @userinfobot. Пример: <code>123456789</code>",
        reply_markup=back_to_device_kb(),
    )
    await cq.answer()


@router.message(AdminFilter(), Form.wait_admin)
async def on_admin_input(message: Message, state: FSMContext):
    await state.clear()
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer(
            "⚠️ ID должен быть числом.", reply_markup=back_to_device_kb()
        )
        return
    uid = int(raw)
    try:
        member = await bot.get_chat(uid)
        label = member.username or f"id{uid}"
    except Exception:
        label = f"id{uid}"
    if bot_settings.add_admin(uid):
        audit("admin_added", user_id=uid)
        await message.answer(
            f"✅ <b>{html.escape(str(label))}</b> добавлен администратором.",
            reply_markup=admins_menu(),
        )
    else:
        await message.answer(
            "ℹ️ Уже есть в списке.", reply_markup=admins_menu()
        )


@router.callback_query(AdminFilter(), F.data.startswith("adm:rm:"))
async def on_adm_rm(cq: CallbackQuery):
    raw = cq.data.split(":", 2)[2]
    if raw.isdigit() and bot_settings.remove_admin(int(raw)):
        audit("admin_removed", user_id=raw)
        await cq.message.edit_text(
            "👥 <b>Администраторы</b> — кто может управлять через бота:",
            reply_markup=admins_menu(),
        )
        await cq.answer("➖ Удалён")
    else:
        await cq.answer("⚠️ Не найден")


# ---------- Избранное ----------

@router.callback_query(AdminFilter(), F.data == "fav:menu")
async def on_fav_menu(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target or target == "all":
        await cq.answer("Только для одного устройства", show_alert=True)
        return
    favs = devices.get_favorites(target)
    await cq.message.edit_text(
        f"⭐ <b>Избранное</b> · {html.escape(target_label(target))}\n"
        "Тапни, чтобы добавить/убрать. Избранное появится в меню устройства.",
        reply_markup=_favorites_kb(target, favs),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.startswith("fav:toggle:"))
async def on_fav_toggle(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target or target == "all":
        await cq.answer("Только для одного устройства", show_alert=True)
        return
    action = cq.data.split(":", 2)[2]
    added = devices.toggle_favorite(target, action)
    favs = devices.get_favorites(target)
    await cq.message.edit_text(
        f"⭐ <b>Избранное</b> · {html.escape(target_label(target))}\n"
        "Тапни, чтобы добавить/убрать. Избранное появится в меню устройства.",
        reply_markup=_favorites_kb(target, favs),
    )
    await cq.answer("Добавлено ⭐" if added else "Убрано")


# ---------- История команд ----------

@router.callback_query(AdminFilter(), F.data == "hist:0")
async def on_history(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target or target == "all":
        await cq.answer("Только для одного устройства", show_alert=True)
        return
    hist = HISTORY.get(target, [])
    kb = InlineKeyboardBuilder()
    if not hist:
        text = "🕘 История команд пуста."
    else:
        text = (
            "🕘 <b>Последние команды</b> (нажми, чтобы повторить):\n"
            + "\n".join(
                f"• {html.escape(ACTION_LABELS.get(a, a))}"
                for a, _kw, _ts in reversed(hist[-8:])
            )
        )
        for i, (action, _kw, _ts) in enumerate(hist):
            kb.button(
                text=f"↻ {ACTION_LABELS.get(action, action)}",
                callback_data=f"rep:{i}",
                style="primary",
            )
    kb.button(text="⬅️ К устройству", callback_data="back:device", style="primary")
    kb.adjust(1)
    await cq.message.answer(text, reply_markup=kb.as_markup())
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.startswith("rep:"))
async def on_repeat(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target or target == "all":
        await cq.answer("Цель не выбрана", show_alert=True)
        return
    hist = HISTORY.get(target, [])
    try:
        idx = int(cq.data.split(":", 1)[1])
    except ValueError:
        await cq.answer()
        return
    if not (0 <= idx < len(hist)):
        await cq.answer("Команда уже не в истории", show_alert=True)
        return
    action, kwargs, _ = hist[idx]
    if transport.publish_command(target, action, **kwargs):
        await cq.answer(f"↻ Повторяю: {ACTION_LABELS.get(action, action)}")
    else:
        await cq.message.answer("⚠️ Нет соединения с MQTT-брокером.")
        await cq.answer()


# ---------- Подтверждения опасных действий ----------

CONFIRM_PROMPTS = {
    "shell": ("💻 Выполнить терминал на устройстве?", "shell:ok"),
    "stop": ("⏹ Остановить клиента на устройстве?", "stop:ok"),
    "stop_all": ("⏹ Остановить клиенты на ВСЕХ устройствах?", "stopall:ok"),
    "open_app": ("🚀 Открыть диалог запуска программы?", "openapp:ok"),
    "sleep": ("😴 Отправить устройство в сон?", "sleep:ok"),
}


@router.callback_query(AdminFilter(), F.data.startswith("cfm:"))
async def on_confirm_action(cq: CallbackQuery):
    kind = cq.data.split(":", 1)[1]
    if kind not in CONFIRM_PROMPTS:
        await cq.answer()
        return
    if not SESSION.get("target"):
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    text, yes_cb = CONFIRM_PROMPTS[kind]
    await cq.message.answer(f"⚠️ {text}", reply_markup=confirm_kb(yes_cb, yes_text="✅ Да!"))
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "shell:ok")
async def on_shell_ok(cq: CallbackQuery, state: FSMContext):
    await state.set_state(Form.wait_shell)
    await cq.message.answer(
        "💻 Отправьте команду терминала.\nНапример: <code>whoami</code> или <code>dir</code>",
        reply_markup=back_to_device_kb(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "openapp:ok")
async def on_openapp_ok(cq: CallbackQuery, state: FSMContext):
    await state.set_state(Form.wait_open_app)
    await cq.message.answer(
        "🚀 Отправьте название программы или путь к файлу.\nНапример: <code>calc</code>",
        reply_markup=back_to_device_kb(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "stop:ok")
async def on_stop_ok(cq: CallbackQuery):
    if publish("stop"):
        await _replace_callback_message(
            cq,
            f"⏹ Клиент остановлен: <b>{target_label(SESSION.get('target'))}</b>",
            reply_markup=back_to_device_kb(),
        )
    else:
        await _replace_callback_message(
            cq,
            "⚠️ Нет соединения с MQTT-брокером.",
            reply_markup=back_to_device_kb(),
        )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "stopall:ok")
async def on_stopall_ok(cq: CallbackQuery):
    if transport.publish_command("all", "stop"):
        await cq.message.answer("⏹ Команда остановки отправлена всем.")
    else:
        await cq.message.answer("⚠️ Нет соединения с MQTT-брокером.")
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "sleep:ok")
async def on_sleep_ok(cq: CallbackQuery):
    if publish("power", action="sleep"):
        await cq.message.answer(
            f"😴 Сон отправлен: <b>{target_label(SESSION.get('target'))}</b>"
        )
    else:
        await cq.message.answer("⚠️ Нет соединения с MQTT-брокером.")
    await cq.answer()


# =====================================================================
#  Файлы и папки (волна новых команд)
# =====================================================================

def _require_target(cq: CallbackQuery) -> str | None:
    target = SESSION.get("target")
    if not target or target == "all":
        return None
    return target


@router.callback_query(AdminFilter(), F.data == "files:menu")
async def on_files_menu(cq: CallbackQuery):
    if not _require_target(cq):
        await cq.answer("Выберите конкретное устройство (не «все»)", show_alert=True)
        return
    await cq.message.edit_text(
        f"📁 Файлы: <b>{target_label(SESSION['target'])}</b>",
        reply_markup=files_menu(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "files:list")
async def on_files_list(cq: CallbackQuery, state: FSMContext):
    if not _require_target(cq):
        await cq.answer("Выберите устройство", show_alert=True)
        return
    await state.set_state(Form.wait_path)
    await state.update_data(path_mode="list")
    await cq.message.answer(
        "📂 Отправьте путь к папке, например:\n<code>C:\\Users</code> или <code>~/Downloads</code>",
        reply_markup=back_to_device_kb(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "files:find")
async def on_files_find(cq: CallbackQuery, state: FSMContext):
    if not _require_target(cq):
        await cq.answer("Выберите устройство", show_alert=True)
        return
    await state.set_state(Form.wait_find)
    await cq.message.answer(
        "🔍 Отправьте что искать (часть имени файла):\n<code>report.pdf</code> или <code>отчёт</code>",
        reply_markup=back_to_device_kb(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "files:get")
async def on_files_get(cq: CallbackQuery, state: FSMContext):
    if not _require_target(cq):
        await cq.answer("Выберите устройство", show_alert=True)
        return
    await state.set_state(Form.wait_path)
    await state.update_data(path_mode="get")
    await cq.message.answer(
        "📥 Отправьте полный путь к файлу, который забрать:\n<code>C:\\Users\\me\\report.pdf</code>",
        reply_markup=back_to_device_kb(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "files:put")
async def on_files_put(cq: CallbackQuery, state: FSMContext):
    if not _require_target(cq):
        await cq.answer("Выберите устройство", show_alert=True)
        return
    await state.set_state(Form.wait_path)
    await state.update_data(path_mode="put")
    await cq.message.answer(
        "📤 Отправьте файл (документом) — он будет сохранён в Downloads на устройстве.",
        reply_markup=back_to_device_kb(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "files:del")
async def on_files_del(cq: CallbackQuery, state: FSMContext):
    if not _require_target(cq):
        await cq.answer("Выберите устройство", show_alert=True)
        return
    await state.set_state(Form.wait_path)
    await state.update_data(path_mode="del")
    await cq.message.answer(
        "🗑 Отправьте полный путь к файлу, который удалить. Это необратимо!",
        reply_markup=back_to_device_kb(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "files:open")
async def on_files_open(cq: CallbackQuery, state: FSMContext):
    if not _require_target(cq):
        await cq.answer("Выберите устройство", show_alert=True)
        return
    await state.set_state(Form.wait_path)
    await state.update_data(path_mode="open")
    await cq.message.answer(
        "🖥 Отправьте путь, который открыть (папка или файл):\n<code>C:\\Users\\me\\Desktop</code>",
        reply_markup=back_to_device_kb(),
    )
    await cq.answer()


@router.message(AdminFilter(), Form.wait_path)
async def on_path_input(message: Message, state: FSMContext):
    data = await state.get_data()
    mode = data.get("path_mode", "list")
    await state.clear()
    target = SESSION.get("target")
    if not target or target == "all":
        await message.answer("Цель не выбрана.", reply_markup=back_to_device_kb())
        return

    if mode == "put":
        doc = message.document
        if not doc:
            await message.answer("⚠️ Отправьте файл документом.", reply_markup=back_to_device_kb())
            return
        buf = await bot.download(doc)
        chunk = buf.read()
        name = doc.file_name or "file.bin"
        dest = f"~/Downloads/{name}"
        if len(chunk) > 30 * 1024 * 1024:
            await message.answer("⚠️ Файл > 30 МБ не влезет в MQTT-сообщение.", reply_markup=back_to_device_kb())
            return
        fun_text_collector.reset()
        if not publish("file_put", name=name, path=dest, b64=base64.b64encode(chunk).decode("ascii")):
            await message.answer("⚠️ Нет соединения с MQTT-брокером.", reply_markup=back_to_device_kb())
            return
        result = await fun_text_collector.wait(25.0)
        if result and result.get("ok"):
            await message.answer(f"✅ Сохранено: <code>{html.escape(str(result.get('path') or dest))}</code>", reply_markup=back_to_device_kb())
        else:
            await message.answer(f"⚠️ {html.escape(str((result or {}).get('error') or 'Нет ответа от устройства'))}", reply_markup=back_to_device_kb())
        return

    path = (message.text or "").strip()
    if not path:
        await message.answer("⚠️ Пустой путь.", reply_markup=back_to_device_kb())
        return

    if mode == "list":
        fun_text_collector.reset()
        if not publish("dir_list", path=path):
            await message.answer("⚠️ Нет соединения с MQTT-брокером.", reply_markup=back_to_device_kb())
            return
        result = await fun_text_collector.wait(15.0)
        text = (result or {}).get("text") or "⏳ Нет ответа от устройства"
        await message.answer(f"<pre>{html.escape(str(text)[:3500])}</pre>", reply_markup=back_to_device_kb())
    elif mode == "get":
        file_collector.reset()
        if not publish("file_get", path=path):
            await message.answer("⚠️ Нет соединения с MQTT-брокером.", reply_markup=back_to_device_kb())
            return
        result = await file_collector.wait(30.0)
        if not result:
            await message.answer("⏳ Файл не получен (нет ответа / слишком большой).", reply_markup=back_to_device_kb())
            return
        if not result.get("ok", False):
            await message.answer(f"⚠️ {html.escape(str(result.get('error') or 'Устройство не смогло прочитать файл.'))}", reply_markup=back_to_device_kb())
            return
        encoded = result.get("data") or result.get("b64") or ""
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            await message.answer("⚠️ Устройство вернуло повреждённый файл.", reply_markup=back_to_device_kb())
            return
        await message.answer_document(
            BufferedInputFile(data, filename=result.get("filename") or result.get("name") or "file.bin"),
            caption=f"📥 Файл с <b>{target_label(target)}</b>",
        )
    elif mode == "del":
        fun_text_collector.reset()
        if not publish("file_del", path=path):
            await message.answer("⚠️ Нет соединения с MQTT-брокером.", reply_markup=back_to_device_kb())
            return
        result = await fun_text_collector.wait(15.0)
        if result and result.get("ok"):
            await message.answer(f"🗑 Удалено: <code>{html.escape(path)}</code>", reply_markup=back_to_device_kb())
        else:
            await message.answer(f"⚠️ {html.escape(str((result or {}).get('error') or 'Нет ответа'))}", reply_markup=back_to_device_kb())
    elif mode == "open":
        fun_text_collector.reset()
        if not publish("path_open", path=path):
            await message.answer("⚠️ Нет соединения с MQTT-брокером.", reply_markup=back_to_device_kb())
            return
        result = await fun_text_collector.wait(10.0)
        if result and result.get("ok"):
            await message.answer(f"🖥 Открыто: <code>{html.escape(path)}</code>", reply_markup=back_to_device_kb())
        else:
            await message.answer(f"⚠️ {html.escape(str((result or {}).get('error') or 'Нет ответа'))}", reply_markup=back_to_device_kb())


@router.message(AdminFilter(), Form.wait_find)
async def on_find_input(message: Message, state: FSMContext):
    await state.clear()
    target = SESSION.get("target")
    pattern = (message.text or "").strip()
    if not target or target == "all" or not pattern:
        await message.answer("⚠️ Пустой запрос или нет цели.", reply_markup=back_to_device_kb())
        return
    fun_text_collector.reset()
    if not publish("find_file", pattern=pattern, root="~"):
        await message.answer("⚠️ Нет соединения с MQTT-брокером.", reply_markup=back_to_device_kb())
        return
    result = await fun_text_collector.wait(20.0)
    text = (result or {}).get("text") or "⏳ Нет ответа (поиск мог занять больше времени)"
    await message.answer(f"🔍 <b>Найдено:</b>\n<pre>{html.escape(str(text)[:3500])}</pre>", reply_markup=back_to_device_kb())


# =====================================================================
#  Утилиты и удаленный ввод (Fun / Tools)
# =====================================================================

@router.callback_query(AdminFilter(), F.data.in_({"fun:extip", "cmd:extip"}))
async def on_fun_extip(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    await cq.answer("🌍 Запрашиваю внешний IP...")
    fun_text_collector.reset()
    if not publish("ext_ip"):
        await _replace_callback_message(cq, "⚠️ Нет соединения с MQTT-брокером.", reply_markup=back_to_device_kb())
        return
    result = await fun_text_collector.wait(10.0)
    text = (result or {}).get("text") or "⏳ Нет ответа от устройства"
    await _replace_callback_message(cq, f"🌍 <b>Внешний IP ({target_label(target)}):</b>\n<code>{html.escape(str(text))}</code>", reply_markup=back_to_device_kb())


@router.callback_query(AdminFilter(), F.data.in_({"fun:screenoff", "cmd:screenoff"}))
async def on_fun_screenoff(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    if publish("screen_off"):
        await cq.answer("🧯 Экран выключен")
        await _replace_callback_message(cq, f"🧯 Экран успешно погашен: <b>{target_label(target)}</b>", reply_markup=back_to_device_kb())
    else:
        await cq.answer("⚠️ Ошибка отправки")
        await _replace_callback_message(cq, "⚠️ Ошибка отправки", reply_markup=back_to_device_kb())


@router.callback_query(AdminFilter(), F.data.in_({"fun:screensaver", "cmd:saveron"}))
async def on_fun_screensaver(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    if publish("screensaver_on"):
        await cq.answer("💤 Заставка запущена")
        await _replace_callback_message(cq, f"💤 Заставка экрана включена: <b>{target_label(target)}</b>", reply_markup=back_to_device_kb())
    else:
        await cq.answer("⚠️ Ошибка отправки")
        await _replace_callback_message(cq, "⚠️ Ошибка отправки", reply_markup=back_to_device_kb())


@router.callback_query(AdminFilter(), F.data.in_({"fun:type", "cmd:typetxt"}))
async def on_fun_type(cq: CallbackQuery, state: FSMContext):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    await state.set_state(Form.wait_fun_text)
    await cq.message.answer("⌨️ Введите текст, который агент должен напечатать на клавиатуре ПК:", reply_markup=back_to_device_kb())
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.in_({"fun:hotkey", "cmd:hotkey"}))
async def on_fun_hotkey(cq: CallbackQuery, state: FSMContext):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    await state.set_state(Form.wait_fun_hotkey)
    await cq.message.answer("⌘ Введите сочетание клавиш через плюс (например: <code>ctrl+c</code>, <code>cmd+space</code>, <code>alt+tab</code>):", reply_markup=back_to_device_kb())
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.in_({"fun:wallpaper", "cmd:wallpaper"}))
async def on_fun_wallpaper(cq: CallbackQuery, state: FSMContext):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    await state.set_state(Form.wait_fun_wallpaper)
    await cq.message.answer("🖼 Отправьте прямую ссылку на картинку (.jpg или .png) для установки на рабочий стол:", reply_markup=back_to_device_kb())
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.in_({"fun:spam", "cmd:spam"}))
async def on_fun_spam(cq: CallbackQuery, state: FSMContext):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    await state.set_state(Form.wait_fun_spam)
    await cq.message.answer("💬 Отправьте текст для всплывающих окон на экране ПК:", reply_markup=back_to_device_kb())
    await cq.answer()


@router.message(AdminFilter(), Form.wait_fun_text)
async def on_fun_text_input(message: Message, state: FSMContext):
    await state.clear()
    target = SESSION.get("target")
    text = (message.text or "").strip()
    if not target or not text:
        await message.answer("⚠️ Пустой текст или цель не выбрана.", reply_markup=back_to_device_kb())
        return
    if publish("type_text", text=text):
        await message.answer(f"⌨️ Текст отправлен на ввод: <b>{target_label(target)}</b>", reply_markup=back_to_device_kb())
    else:
        await message.answer("⚠️ Ошибка отправки на брокер.", reply_markup=back_to_device_kb())


@router.message(AdminFilter(), Form.wait_fun_hotkey)
async def on_fun_hotkey_input(message: Message, state: FSMContext):
    await state.clear()
    target = SESSION.get("target")
    keys = (message.text or "").strip().lower()
    if not target or not keys:
        await message.answer("⚠️ Пустое сочетание или цель не выбрана.", reply_markup=back_to_device_kb())
        return
    if publish("hotkey", keys=keys):
        await message.answer(f"⌘ Сочетание <code>{html.escape(keys)}</code> нажато на <b>{target_label(target)}</b>", reply_markup=back_to_device_kb())
    else:
        await message.answer("⚠️ Ошибка отправки на брокер.", reply_markup=back_to_device_kb())


@router.message(AdminFilter(), Form.wait_fun_wallpaper)
async def on_fun_wallpaper_input(message: Message, state: FSMContext):
    await state.clear()
    target = SESSION.get("target")
    url = (message.text or "").strip()
    if not target or not url:
        await message.answer("⚠️ Ссылка пуста или цель не выбрана.", reply_markup=back_to_device_kb())
        return
    fun_text_collector.reset()
    if not publish("wallpaper_set", url=url):
        await message.answer("⚠️ Нет соединения с MQTT-брокером.", reply_markup=back_to_device_kb())
        return
    await message.answer("⏳ Устанавливаю обои рабочего стола...")
    result = await fun_text_collector.wait(20.0)
    if result and result.get("ok"):
        await message.answer(f"🖼 Обои успешно обновлены на <b>{target_label(target)}</b>!", reply_markup=back_to_device_kb())
    else:
        err = (result or {}).get("error") or "Устройство не ответило вовремя"
        await message.answer(f"⚠️ Ошибка смены обоев: {html.escape(err)}", reply_markup=back_to_device_kb())


@router.callback_query(AdminFilter(), F.data == "menu:wallpaper")
async def on_menu_wallpaper(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите устройство", show_alert=True)
        return
    await cq.message.edit_text(
        f"🖼 <b>Управление обоями рабочего стола</b> · <b>{target_label(target)}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "• 🎲 <b>Случайный мем</b> — скачает смешной мем высокого качества из интернета\n"
        "• 📸 <b>Фото из чата</b> — просто пришлите любую картинку прямо в этот диалог\n"
        "• 🌐 <b>URL ссылка</b> — укажите прямую ссылку на картинку в сети\n"
        "• 🔄 <b>Восстановить прежние обои</b> — вернёт оригинальные обои ПК",
        reply_markup=wallpaper_menu(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "cmd:wallpaper_random_meme")
async def on_wallpaper_random_meme(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    await cq.answer("🎲 Ищу мем и ставлю на рабочий стол...")
    fun_text_collector.reset()
    if not publish("wallpaper_set", random_meme=True):
        await cq.message.answer("⚠️ Ошибка отправки на брокер.", reply_markup=back_to_device_kb())
        return
    result = await fun_text_collector.wait(15.0)
    if result and result.get("ok"):
        await cq.message.answer(f"🎉 <b>Случайный мем успешно установлен на рабочий стол</b>: {target_label(target)}!", reply_markup=wallpaper_menu())
    else:
        err = (result or {}).get("error")
        if err:
            await cq.message.answer(f"⚠️ Ошибка установки: {html.escape(err)}", reply_markup=wallpaper_menu())
        else:
            await cq.message.answer(f"🖼 Обои отправлены на установку для <b>{target_label(target)}</b>!", reply_markup=wallpaper_menu())


@router.callback_query(AdminFilter(), F.data == "cmd:wallpaper_photo_guide")
async def on_wallpaper_photo_guide(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    await cq.message.answer(
        f"📸 <b>Установка обоев прямо из фото</b>\n"
        f"Целевое устройство: <b>{target_label(target)}</b>\n\n"
        f"Просто <b>прикрепите и отправьте любую фотографию</b> прямо сюда в этот чат Telegram!\n"
        f"Бот мгновенно перешлёт её на компьютер и сделает новыми обоями рабочего стола.",
        reply_markup=wallpaper_menu(),
    )
    await cq.answer()


@router.message(AdminFilter(), F.photo)
async def on_photo_wallpaper_message(message: Message):
    target = SESSION.get("target")
    if not target:
        await message.answer("⚠️ Сначала выберите целевое устройство в меню /devices, чтобы установить обои!", reply_markup=devices_menu())
        return

    status_msg = await message.answer(f"⏳ Скачиваю фото и передаю на <b>{target_label(target)}</b>...")
    try:
        photo = message.photo[-1]
        file_info = await message.bot.get_file(photo.file_id)
        photo_bytes_io = io.BytesIO()
        await message.bot.download_file(file_info.file_path, photo_bytes_io)
        raw_bytes = photo_bytes_io.getvalue()
        b64_img = base64.b64encode(raw_bytes).decode('ascii')

        fun_text_collector.reset()
        if not publish("wallpaper_set", b64=b64_img):
            await status_msg.edit_text("⚠️ Ошибка отправки фото через брокер.", reply_markup=back_to_device_kb())
            return

        result = await fun_text_collector.wait(20.0)
        if result and result.get("ok"):
            await status_msg.edit_text(
                f"✅ <b>Фото успешно установлено как обои рабочего стола!</b>\n"
                f"ПК: <b>{target_label(target)}</b> ({len(raw_bytes) // 1024} КБ)",
                reply_markup=wallpaper_menu(),
            )
        else:
            await status_msg.edit_text(
                f"🖼 Фото передано на <b>{target_label(target)}</b> ({len(raw_bytes) // 1024} КБ)!",
                reply_markup=wallpaper_menu(),
            )
    except Exception as e:
        log.error("Failed to process photo wallpaper: %s", e)
        await status_msg.edit_text(f"⚠️ Ошибка обработки фото: {e}", reply_markup=back_to_device_kb())


@router.message(AdminFilter(), Form.wait_fun_spam)
async def on_fun_spam_input(message: Message, state: FSMContext):
    await state.clear()
    target = SESSION.get("target")
    text = (message.text or "").strip()
    if not target or not text:
        await message.answer("⚠️ Текст пуст.", reply_markup=back_to_device_kb())
        return
    if publish("msgbox_spam", text=text, count=5):
        await message.answer(f"💬 Отправлено 5 диалоговых окон на экран <b>{target_label(target)}</b>!", reply_markup=back_to_device_kb())
    else:
        await message.answer("⚠️ Ошибка отправки на брокер.", reply_markup=back_to_device_kb())


# =====================================================================
#  Хендлеры: 🎭 Приколы и розыгрыши
# =====================================================================

@router.callback_query(AdminFilter(), F.data == "prank:screamer")
async def on_prank_screamer(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    if publish("prank_screamer"):
        await cq.answer("🎬 Скример запущен!")
        await cq.message.answer(f"🎬 <b>Полноэкранный скример активирован:</b> {target_label(target)}", reply_markup=back_to_device_kb())
    else:
        await cq.answer("⚠️ Ошибка отправки", show_alert=True)


@router.callback_query(AdminFilter(), F.data == "prank:rickroll50")
async def on_prank_rickroll(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    if publish("prank_rickroll", tabs=50):
        await cq.answer("🎵 Рикролл x50 запущен!")
        await cq.message.answer(f"🎵 <b>50 вкладок Rickroll отправлены на открытие:</b> {target_label(target)}", reply_markup=back_to_device_kb())
    else:
        await cq.answer("⚠️ Ошибка отправки", show_alert=True)


@router.callback_query(AdminFilter(), F.data == "prank:matrix")
async def on_prank_matrix(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    if publish("prank_matrix", duration=15):
        await cq.answer("🌈 Матрица запущена!")
        await cq.message.answer(f"🌈 <b>Зеленый дождь «Матрица» запущен на весь экран:</b> {target_label(target)}", reply_markup=back_to_device_kb())
    else:
        await cq.answer("⚠️ Ошибка отправки", show_alert=True)


@router.callback_query(AdminFilter(), F.data == "prank:siren")
async def on_prank_siren(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    if publish("prank_siren", duration=10):
        await cq.answer("🔊 Сирена включена!")
        await cq.message.answer(f"🔊 <b>Звуковая сирена тревоги запущена на 10 сек:</b> {target_label(target)}", reply_markup=back_to_device_kb())
    else:
        await cq.answer("⚠️ Ошибка отправки", show_alert=True)


@router.callback_query(AdminFilter(), F.data == "prank:shout")
async def on_prank_shout(cq: CallbackQuery, state: FSMContext):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    await state.set_state(Form.wait_shout)
    await cq.message.answer("📢 Введите текст, который агент должен прокричать через динамики на 100% громкости:", reply_markup=back_to_device_kb())
    await cq.answer()


@router.message(AdminFilter(), Form.wait_shout)
async def on_prank_shout_input(message: Message, state: FSMContext):
    await state.clear()
    target = SESSION.get("target")
    text = (message.text or "").strip()
    if not target or not text:
        await message.answer("⚠️ Текст пуст.", reply_markup=back_to_device_kb())
        return
    if publish("prank_shout_tts", text=text):
        await message.answer(f"📢 Текст отправлен на громкую озвучку на <b>{target_label(target)}</b>!", reply_markup=back_to_device_kb())
    else:
        await message.answer("⚠️ Ошибка отправки на брокер.", reply_markup=back_to_device_kb())


@router.callback_query(AdminFilter(), F.data == "prank:swapmouse")
async def on_prank_swapmouse(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    current_state = bool(SESSION.get(f"mouse_swapped_{target}", False))
    new_state = not current_state
    if publish("prank_swap_mouse", swap=new_state):
        SESSION[f"mouse_swapped_{target}"] = new_state
        try:
            await cq.message.edit_reply_markup(reply_markup=pranks_menu(page=3, target=target))
        except Exception:
            pass
        if new_state:
            await cq.answer("🖱 Инверсия мыши ВКЛЮЧЕНА! (Кнопки поменялись местами)", show_alert=True)
        else:
            await cq.answer("🖱 Инверсия мыши ВЫКЛЮЧЕНА! (Стандартный режим)", show_alert=True)
    else:
        await cq.answer("⚠️ Ошибка отправки на брокер", show_alert=True)


@router.callback_query(AdminFilter(), F.data == "prank:crazycursor")
async def on_prank_crazycursor(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    if publish("prank_crazy_cursor", duration=10):
        await cq.answer("🌀 Курсор крутится!")
        await cq.message.answer(f"🌀 <b>Пьяный курсор активирован на 10 сек:</b> {target_label(target)}", reply_markup=back_to_device_kb())
    else:
        await cq.answer("⚠️ Ошибка отправки", show_alert=True)


@router.callback_query(AdminFilter(), F.data == "prank:hidedesktop")
async def on_prank_hidedesktop(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    current_state = bool(SESSION.get(f"desktop_hidden_{target}", False))
    new_state = not current_state
    if publish("prank_hide_desktop", hide=new_state):
        SESSION[f"desktop_hidden_{target}"] = new_state
        try:
            await cq.message.edit_reply_markup(reply_markup=pranks_menu(page=1, target=target))
        except Exception:
            pass
        if new_state:
            await cq.answer("🫥 Иконки скрыты! (Нажмите ещё раз, чтобы вернуть)", show_alert=True)
        else:
            await cq.answer("🖥 Иконки рабочего стола восстановлены!", show_alert=True)
    else:
        await cq.answer("⚠️ Ошибка отправки на брокер", show_alert=True)


@router.callback_query(AdminFilter(), F.data == "prank:stop_all")
async def on_prank_stop_all(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    if publish("prank_stop_all"):
        SESSION[f"mouse_swapped_{target}"] = False
        SESSION[f"desktop_hidden_{target}"] = False
        SESSION[f"nightlight_{target}"] = False
        page = 1
        msg_text = cq.message.text or ""
        for p in range(1, 6):
            if f"({p})" in msg_text or f"Стр. {p}" in msg_text:
                page = p
                break
        try:
            await cq.message.edit_reply_markup(reply_markup=pranks_menu(page=page, target=target))
        except Exception:
            pass
        await cq.answer("🛑 ВСЕ ПРИКОЛЫ ОСТАНОВЛЕНЫ! Мышь, экран и рабочий стол в норме.", show_alert=True)
    else:
        await cq.answer("⚠️ Ошибка отправки команды остановки", show_alert=True)


@router.callback_query(AdminFilter(), F.data == "prank:dancewin")
async def on_prank_dancewin(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    if publish("prank_dancing_windows", duration=10):
        await cq.answer("🪟 Окна танцуют!")
        await cq.message.answer(f"🪟 <b>Анимация тряски окон запущена на:</b> {target_label(target)}", reply_markup=back_to_device_kb())
    else:
        await cq.answer("⚠️ Ошибка отправки", show_alert=True)


@router.callback_query(AdminFilter(), F.data == "prank:blackscreen")
async def on_prank_blackscreen(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    if publish("prank_black_screen", duration=15):
        await cq.answer("⬛ Чёрный экран!")
        await cq.message.answer(f"⬛ <b>Чёрный экран включен на 15 сек на:</b> {target_label(target)}", reply_markup=back_to_device_kb())
    else:
        await cq.answer("⚠️ Ошибка отправки", show_alert=True)


@router.callback_query(AdminFilter(), F.data == "prank:randomsite")
async def on_prank_randomsite(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    if publish("prank_random_site"):
        await cq.answer("🎲 Сайт открывается!")
        await cq.message.answer(f"🎲 <b>Случайный забавный сайт открыт в браузере:</b> {target_label(target)}", reply_markup=back_to_device_kb())
    else:
        await cq.answer("⚠️ Ошибка отправки", show_alert=True)


# =====================================================================
#  Хендлеры: 🖥 Экран, 📊 Сенсоры, 🌐 Сеть, 🛠 Терминал
# =====================================================================

@router.callback_query(AdminFilter(), F.data == "cmd:brightness")
async def on_cmd_brightness(cq: CallbackQuery, state: FSMContext):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    await state.set_state(Form.wait_brightness)
    await cq.message.answer("☀️ Введите уровень яркости экрана в процентах (0–100):", reply_markup=back_to_device_kb())
    await cq.answer()


@router.message(AdminFilter(), Form.wait_brightness)
async def on_brightness_input(message: Message, state: FSMContext):
    await state.clear()
    target = SESSION.get("target")
    try:
        lvl = int((message.text or "").strip())
        lvl = max(0, min(100, lvl))
    except ValueError:
        await message.answer("⚠️ Введите число от 0 до 100.", reply_markup=back_to_device_kb())
        return

    status_msg = await message.answer(
        f"⏳ <b>☀️ Яркость экрана</b> · <b>{html.escape(target_label(target))}</b>\n<code>[■□□□□] 25% Настройка яркости {lvl}%...</code>",
        reply_markup=back_to_device_kb(),
    )
    sent, command_id = publish_tracked("display_brightness", level=lvl)
    if not sent:
        await status_msg.edit_text("⚠️ MQTT-брокер недоступен: команда не отправлена.", reply_markup=back_to_device_kb())
        return
    result = await fun_text_collector.wait_for(target, "display_brightness", timeout=8.0, command_id=command_id)
    txt = (result or {}).get("text") or f"☀️ Яркость экрана установлена на {lvl}%"
    try:
        await status_msg.edit_text(
            f"☀️ <b>Яркость ({html.escape(target_label(target))}):</b>\n<pre>{html.escape(str(txt)[:3800])}</pre>",
            reply_markup=back_to_device_kb(),
        )
    except Exception:
        await message.answer(
            f"☀️ <b>Яркость ({html.escape(target_label(target))}):</b>\n<pre>{html.escape(str(txt)[:3800])}</pre>",
            reply_markup=back_to_device_kb(),
        )


@router.callback_query(AdminFilter(), F.data == "cmd:nightlight")
async def on_cmd_nightlight(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    current_state = bool(SESSION.get(f"nightlight_{target}", False))
    new_state = not current_state
    sent, command_id = publish_tracked("display_night_light", enabled=new_state)
    if not sent:
        await cq.answer("⚠️ Ошибка отправки на брокер", show_alert=True)
        return
    await cq.answer("🌙 Переключаю ночной свет...")
    result = await fun_text_collector.wait_for(target, "display_night_light", timeout=8.0, command_id=command_id)
    ok = bool(result and result.get("ok", False))
    if ok:
        SESSION[f"nightlight_{target}"] = new_state
        try:
            await cq.message.edit_reply_markup(reply_markup=screen_menu(target=target))
        except Exception:
            pass
    text = (result or {}).get("text") or ("Ночной свет переключён." if ok else "⚠️ Агент не подтвердил переключение ночного света.")
    await cq.message.answer(html.escape(str(text)), reply_markup=back_to_device_kb())


@router.callback_query(AdminFilter(), F.data == "cmd:rotate")
async def on_cmd_rotate(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    await cq.answer()
    await cq.message.answer(
        f"🔄 <b>Выберите угол поворота экрана для</b> {target_label(target)}:",
        reply_markup=rotate_menu(),
    )


@router.callback_query(AdminFilter(), F.data.startswith("rotate:"))
async def on_rotate_select(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    try:
        angle = int(cq.data.split(":", 1)[1])
    except Exception:
        angle = 0
    sent, command_id = publish_tracked("display_rotate", angle=angle)
    if not sent:
        await cq.answer("⚠️ Ошибка отправки", show_alert=True)
        return
    await cq.answer("🔄 Поворачиваю экран...")
    result = await fun_text_collector.wait_for(target, "display_rotate", timeout=8.0, command_id=command_id)
    text = (result or {}).get("text") or (f"Экран повернут на {angle}°" if result and result.get("ok") else "⚠️ Агент не подтвердил поворот экрана.")
    await _replace_callback_message(
        cq,
        f"🔄 <b>{html.escape(target_label(target))}:</b>\n{html.escape(str(text))}",
        reply_markup=back_to_device_kb(),
    )


async def _simple_command_unlocked(cq: CallbackQuery, action: str, emoji: str, label: str, timeout: float = 12.0, **publish_kwargs):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    await cq.answer(f"{emoji} {label}...")
    sent, command_id = publish_tracked(action, **publish_kwargs)
    if not sent:
        await _replace_callback_message(cq, "⚠️ MQTT-брокер недоступен: команда не отправлена.", reply_markup=back_to_device_kb())
        return

    # Одна карточка на всю операцию: меню -> прогресс -> результат.
    status_msg = cq.message
    try:
        await status_msg.edit_text(
        f"⏳ <b>{emoji} {label}</b> · <b>{html.escape(target_label(target))}</b>\n<code>[■□□□□] 20% Связь с агентом...</code>",
        reply_markup=back_to_device_kb(),
        )
    except Exception:
        # Если Telegram не разрешил редактирование (например, старое медиа),
        # удаляем старую карточку и создаём ровно одну новую.
        try:
            await cq.message.delete()
        except Exception:
            pass
        status_msg = await cq.message.answer(
            f"⏳ <b>{emoji} {label}</b> · <b>{html.escape(target_label(target))}</b>\n<code>[■□□□□] 20% Связь с агентом...</code>",
            reply_markup=back_to_device_kb(),
        )

    anim_task = None
    stop_anim = asyncio.Event()

    async def _animate_loader():
        frames = [
            "<code>[■□□□□] 20% Связь с агентом...</code>",
            "<code>[■■□□□] 40% Выполнение на ПК...</code>",
            "<code>[■■■□□] 60% Обработка результата...</code>",
            "<code>[■■■■□] 80% Сбор данных...</code>",
            "<code>[■■■■■] 95% Ожидание ответа...</code>",
        ]
        idx = 0
        while not stop_anim.is_set():
            # Telegram ограничивает частые правки одного сообщения; 1.4 с
            # убирает фризы и ошибки Flood control.
            await asyncio.sleep(1.4)
            if stop_anim.is_set():
                break
            idx = (idx + 1) % len(frames)
            try:
                await status_msg.edit_text(
                    f"⏳ <b>{emoji} {label}</b> · <b>{html.escape(target_label(target))}</b>\n{frames[idx]}",
                    reply_markup=back_to_device_kb(),
                )
            except Exception:
                pass

    anim_task = asyncio.create_task(_animate_loader())

    try:
        result = await fun_text_collector.wait_for(target, action, timeout=timeout, command_id=command_id)
    finally:
        stop_anim.set()
        if anim_task:
            anim_task.cancel()
            try:
                await anim_task
            except (asyncio.CancelledError, Exception):
                pass

    text = (result or {}).get("text") or ("Агент не ответил за отведённое время." if result is None else "Ответ без текста")
    if result is None:
        text = "⚠️ Агент не ответил за отведённое время. Проверьте его статус и MQTT-соединение."
    final_text = f"{emoji} <b>{label} ({html.escape(target_label(target))}):</b>\n<pre>{html.escape(str(text)[:3800])}</pre>"
    try:
        await status_msg.edit_text(final_text, reply_markup=back_to_device_kb())
    except Exception as exc:
        # Не создаём копию сообщения при безобидной ошибке
        # «message is not modified» (так бывает при быстром повторном клике).
        if "not modified" not in str(exc).lower():
            try:
                await status_msg.delete()
            except Exception:
                pass
            await cq.message.answer(final_text, reply_markup=back_to_device_kb())
    return result


async def simple_command(cq: CallbackQuery, action: str, emoji: str, label: str, timeout: float = 12.0, **publish_kwargs):
    """Запустить одну команду на одной карточке без параллельных дублей."""
    message = cq.message
    key = (
        int(cq.from_user.id),
        int(message.chat.id),
        int(message.message_id),
        str(action),
    )
    if key in _ACTIVE_UI_COMMANDS:
        await cq.answer("Эта команда уже выполняется", show_alert=True)
        return None
    _ACTIVE_UI_COMMANDS.add(key)
    try:
        return await _simple_command_unlocked(
            cq, action, emoji, label, timeout=timeout, **publish_kwargs
        )
    finally:
        _ACTIVE_UI_COMMANDS.discard(key)


@router.callback_query(AdminFilter(), F.data == "cmd:check_update")
async def on_cmd_check_update(cq: CallbackQuery):
    result = await simple_command(cq, "agent_update", "🔄", "Обновить агента", timeout=45.0, update=True)
    # Старые агенты до v3.3.6 умели только сообщить статус и игнорировали
    # update=true. Для них выполняем одноразовый переход через уже имеющийся
    # безопасный shell-канал; после этого дальнейшие обновления идут кнопкой.
    text = str((result or {}).get("text") or "")
    # Если старый агент вообще не ответил, result будет None (обычный случай
    # для старой версии). Не ждём, что он подтвердит agent_update: shell уже
    # есть в старом агенте и может сам подтянуть новую версию.
    needs_legacy = not result
    if result and result.get("ok"):
        needs_legacy = "v1.3" in text or "Актуален" in text
    if needs_legacy:
        legacy = (
            'cd "$HOME/XIDER/git-ver/XGENT-MCS" && t=$(mktemp -d) && '
            'curl -fsSL https://github.com/invinby/XIDER/archive/refs/heads/main.zip -o "$t/x.zip" && '
            'unzip -q "$t/x.zip" -d "$t" && '
            'for f in xgent_mcs.py config.py crypto.py xgencrypto.py xider_guardian.py requirements.txt setup_mac.py start_agent.sh stop_agent.sh start_guardian.sh; do '
            'cp "$t/XIDER-main/XGENT-MCS/$f" "$f"; done && '
            'chmod +x start_agent.sh stop_agent.sh start_guardian.sh && '
            'launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.xgent.agent.plist" 2>/dev/null || true && '
            'rm -f "$HOME/Library/LaunchAgents/com.xgent.agent.plist" && '
            '(kill "$(cat agent.pid)" 2>/dev/null || true) && sleep 1 && bash ./start_agent.sh && bash ./start_guardian.sh'
        )
        sent, _ = publish_tracked("shell", command=legacy, timeout=60)
        if sent:
            await _replace_callback_message(
                cq,
                "🛠 Старый агент найден. Запустил одноразовое обновление через его защищённый канал; дальше обновления будут из этой кнопки.",
                reply_markup=back_to_device_kb(),
            )


@router.callback_query(AdminFilter(), F.data == "cmd:smart")
async def on_cmd_smart(cq: CallbackQuery):
    await simple_command(cq, "storage_smart", "🩺", "Диагностика SMART", timeout=12.0)


@router.callback_query(AdminFilter(), F.data == "cmd:apps")
async def on_cmd_apps(cq: CallbackQuery):
    await simple_command(cq, "sys_installed_apps", "📦", "Установленное ПО", timeout=15.0)


@router.callback_query(AdminFilter(), F.data == "cmd:wifi")
async def on_cmd_wifi(cq: CallbackQuery):
    await simple_command(cq, "net_wifi_passwords", "📶", "Сети Wi-Fi", timeout=12.0)


@router.callback_query(AdminFilter(), F.data == "cmd:usb")
async def on_cmd_usb(cq: CallbackQuery):
    await simple_command(cq, "usb_devices", "🔌", "USB-устройства", timeout=10.0)


@router.callback_query(AdminFilter(), F.data == "cmd:bluetooth")
async def on_cmd_bluetooth(cq: CallbackQuery):
    await simple_command(cq, "net_bluetooth_list", "🔵", "Bluetooth устройства", timeout=10.0)


@router.callback_query(AdminFilter(), F.data == "cmd:netstat")
async def on_cmd_netstat(cq: CallbackQuery):
    await simple_command(cq, "netstat", "🔗", "Активные порты", timeout=10.0)


@router.callback_query(AdminFilter(), F.data == "cmd:startup")
async def on_cmd_startup(cq: CallbackQuery):
    await simple_command(cq, "startup_list", "🚀", "Автозагрузка", timeout=10.0)


@router.callback_query(AdminFilter(), F.data == "cmd:cmdhistory")
async def on_cmd_cmdhistory(cq: CallbackQuery):
    await simple_command(cq, "sys_history_cmd", "🕘", "История консоли", timeout=10.0, lines=30)


@router.callback_query(AdminFilter(), F.data == "cmd:prockillname")
async def on_cmd_prockillname(cq: CallbackQuery, state: FSMContext):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    await state.set_state(Form.wait_prockill)
    await cq.message.answer("🔪 Введите имя процесса для завершения (например: <code>chrome.exe</code> или <code>Discord</code>):", reply_markup=back_to_device_kb())
    await cq.answer()


@router.message(AdminFilter(), Form.wait_prockill)
async def on_prockill_input(message: Message, state: FSMContext):
    await state.clear()
    target = SESSION.get("target")
    name = (message.text or "").strip()
    if not target or not name:
        await message.answer("⚠️ Имя процесса пусто.", reply_markup=back_to_device_kb())
        return
    if publish("proc_kill_name", name=name):
        await message.answer(f"🔪 Команда завершения процесса <code>{html.escape(name)}</code> отправлена на <b>{target_label(target)}</b>!", reply_markup=back_to_device_kb())
    else:
        await message.answer("⚠️ Ошибка отправки на брокер.", reply_markup=back_to_device_kb())


# =====================================================================
#  Новые хендлеры: 🎭 Пагинация приколов и запуск розыгрышей
# =====================================================================

@router.callback_query(AdminFilter(), F.data.startswith("prankpage:"))
async def on_prank_page(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите устройство", show_alert=True)
        return
    page = int(cq.data.split(":", 1)[1])
    page_names = {
        1: "Экраны & Визуал",
        2: "Звуки & Голос",
        3: "Мышь & Клавиатура",
        4: "Фейки & Системный хаос",
        5: "Мемы & Ультра-Троллинг",
    }
    p_name = page_names.get(page, f"Стр. {page}")
    await cq.message.edit_text(
        f"🎭 <b>Приколы & Розыгрыши ({p_name})</b> · <b>{target_label(target)}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Выберите прикол из списка ниже:",
        reply_markup=pranks_menu(page),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.startswith("cmd:prank_"))
async def on_cmd_prank_generic(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    prank_cmd = cq.data[4:]  # e.g. "prank_bsod"
    await cq.answer("🎭 Запускаю...")
    fun_text_collector.reset()
    if not publish(prank_cmd):
        await cq.message.answer("⚠️ Ошибка отправки на брокер.", reply_markup=back_to_device_kb())
        return
    result = await fun_text_collector.wait(10.0)
    text = (result or {}).get("text") or "🎭 Команда розыгрыша отправлена агенту!"
    await cq.message.answer(f"🎭 <b>{target_label(target)}:</b>\n{html.escape(str(text))}", reply_markup=back_to_device_kb())


# =====================================================================
#  Новые хендлеры: 💤 Режим сна агента (Standby/Watchdog) & ☀️ Пробуждение
# =====================================================================

@router.callback_query(AdminFilter(), F.data == "cmd:standby_sleep")
async def on_cmd_standby_sleep(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    await cq.answer("💤 Перевожу агента в режим сна...")
    fun_text_collector.reset()
    publish("standby_sleep")
    res = await fun_text_collector.wait(8.0)
    text = (res or {}).get("text") or "💤 Агент переведен в режим сна (Watchdog)."
    if target != "all" and target in devices:
        devices[target]["standby"] = True
    await cq.message.answer(f"💤 <b>{target_label(target)}:</b>\n{html.escape(str(text))}", reply_markup=back_to_device_kb())


@router.callback_query(AdminFilter(), F.data == "cmd:wake")
async def on_cmd_wake(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите цель", show_alert=True)
        return
    await cq.answer("☀️ Пробуждаю агента...")
    fun_text_collector.reset()
    publish("wake")
    res = await fun_text_collector.wait(8.0)
    text = (res or {}).get("text") or "☀️ Агент успешно пробужден!"
    if target != "all" and target in devices:
        devices[target]["standby"] = False
    await cq.message.answer(f"☀️ <b>{target_label(target)}:</b>\n{html.escape(str(text))}", reply_markup=back_to_device_kb())


# =====================================================================
#  Новые хендлеры: 🚀 Автозапуск при включении ПК (Registry / LaunchAgents)
# =====================================================================

@router.callback_query(AdminFilter(), F.data == "cmd:autorun_status")
async def on_cmd_autorun_status(cq: CallbackQuery):
    await simple_command(cq, "autorun_status", "🚀", "Автозагрузка", timeout=10.0)


@router.callback_query(AdminFilter(), F.data == "cmd:autorun_enable")
async def on_cmd_autorun_enable(cq: CallbackQuery):
    await simple_command(cq, "autorun_enable", "✅", "Включение автозапуска", timeout=10.0)


@router.callback_query(AdminFilter(), F.data == "cmd:autorun_disable")
async def on_cmd_autorun_disable(cq: CallbackQuery):
    await simple_command(cq, "autorun_disable", "🛑", "Отключение автозапуска", timeout=10.0)


@router.callback_query(AdminFilter(), F.data == "cmd:guardian_menu")
async def on_cmd_guardian_menu(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer("Сначала выберите устройство", show_alert=True)
        return
    await cq.message.edit_text(
        f"🛡 <b>Guardian</b> · {html.escape(target_label(target))}\n\n"
        "Видимый supervisor: связь, запуск и восстановление агента.",
        reply_markup=guardian_menu(),
    )
    await cq.answer()


async def _guardian_command(cq: CallbackQuery, command: str, *, enabled: bool | None = None):
    kwargs = {"command": command}
    if enabled is not None:
        kwargs["enabled"] = enabled
    await simple_command(cq, "guardian", "🛡", f"Guardian: {command}", timeout=12.0, **kwargs)


@router.callback_query(AdminFilter(), F.data == "cmd:guardian_status")
async def on_cmd_guardian_status(cq: CallbackQuery):
    await _guardian_command(cq, "status")


@router.callback_query(AdminFilter(), F.data == "cmd:guardian_start")
async def on_cmd_guardian_start(cq: CallbackQuery):
    await _guardian_command(cq, "start")


@router.callback_query(AdminFilter(), F.data == "cmd:guardian_stop")
async def on_cmd_guardian_stop(cq: CallbackQuery):
    await _guardian_command(cq, "stop")


@router.callback_query(AdminFilter(), F.data == "cmd:guardian_restart")
async def on_cmd_guardian_restart(cq: CallbackQuery):
    await _guardian_command(cq, "restart")


@router.callback_query(AdminFilter(), F.data == "cmd:guardian_auto_on")
async def on_cmd_guardian_auto_on(cq: CallbackQuery):
    await _guardian_command(cq, "auto_restart", enabled=True)


@router.callback_query(AdminFilter(), F.data == "cmd:guardian_auto_off")
async def on_cmd_guardian_auto_off(cq: CallbackQuery):
    await _guardian_command(cq, "auto_restart", enabled=False)


# =====================================================================
#  Новые хендлеры: ⏱ Аптайм, 🧹 TEMP кэш, 🏓 Пинг сети
# =====================================================================

@router.callback_query(AdminFilter(), F.data == "cmd:sys_uptime")
async def on_cmd_sys_uptime(cq: CallbackQuery):
    await simple_command(cq, "sys_uptime", "⏱", "Время работы", timeout=10.0)


@router.callback_query(AdminFilter(), F.data == "cmd:sys_clean_temp")
async def on_cmd_sys_clean_temp(cq: CallbackQuery):
    await simple_command(cq, "sys_clean_temp", "🧹", "Очистка TEMP", timeout=15.0)


@router.callback_query(AnyAccessFilter(), F.data == "cmd:net_ping")
async def on_cmd_net_ping(cq: CallbackQuery):
    await simple_command(cq, "net_ping", "🏓", "Пинг с узла", timeout=12.0, host="8.8.8.8", count=4)

@router.callback_query(AnyAccessFilter(), F.data == "cmd:geo_location")
async def on_cmd_geo_location(cq: CallbackQuery):
    await simple_command(cq, "geo_location", "📍", "Геолокация", timeout=15.0)


# =====================================================================
#  Запуск
# =====================================================================

async def main() -> None:
    global LOOP
    LOOP = asyncio.get_running_loop()
    print("\n" + "=" * 70)
    print("           ⚡ XIDER TELEGRAM CONTROL SERVER [ONLINE] ⚡")
    print("=" * 70)
    try:
        me = await bot.get_me()
        print(f"  [+] Telegram Bot:     @{me.username} (ID: {me.id})")
    except Exception as e:
        print(f"  [!] Telegram Bot:     Warning fetching @username: {e}")
    print(f"  [+] Authorized Admin: {ADMIN_ID}")
    print(f"  [+] MQTT Broker:      {MQTT_BROKER}:{MQTT_PORT}")
    print(f"  [+] MQTT Prefix:      {MQTT_PREFIX}")
    print(f"  [+] Encryption:       {'AES-256-GCM (Active)' if ENCRYPT_PAYLOAD else 'Plaintext'}")
    print("=" * 70)
    print("  [*] Live server console active. Real-time events stream below:\n")
    log.info("Запуск бота XGENT и MQTT-транспорта...")
    transport.start()
    offline_watchdog = asyncio.create_task(_device_offline_watchdog())
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        offline_watchdog.cancel()
        try:
            await offline_watchdog
        except asyncio.CancelledError:
            pass
        transport.stop()


def create_image():
    from PIL import Image, ImageDraw
    image = Image.new('RGB', (64, 64), color=(0, 0, 0))
    dc = ImageDraw.Draw(image)
    dc.rectangle((16, 16, 48, 48), fill=(0, 150, 255))
    return image


def run_tray():
    import pystray
    from pystray import Menu, MenuItem

    def aiogram_thread():
        asyncio.run(main())
        os._exit(0)

    t = threading.Thread(target=aiogram_thread, daemon=True)
    t.start()

    def on_stop(icon, item):
        icon.stop()
        os._exit(0)

    icon = pystray.Icon(
        "xider-bot",
        create_image(),
        "XIDER Bot Server",
        menu=Menu(MenuItem("Остановить Сервер", on_stop))
    )
    icon.run()


if __name__ == "__main__":
    if "--tray" in sys.argv:
        try:
            run_tray()
        except ImportError:
            print("[!] Ошибка: pystray или Pillow не установлены. Установите их или запускайте без --tray.")
            asyncio.run(main())
    else:
        asyncio.run(main())
