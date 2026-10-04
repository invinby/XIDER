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
import xlex
import release_catalog
import info_book
import ui_cards
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
    wait_wallpaper_photo = State()
    wait_fun_spam = State()
    wait_shout = State()
    wait_prockill = State()
    wait_brightness = State()
    wait_admin_message = State()
    wait_bot_text = State()
    wait_server_command = State()


async def _start_authorized_input(
    state: FSMContext,
    form_state: State,
    callback: str,
    device_id: str,
    **form_data,
) -> None:
    """Bind a pending device command to its initiating grant and device."""
    await state.set_state(form_state)
    await state.update_data(
        **form_data,
        authorization_callback=str(callback or ""),
        authorization_target=str(device_id or ""),
    )


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
    detail = access_store.redact_text(detail)
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
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    if target == "all":
        await cq.answer(_lex("single_device_only"), show_alert=True)
        return
    await cq.answer("📋 Запрашиваю буфер обмена...")
    if not await _callback_result_access_or_report(cq, target):
        return
    sent, command_id = publish_tracked("clipboard", _target=target)
    if not sent:
        await _replace_callback_message(cq,
            _lex("mqtt_disconnected"),
            reply_markup=back_to_device_kb(),
        )
        return
    result = await clipboard_collector.wait_for(target, "clipboard", 10.0, command_id)
    if not await _callback_result_access_or_report(cq, target):
        return
    if result is None:
        await _replace_callback_message(cq,
            _lex("device_timeout", seconds="10"),
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
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    if target == "all":
        await cq.answer(_lex("single_device_only"), show_alert=True)
        return
    await cq.answer("⚙️ Запрашиваю процессы (Топ-5)...")
    if not await _callback_result_access_or_report(cq, target):
        return
    sent, command_id = publish_tracked("processes", _target=target)
    if not sent:
        await _replace_callback_message(cq,
            _lex("mqtt_disconnected"),
            reply_markup=back_to_device_kb(),
        )
        return
    result = await processes_collector.wait_for(target, "processes", 15.0, command_id)
    if not await _callback_result_access_or_report(cq, target):
        return
    if result is None:
        await _replace_callback_message(cq,
            _lex("device_timeout", seconds="15"),
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
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    if target == "all":
        await cq.answer(_lex("single_device_only"), show_alert=True)
        return
    await cq.answer("🎙 Идет запись звука (5 сек)...")
    if not await _callback_result_access_or_report(cq, target):
        return
    sent, command_id = publish_tracked("mic", _target=target)
    if not sent:
        await _replace_callback_message(cq,
            _lex("mqtt_disconnected"),
            reply_markup=back_to_device_kb(),
        )
        return
    result = await mic_collector.wait_for(target, "mic", 20.0, command_id)
    if not await _callback_result_access_or_report(cq, target):
        return
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


@router.callback_query(OwnerFilter(), F.data == "cmd:shell")
async def on_cmd_shell(cq: CallbackQuery, state: FSMContext):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    if target == "all":
        await cq.answer(_lex("single_device_only"), show_alert=True)
        return
    await state.set_state(Form.wait_shell)
    await state.update_data(command_target=target)
    await _replace_callback_message(
        cq,
        _lex("shell_prompt"),
        reply_markup=back_to_device_kb(),
    )
    await cq.answer()


@router.message(OwnerFilter(), Form.wait_shell)
async def on_shell_input(message: Message, state: FSMContext):
    form_data = await state.get_data()
    await state.clear()
    cmd = (message.text or "").strip()
    if not cmd:
        await _replace_user_card(
            message, _lex("generic_empty_command"), reply_markup=back_to_device_kb()
        )
        return
    target = form_data.get("command_target")
    if not target:
        await _replace_user_card(
            message, _lex("target_required"), reply_markup=back_to_device_kb()
        )
        return
    if target == "all":
        await _replace_user_card(
            message, _lex("single_device_only"), reply_markup=back_to_device_kb()
        )
        return
    sent, command_id = publish_tracked("shell", _target=target, command=cmd)
    if not sent:
        await _replace_user_card(
            message, _lex("mqtt_disconnected"), reply_markup=back_to_device_kb()
        )
        return

    await _replace_user_card(
        message,
        _lex_html("command_waiting", emoji="💻", label="Shell", device=target_label(target)),
        reply_markup=back_to_device_kb(),
    )
    result = await shell_collector.wait_for(target, "shell", 20.0, command_id)
    if result is None:
        await _replace_user_card(
            message,
            _lex("device_timeout", seconds="20"),
            reply_markup=back_to_device_kb(),
        )
        return

    output = str(result.get("output", ""))
    safe_out = html.escape(output)
    if len(safe_out) > 3800:
        safe_out = safe_out[:3800]
        if safe_out.rfind("&") > safe_out.rfind(";"):
            safe_out = safe_out[:safe_out.rfind("&")]
        safe_out += "\n...[ОБРЕЗАНО]"

    await _replace_user_card(
        message,
        _lex(
            "command_result",
            emoji="💻",
            label="Shell",
            device=html.escape(target_label(target)),
            text=safe_out,
        ),
        reply_markup=back_to_device_kb(),
    )


@router.callback_query(AdminFilter(), F.data == "cmd:open_app")
async def on_cmd_open_app(cq: CallbackQuery, state: FSMContext):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    if target == "all":
        await cq.answer(_lex("single_device_only"), show_alert=True)
        return
    await _start_authorized_input(state, Form.wait_open_app, cq.data, target, command_target=target)
    await _replace_callback_message(
        cq,
        _lex("open_app_prompt"),
        reply_markup=back_to_device_kb(),
    )
    await cq.answer()


@router.message(AdminFilter(), Form.wait_open_app)
async def on_open_app_input(message: Message, state: FSMContext):
    form_data = await state.get_data()
    await state.clear()
    app = (message.text or "").strip()
    if not app:
        await _replace_user_card(
            message, _lex("generic_empty_command"), reply_markup=back_to_device_kb()
        )
        return
    target = form_data.get("command_target")
    if not target:
        await _replace_user_card(
            message, _lex("target_required"), reply_markup=back_to_device_kb()
        )
        return
    if target == "all":
        await _replace_user_card(
            message, _lex("single_device_only"), reply_markup=back_to_device_kb()
        )
        return
    if not _pending_device_input_still_allowed(message, form_data, target):
        await _reject_revoked_device_input(message)
        return
    if publish("open_app", _target=target, app=app):
        await _replace_user_card(
            message,
            _lex_html("app_launch_request_sent", app=app, device=target_label(target)),
            reply_markup=back_to_device_kb(),
        )
    else:
        await _replace_user_card(
            message, _lex("mqtt_disconnected"), reply_markup=back_to_device_kb()
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
    "geo_location": "⚠️ Локация по IP (неточно)",
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

    guardian = info.get("guardian") or {}
    guardian_seen = float(info.get("guardian_last_seen", 0) or 0)
    if guardian:
        guardian_version = html.escape(str(guardian.get("version") or "?"))
        guardian_fresh = bool(is_online and guardian_seen and time.time() - guardian_seen < _OFFLINE_TIMEOUT_SEC)
        guardian_state = "🟢 ответил" if guardian_fresh else "⚪ нет свежего ответа"
        guardian_str = f"{guardian_state}, v{guardian_version}"
    else:
        guardian_str = "не обнаружен"

    os_low = os_str.lower()
    os_icon = "🍏" if ("mac" in os_low or "darwin" in os_low) else ("🪟" if "win" in os_low else "🐧")
    component = release_catalog.component_for(os_str)
    latest = release_catalog.newest_with_package(release_catalog.catalog.cached(), component or "")
    old_version_note = ""
    if release_catalog.is_older(ver, latest):
        old_version_note = "\n⚠️ " + _lex(
            "version_warning", current=ver, latest=html.escape(latest.tag.removeprefix("v"))
        ) + "\n"

    return (
        f"{os_icon} <b>{name}</b> {online_dot} <code>[{status_str}]</code>\n"
        f"<code>ID: {device_id}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>ОС:</b> {os_str}\n"
        f"• <b>Агент:</b> v{ver}  •  <b>Пинг:</b> {seen_str}\n"
        f"• <b>Guardian:</b> {guardian_str}\n"
        f"{old_version_note}"
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
    return xlex.normalize_style(bot_settings.get("ui_style", "technical")) == "xtech"


def _custom_ui() -> bool:
    return xlex.normalize_style(bot_settings.get("ui_style", "technical")) == "xperson"


def _ui_phrase(technical: str, conversational: str, custom: str) -> str:
    style = xlex.normalize_style(bot_settings.get("ui_style", "technical"))
    if style == "xperson":
        return custom
    return technical if style == "xtech" else conversational


def _lex(key: str, **values: str) -> str:
    return xlex.render(key, bot_settings.get("ui_style", "technical"), **values)


def _lex_html(key: str, **values: str) -> str:
    safe_values = {name: html.escape(str(value)) for name, value in values.items()}
    return _lex(key, **safe_values)


def _nav(key: str) -> str:
    return xlex.nav(key, bot_settings.get("ui_style", "technical"))


def main_menu(user_id: int | None = None):
    user_id = int(user_id if user_id is not None else CURRENT_TG_USER.get())
    role = get_user_role(user_id)
    kb = InlineKeyboardBuilder()
    if role == Role.OWNER:
        kb.button(text=_lex("devices_button"), callback_data="menu:devices", style="primary")
        kb.button(text=_nav("all_devices"), callback_data="dev:all", style="primary")
        kb.button(text=_lex("server_button"), callback_data="menu:server", style="primary")
        kb.button(text=_nav("events"), callback_data="ev:menu", style="primary")
    elif role == Role.USER:
        kb.button(text=_lex("my_devices_button"), callback_data="menu:devices", style="primary")
        kb.button(text=_lex("server_overview_button"), callback_data="menu:server", style="primary")
    else:
        kb.button(text=_lex("guest_devices_button"), callback_data="menu:guest_devices", style="primary")
        kb.button(text=_lex("server_overview_button"), callback_data="menu:server", style="primary")
    kb.button(text=_lex("about_button"), callback_data="menu:about", style="primary")
    if role == Role.OWNER:
        kb.button(text=_nav("admin"), callback_data="menu:admin", style="danger")
    # Compact dashboard rows group devices, operations, then information/admin.
    kb.adjust(2)
    return kb.as_markup()


def _visible_device_items(user_id: int | None = None) -> dict[str, dict]:
    user_id = int(user_id if user_id is not None else CURRENT_TG_USER.get())
    all_devices = devices.all()
    role = get_user_role(user_id)
    if role == Role.OWNER:
        return all_devices
    if role != Role.USER:
        return {}
    record = access_store.get_user(user_id) or {}
    permitted = set((record.get("permissions") or {}).get("devices") or [])
    return {device_id: info for device_id, info in all_devices.items() if device_id in permitted}


def _callback_visible_to_user(callback: str, user_id: int | None = None) -> bool:
    """Render a device action only when the same server-side gate will allow it."""
    user_id = int(user_id if user_id is not None else CURRENT_TG_USER.get())
    if get_user_role(user_id) == Role.OWNER:
        return True
    return access_store.can_use_callback(
        user_id, callback, ADMIN_ID, SESSION.get("target")
    )


def _action_button(kb: InlineKeyboardBuilder, *, text: str, callback: str,
                   style: str = "primary", user_id: int | None = None) -> None:
    if _callback_visible_to_user(callback, user_id):
        kb.button(text=text, callback_data=callback, style=style)


def devices_menu(user_id: int | None = None):
    user_id = int(user_id if user_id is not None else CURRENT_TG_USER.get())
    is_owner = get_user_role(user_id) == Role.OWNER
    kb = InlineKeyboardBuilder()
    devs = _visible_device_items(user_id)
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
            text=_limit_button_label(f"{dot} {icon} {name}"),
            callback_data=f"dev:{device_id}",
            style="success" if is_online else "danger",
        )
    if is_owner:
        kb.button(text=_lex("devices_add_button"), callback_data="devmg:manualadd", style="primary")
        kb.button(text=_lex("devices_blocked_button"), callback_data="devmg:blocked", style="danger")
        kb.button(text=_lex("devices_clear_button"), callback_data="devmg:clear_all", style="danger")
    kb.button(text=_nav("refresh"), callback_data="menu:devices", style="success")
    kb.button(text=_nav("home"), callback_data="menu:main", style="primary")
    dev_count = len(devs)
    adjust_pattern = [1] * dev_count + ([2, 1, 2] if is_owner else [1, 1])
    kb.adjust(*adjust_pattern)
    return kb.as_markup()


def device_menu(device_id: str, user_id: int | None = None):
    if device_id == "all":
        return all_menu()
    user_id = int(user_id if user_id is not None else CURRENT_TG_USER.get())
    is_owner = get_user_role(user_id) == Role.OWNER
    kb = InlineKeyboardBuilder()

    # Exact-action grants reveal only the categories that contain a granted
    # action. `full_device` keeps the complete owner-style device menu.
    _action_button(kb, text=_nav("media"), callback="cat:media", style="success", user_id=user_id)
    _action_button(kb, text=_nav("screen"), callback="cat:screen", user_id=user_id)
    _action_button(kb, text=_nav("input"), callback="cat:input", user_id=user_id)
    _action_button(kb, text=_nav("system"), callback="cat:system", user_id=user_id)
    _action_button(kb, text=_nav("network"), callback="cat:network", user_id=user_id)
    _action_button(kb, text=_nav("files"), callback="cat:files", user_id=user_id)
    _action_button(kb, text=_nav("terminal"), callback="cat:terminal", user_id=user_id)
    _action_button(kb, text=_nav("power"), callback="cat:power", style="danger", user_id=user_id)
    _action_button(kb, text=_nav("pranks"), callback="cat:pranks", style="success", user_id=user_id)
    _action_button(kb, text=_nav("device_settings"), callback="cat:device", user_id=user_id)

    # Favorite actions (if configured)
    favs = devices.get_favorites(device_id)[:4]
    for action in favs:
        callback = CALLBACK_BY_ACTION.get(action, "noop")
        _action_button(
            kb,
            text=_lex("favorite_device_button", action=ACTION_LABELS.get(action, action)),
            callback=callback,
            user_id=user_id,
        )

    info = devices.get(device_id) or {}
    if is_owner:
        agent_ver = str(info.get("version") or "?")
        keeper_ver = str((info.get("guardian") or {}).get("version") or "?")
        kb.button(
            text=_limit_button_label(_lex("versions_button", agent=agent_ver, keeper=keeper_ver)),
            callback_data="versions:device", style="success",
        )
    _action_button(
        kb, text=_lex("device_stop_agent_button"), callback="cfm:stop",
        style="danger", user_id=user_id,
    )

    # Bottom row: Back
    kb.button(
        text=_lex("devices_back_list_button"),
        callback_data="menu:target" if is_owner else "menu:devices",
        style="danger",
    )

    kb.adjust(2)
    return kb.as_markup()


def all_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text=_lex("all_status_button"), callback_data="all:status", style="primary")
    kb.button(text=_lex("all_screenshot_button"), callback_data="all:screenshot", style="success")
    kb.button(text=_limit_button_label("⚠️ " + _lex("geo_beta")), callback_data="cmd:geo_location", style="primary")
    kb.button(text=_lex("all_lock_button"), callback_data="cmd:lock", style="danger")
    kb.button(text=_lex("all_mute_button"), callback_data="cmd:volume", style="primary")
    kb.button(text=_lex("all_stop_button"), callback_data="cfm:stop_all", style="danger")
    kb.button(text=_lex("devices_back_list_button"), callback_data="menu:target", style="primary")
    kb.button(text=_nav("home"), callback_data="menu:main", style="primary")
    kb.adjust(2)
    return kb.as_markup()


def back_to_device_kb():
    """Маленькая клавиатура «Назад» — появляется после каждой команды."""
    kb = InlineKeyboardBuilder()
    kb.button(text=_nav("back_device"), callback_data="back:device", style="primary")
    kb.button(text=_nav("home"), callback_data="menu:main", style="primary")
    kb.adjust(2)
    return kb.as_markup()


def power_menu():
    """Подменю подтверждения выключения / перезагрузки."""
    kb = InlineKeyboardBuilder()
    kb.button(text=_lex("power_reboot_button"), callback_data="power:reboot", style="danger")
    kb.button(text=_lex("power_shutdown_button"), callback_data="power:shutdown", style="danger")
    kb.button(text=_nav("back_device"), callback_data="back:device", style="primary")
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
    kb.button(text=_lex("power_confirm_button", action=label), callback_data=f"power_confirm:{action}", style="danger")
    kb.button(text=_nav("cancel"), callback_data="back:device", style="primary")
    kb.adjust(1, 1)
    return kb.as_markup()


def rotate_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text=_lex("rotate_standard_button"), callback_data="rotate:0", style="success")
    kb.button(text=_lex("rotate_right_button"), callback_data="rotate:90", style="primary")
    kb.button(text=_lex("rotate_inverted_button"), callback_data="rotate:180", style="primary")
    kb.button(text=_lex("rotate_left_button"), callback_data="rotate:270", style="primary")
    kb.button(text=_lex("rotate_screen_button"), callback_data="cat:screen", style="danger")
    kb.button(text=_nav("home"), callback_data="menu:main", style="primary")
    kb.adjust(2, 2, 2)
    return kb.as_markup()


def media_menu(user_id: int | None = None):
    kb = InlineKeyboardBuilder()
    _action_button(kb, text=_lex("media_screenshot_button"), callback="cmd:screenshot", style="success", user_id=user_id)
    _action_button(kb, text=_limit_button_label("⚠️ " + _lex("geo_beta")), callback="cmd:geo_location", user_id=user_id)
    _action_button(kb, text=_lex("media_webcam_button"), callback="cmd:webcam", style="success", user_id=user_id)
    _action_button(kb, text=_lex("media_mic_button"), callback="mic:opts", style="success", user_id=user_id)
    _action_button(kb, text=_lex("media_speak_button"), callback="cmd:sound", user_id=user_id)
    _action_button(kb, text=_lex("media_volume_button"), callback="vol:opts", user_id=user_id)
    _action_button(kb, text=_lex("media_mute_button"), callback="cmd:volume", user_id=user_id)
    kb.button(text=_nav("back_device"), callback_data="back:device", style="danger")
    kb.button(text=_nav("home"), callback_data="menu:main", style="primary")
    kb.adjust(2, 2, 2, 2)
    return kb.as_markup()


def screen_menu(target: str | None = None):
    target = target or SESSION.get("target") or ""
    is_nl = bool(SESSION.get(f"nightlight_{target}", False))
    kb = InlineKeyboardBuilder()
    kb.button(text=_lex("screen_off_button"), callback_data="fun:screenoff", style="danger")
    kb.button(text=_lex("screen_saver_button"), callback_data="fun:screensaver", style="primary")
    kb.button(text=_lex("screen_wallpaper_button"), callback_data="menu:wallpaper", style="success")
    kb.button(text=_lex("screen_brightness_button"), callback_data="cmd:brightness", style="success")
    if is_nl:
        kb.button(text=_lex("screen_nightlight_enabled_button"), callback_data="cmd:nightlight", style="danger")
    else:
        kb.button(text=_lex("screen_nightlight_disabled_button"), callback_data="cmd:nightlight", style="primary")
    kb.button(text=_lex("screen_rotate_button"), callback_data="cmd:rotate", style="primary")
    kb.button(text=_nav("back_device"), callback_data="back:device", style="danger")
    kb.button(text=_nav("home"), callback_data="menu:main", style="primary")
    kb.adjust(2, 2, 2, 2)
    return kb.as_markup()


def wallpaper_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text=_lex("wallpaper_random_meme_button"), callback_data="cmd:wallpaper_random_meme", style="success")
    kb.button(text=_lex("wallpaper_photo_button"), callback_data="cmd:wallpaper_photo_guide", style="primary")
    kb.button(text=_lex("wallpaper_url_button"), callback_data="fun:wallpaper", style="primary")
    kb.button(text=_lex("wallpaper_restore_button"), callback_data="cmd:prank_restore_wallpaper", style="danger")
    kb.button(text=_lex("wallpaper_back_screen_button"), callback_data="cat:screen", style="primary")
    kb.button(text=_nav("home"), callback_data="menu:main", style="primary")
    kb.adjust(2)
    return kb.as_markup()


def input_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text=_lex("input_clipboard_read_button"), callback_data="cmd:clipboard", style="success")
    kb.button(text=_lex("input_clipboard_paste_button"), callback_data="cmd:clipset", style="primary")
    kb.button(text=_lex("input_type_text_button"), callback_data="fun:type", style="primary")
    kb.button(text=_lex("input_hotkey_button"), callback_data="fun:hotkey", style="primary")
    kb.button(text=_lex("input_mouse_swap_button"), callback_data="prank:swapmouse", style="primary")
    kb.button(text=_lex("input_crazy_cursor_button"), callback_data="prank:crazycursor", style="primary")
    kb.button(text=_nav("back_device"), callback_data="back:device", style="danger")
    kb.button(text=_nav("home"), callback_data="menu:main", style="primary")
    kb.adjust(2, 2, 2, 2)
    return kb.as_markup()


def system_menu(user_id: int | None = None):
    kb = InlineKeyboardBuilder()
    _action_button(kb, text=_lex("system_sysinfo_button"), callback="cmd:sysinfo", user_id=user_id)
    _action_button(kb, text=_lex("system_uptime_button"), callback="cmd:sys_uptime", style="success", user_id=user_id)
    _action_button(kb, text=_lex("system_battery_button"), callback="cmd:battery", style="success", user_id=user_id)
    _action_button(kb, text=_lex("system_disks_button"), callback="cmd:disks", user_id=user_id)
    _action_button(kb, text=_lex("system_smart_button"), callback="cmd:smart", style="success", user_id=user_id)
    _action_button(kb, text=_lex("system_clean_temp_button"), callback="cmd:sys_clean_temp", style="danger", user_id=user_id)
    _action_button(kb, text=_lex("system_apps_button"), callback="cmd:apps", user_id=user_id)
    _action_button(kb, text=_lex("system_capabilities_button"), callback="cmd:capabilities", user_id=user_id)
    _action_button(kb, text=_lex("system_refresh_status_button"), callback="cmd:status", style="success", user_id=user_id)
    kb.button(text=_nav("back_device"), callback_data="back:device", style="danger")
    kb.adjust(2)
    return kb.as_markup()


def network_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text=_lex("network_external_ip_button"), callback_data="fun:extip", style="success")
    kb.button(text=_lex("network_ping_button"), callback_data="cmd:net_ping", style="success")
    kb.button(text=_lex("network_adapters_button"), callback_data="cmd:network", style="primary")
    kb.button(text=_lex("network_wifi_button"), callback_data="cmd:wifi", style="primary")
    kb.button(text=_lex("network_usb_button"), callback_data="cmd:usb", style="primary")
    kb.button(text=_lex("network_bluetooth_button"), callback_data="cmd:bluetooth", style="primary")
    kb.button(text=_lex("network_netstat_button"), callback_data="cmd:netstat", style="primary")
    kb.button(text=_nav("back_device"), callback_data="back:device", style="danger")
    kb.button(text=_nav("home"), callback_data="menu:main", style="primary")
    kb.adjust(2)
    return kb.as_markup()


def files_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text=_lex("files_list_button"), callback_data="files:list", style="primary")
    kb.button(text=_lex("files_find_button"), callback_data="files:find", style="primary")
    kb.button(text=_lex("files_download_button"), callback_data="files:get", style="success")
    kb.button(text=_lex("files_upload_button"), callback_data="files:put", style="success")
    kb.button(text=_lex("files_open_path_button"), callback_data="files:open", style="primary")
    kb.button(text=_lex("files_delete_button"), callback_data="files:del", style="danger")
    kb.button(text=_nav("back_device"), callback_data="back:device", style="danger")
    kb.button(text=_nav("home"), callback_data="menu:main", style="primary")
    kb.adjust(2, 2, 2, 2)
    return kb.as_markup()


def terminal_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text=_lex("terminal_processes_button"), callback_data="proc:0", style="primary")
    kb.button(text=_lex("terminal_kill_process_button"), callback_data="cmd:prockillname", style="danger")
    kb.button(text=_lex("terminal_shell_button"), callback_data="cfm:shell", style="danger")
    kb.button(text=_lex("terminal_open_app_button"), callback_data="cmd:open_app", style="primary")
    kb.button(text=_lex("terminal_startup_button"), callback_data="cmd:startup", style="primary")
    kb.button(text=_lex("terminal_services_button"), callback_data="cmd:services", style="primary")
    kb.button(text=_lex("terminal_history_button"), callback_data="cmd:cmdhistory", style="primary")
    kb.button(text=_nav("back_device"), callback_data="back:device", style="danger")
    kb.adjust(2)
    return kb.as_markup()


# Псевдонимы обратной совместимости
control_menu = input_menu
fun_menu = terminal_menu


def power_menu_new(user_id: int | None = None):
    kb = InlineKeyboardBuilder()
    target = SESSION.get("target") or ""
    dev_info = devices.get(target) or {}
    is_sleeping = dev_info.get("standby", False)
    if is_sleeping:
        _action_button(kb, text=_lex("power_wake_agent_button"), callback="cmd:wake", style="success", user_id=user_id)
    else:
        _action_button(kb, text=_lex("power_standby_agent_button"), callback="cmd:standby_sleep", user_id=user_id)
    _action_button(kb, text=_lex("power_lock_screen_button"), callback="cmd:lock", style="danger", user_id=user_id)
    _action_button(kb, text=_lex("power_sleep_device_button"), callback="power:sleep", style="danger", user_id=user_id)
    _action_button(kb, text=_lex("power_reboot_device_button"), callback="power:reboot", style="danger", user_id=user_id)
    _action_button(kb, text=_lex("power_shutdown_device_button"), callback="power:shutdown", style="danger", user_id=user_id)
    _action_button(kb, text=_lex("power_wol_button"), callback="cmd:wol", user_id=user_id)
    _action_button(kb, text=_lex("power_autorun_status_button"), callback="cmd:autorun_status", user_id=user_id)
    _action_button(kb, text=_lex("power_autorun_enable_button"), callback="cmd:autorun_enable", style="success", user_id=user_id)
    _action_button(kb, text=_lex("power_autorun_disable_button"), callback="cmd:autorun_disable", style="danger", user_id=user_id)
    _action_button(kb, text=_lex("power_guardian_menu_button"), callback="cmd:guardian_menu", user_id=user_id)
    _action_button(kb, text=_lex("power_stop_agent_button"), callback="cfm:stop", style="danger", user_id=user_id)
    kb.button(text=_nav("back_device"), callback_data="back:device", style="primary")
    kb.button(text=_nav("home"), callback_data="menu:main", style="primary")
    kb.adjust(2)
    return kb.as_markup()


def guardian_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text=_lex("guardian_status_button"), callback_data="cmd:guardian_status", style="primary")
    kb.button(text=_lex("guardian_start_agent_button"), callback_data="cmd:guardian_start", style="success")
    kb.button(text=_lex("guardian_stop_agent_button"), callback_data="cfm:guardian_stop", style="danger")
    kb.button(text=_lex("guardian_restart_agent_button"), callback_data="cmd:guardian_restart", style="primary")
    kb.button(text=_lex("guardian_auto_enable_button"), callback_data="cmd:guardian_auto_on", style="success")
    kb.button(text=_lex("guardian_auto_disable_button"), callback_data="cmd:guardian_auto_off", style="danger")
    kb.button(text=_lex("guardian_back_power_button"), callback_data="cat:power", style="primary")
    kb.adjust(2)
    return kb.as_markup()


def pranks_menu(page: int = 1, target: str | None = None):
    target = target or SESSION.get("target") or ""
    mouse_swapped = bool(SESSION.get(f"mouse_swapped_{target}", False))
    desktop_hidden = bool(SESSION.get(f"desktop_hidden_{target}", False))
    kb = InlineKeyboardBuilder()

    # Главная кнопка экстренной отмены любых приколов на каждой странице
    kb.button(text=_lex("prank_stop_all_button"), callback_data="prank:stop_all", style="danger")

    if page == 1:
        # Страница 1: Экраны & Визуал (10)
        kb.button(text=_lex("prank_screamer_button"), callback_data="prank:screamer", style="danger")
        kb.button(text=_lex("prank_rickroll_button"), callback_data="prank:rickroll50", style="success")
        kb.button(text=_lex("prank_matrix_button"), callback_data="prank:matrix", style="success")
        kb.button(text=_lex("prank_bsod_button"), callback_data="cmd:prank_bsod", style="danger")
        kb.button(text=_lex("prank_fake_update_button"), callback_data="cmd:prank_fake_update", style="primary")
        kb.button(text=_lex("prank_rotate_screen_button"), callback_data="cmd:prank_invert_screen", style="primary")
        if desktop_hidden:
            kb.button(text=_lex("prank_icons_hidden_button"), callback_data="prank:hidedesktop", style="danger")
        else:
            kb.button(text=_lex("prank_hide_icons_button"), callback_data="prank:hidedesktop", style="primary")
        kb.button(text=_lex("prank_window_dance_button"), callback_data="prank:dancewin", style="primary")
        kb.button(text=_lex("prank_black_screen_button"), callback_data="prank:blackscreen", style="danger")
        kb.button(text=_lex("prank_shake_window_button"), callback_data="cmd:prank_shake_window", style="primary")
    elif page == 2:
        # Страница 2: Звуки & Голос (10)
        kb.button(text=_lex("prank_siren_button"), callback_data="prank:siren", style="danger")
        kb.button(text=_lex("prank_shout_button"), callback_data="prank:shout", style="success")
        kb.button(text=_lex("prank_morse_button"), callback_data="cmd:prank_beep_morse", style="primary")
        kb.button(text=_lex("prank_spooky_button"), callback_data="cmd:prank_sound_spooky", style="danger")
        kb.button(text=_lex("prank_fart_button"), callback_data="cmd:prank_sound_fart", style="primary")
        kb.button(text=_lex("prank_volume_jump_button"), callback_data="cmd:prank_volume_jump", style="primary")
        kb.button(text=_lex("prank_speak_time_button"), callback_data="cmd:prank_speak_time", style="success")
        kb.button(text=_lex("prank_whisper_button"), callback_data="cmd:prank_say_whisper", style="danger")
        kb.button(text=_lex("prank_laugh_track_button"), callback_data="cmd:prank_laugh_track", style="primary")
        kb.button(text=_lex("prank_random_beeps_button"), callback_data="cmd:prank_random_beeps", style="primary")
    elif page == 3:
        # Страница 3: Мышь & Клавиатура (10)
        if mouse_swapped:
            kb.button(text=_lex("prank_mouse_swapped_button"), callback_data="prank:swapmouse", style="danger")
        else:
            kb.button(text=_lex("prank_mouse_swap_button"), callback_data="prank:swapmouse", style="primary")
        kb.button(text=_lex("prank_crazy_cursor_button"), callback_data="prank:crazycursor", style="primary")
        kb.button(text=_lex("prank_slow_mouse_button"), callback_data="cmd:prank_slow_mouse", style="primary")
        kb.button(text=_lex("prank_glitch_cursor_button"), callback_data="cmd:prank_glitch_cursor", style="primary")
        kb.button(text=_lex("prank_random_clicks_button"), callback_data="cmd:prank_random_clicks", style="primary")
        kb.button(text=_lex("prank_keyboard_disco_button"), callback_data="cmd:prank_keyboard_disco", style="success")
        kb.button(text=_lex("prank_caps_disco_button"), callback_data="cmd:prank_caps_disco", style="primary")
        kb.button(text=_lex("prank_cursor_circle_button"), callback_data="cmd:prank_cursor_circle", style="primary")
        kb.button(text=_lex("prank_notepad_type_button"), callback_data="cmd:prank_open_notepad_type", style="primary")
        kb.button(text=_lex("prank_hacker_typer_button"), callback_data="cmd:prank_hacker_typer", style="success")
    elif page == 4:
        # Страница 4: Фейки & Системный хаос (10)
        kb.button(text=_lex("prank_spam_windows_button"), callback_data="fun:spam", style="danger")
        kb.button(text=_lex("prank_calculator_spam_button"), callback_data="cmd:prank_open_calc_spam", style="primary")
        kb.button(text=_lex("prank_fake_virus_button"), callback_data="cmd:prank_fake_virus", style="danger")
        kb.button(text=_lex("prank_alert_loop_button"), callback_data="cmd:prank_alert_loop", style="primary")
        kb.button(text=_lex("prank_clipboard_spam_button"), callback_data="cmd:prank_paste_clipboard_spam", style="primary")
        kb.button(text=_lex("prank_reverse_clipboard_button"), callback_data="cmd:prank_type_reversed", style="primary")
        kb.button(text=_lex("prank_error_spam_button"), callback_data="cmd:prank_fake_error_spam", style="danger")
        kb.button(text=_lex("prank_fake_sys32_button"), callback_data="cmd:prank_fake_delete_sys32", style="danger")
        kb.button(text=_lex("prank_ghost_typer_button"), callback_data="cmd:prank_ghost_typer", style="primary")
        kb.button(text=_lex("prank_earthquake_button"), callback_data="cmd:prank_earthquake", style="danger")
    else:
        # Страница 5: Мемы & Ультра-Троллинг (10)
        kb.button(text=_lex("prank_meme_wallpaper_button"), callback_data="cmd:prank_meme_wallpaper", style="success")
        kb.button(text=_lex("prank_cat_invaders_button"), callback_data="cmd:prank_cat_invaders", style="primary")
        kb.button(text=_lex("prank_fake_ransom_cats_button"), callback_data="cmd:prank_fake_ransom_cats", style="danger")
        kb.button(text=_lex("prank_nyan_stream_button"), callback_data="cmd:prank_nyan_stream", style="success")
        kb.button(text=_lex("prank_confetti_winner_button"), callback_data="cmd:prank_confetti_winner", style="success")
        kb.button(text=_lex("prank_fake_low_battery_button"), callback_data="cmd:prank_low_battery_fake", style="danger")
        kb.button(text=_lex("prank_fbi_lock_button"), callback_data="cmd:prank_fbi_lock", style="danger")
        kb.button(text=_lex("prank_browser_memes_button"), callback_data="cmd:prank_open_browser_memes", style="success")
        kb.button(text=_lex("prank_ascii_rickroll_button"), callback_data="cmd:prank_rickroll_terminal", style="success")
        kb.button(text=_lex("prank_random_site_button"), callback_data="prank:randomsite", style="primary")

    # Панель вкладок категорий приколов (5 страниц)
    for tab_page, tab_key in enumerate(("visual", "sound", "input", "chaos", "memes"), start=1):
        selected_suffix = "_selected" if page == tab_page else ""
        kb.button(
            text=_lex(f"prank_tab_{tab_key}{selected_suffix}"),
            callback_data=f"prankpage:{tab_page}",
            style="primary",
        )

    kb.button(text=_nav("back_device"), callback_data="back:device", style="danger")
    kb.button(text=_nav("home"), callback_data="menu:main", style="primary")

    kb.adjust(1, 2, 2, 2, 2, 2, 5, 2)
    return kb.as_markup()


def device_settings_menu(user_id: int | None = None):
    device_id = SESSION.get("target") or ""
    user_id = int(user_id if user_id is not None else CURRENT_TG_USER.get())
    is_owner = get_user_role(user_id) == Role.OWNER
    kb = InlineKeyboardBuilder()
    if is_owner:
        kb.button(text=_lex("settings_rename_button"), callback_data=f"devmg:rename:{device_id}", style="primary")
    kb.button(text=_lex("settings_favorites_button"), callback_data="fav:menu", style="primary")
    kb.button(text=_lex("settings_history_button"), callback_data="hist:0", style="primary")
    if is_owner:
        kb.button(text=_lex("settings_versions_button"), callback_data="versions:device", style="success")
    kb.button(text=_lex("settings_autorun_status_button"), callback_data="cmd:autorun_status", style="primary")
    kb.button(text=_lex("settings_autorun_enable_button"), callback_data="cmd:autorun_enable", style="success")
    kb.button(text=_lex("settings_autorun_disable_button"), callback_data="cmd:autorun_disable", style="danger")
    if is_owner:
        kb.button(text=_lex("settings_remove_device_button"), callback_data=f"devmg:delete:{device_id}", style="danger")
        kb.button(text=_lex("settings_block_device_button"), callback_data=f"devmg:block:{device_id}", style="danger")
        kb.button(text=_lex("settings_uninstall_button"), callback_data=f"devmg:uninstall:{device_id}", style="danger")
    kb.button(text=_nav("back_device"), callback_data="back:device", style="primary")
    kb.button(text=_nav("home"), callback_data="menu:main", style="primary")
    kb.adjust(2)
    return kb.as_markup()


def mic_options_menu():
    kb = InlineKeyboardBuilder()
    for sec in (5, 15, 30, 60):
        kb.button(
            text=_lex("microphone_duration_button", seconds=str(sec)),
            callback_data=f"micdur:{sec}",
            style="success",
        )
    kb.button(text=_nav("media"), callback_data="cat:media", style="danger")
    kb.button(text=_nav("home"), callback_data="menu:main", style="primary")
    kb.adjust(2, 2, 2)
    return kb.as_markup()


def vol_options_menu():
    kb = InlineKeyboardBuilder()
    for lvl in (0, 25, 50, 75, 100):
        kb.button(
            text=_lex("volume_level_button", level=str(lvl)),
            callback_data=f"volq:{lvl}",
            style="primary",
        )
    kb.button(text=_nav("media"), callback_data="cat:media", style="danger")
    kb.adjust(3, 2, 1)
    return kb.as_markup()


def confirm_kb(yes_cb: str, yes_text: str | None = None, no_cb: str = "back:device"):
    kb = InlineKeyboardBuilder()
    kb.button(
        text=_limit_button_label(yes_text or _lex("confirm_execute_button")),
        callback_data=yes_cb,
        style="danger",
    )
    kb.button(text=_nav("cancel"), callback_data=no_cb, style="primary")
    kb.adjust(1, 1)
    return kb.as_markup()


def events_menu():
    s = bot_settings.all_settings()
    kb = InlineKeyboardBuilder()
    online_mark = "✅" if s.get("notify_online") else "❌"
    kb.button(
        text=_lex("events_online_button", mark=online_mark),
        callback_data="ev:toggle:notify_online",
        style="success" if s.get("notify_online") else "danger",
    )
    offline_mark = "✅" if s.get("notify_offline") else "❌"
    kb.button(
        text=_lex("events_offline_button", mark=offline_mark),
        callback_data="ev:toggle:notify_offline",
        style="success" if s.get("notify_offline") else "danger",
    )
    battery_mark = "✅" if s.get("notify_battery_low") else "❌"
    kb.button(
        text=_lex("events_battery_button", mark=battery_mark),
        callback_data="ev:toggle:notify_battery_low",
        style="success" if s.get("notify_battery_low") else "danger",
    )
    kb.button(text=_lex("events_quiet_hours_button"), callback_data="ev:quiet", style="primary")
    kb.button(text=_lex("events_digest_button"), callback_data="ev:digest", style="primary")
    kb.button(text=_lex("events_admins_button"), callback_data="ev:admins", style="primary")
    auto_on = get_server_autostart_status()
    kb.button(
        text=_lex(
            "events_server_autostart_button",
            state="ВКЛ ✅" if auto_on else "ВЫКЛ ❌",
        ),
        callback_data="ev:server_autostart:toggle",
        style="success" if auto_on else "danger",
    )
    kb.button(text=_nav("home"), callback_data="menu:main", style="primary")
    kb.adjust(2)
    return kb.as_markup()


def quiet_hours_menu():
    s = bot_settings.all_settings()
    cur_f = str(s.get("quiet_from") or "")
    cur_t = str(s.get("quiet_to") or "")
    kb = InlineKeyboardBuilder()
    for f, t, key in (
        ("", "", "quiet_disable_button"),
        ("23", "8", "quiet_2308_button"),
        ("22", "7", "quiet_2207_button"),
        ("0", "6", "quiet_0006_button"),
    ):
        active = cur_f == f and cur_t == t
        mark = "✅" if active else ""
        kb.button(
            text=_limit_button_label(f"{mark} {_lex(key)}".strip()),
            callback_data=f"quiet:{f}:{t}",
            style="success" if active else "primary",
        )
    kb.button(text=_lex("events_back_button"), callback_data="ev:menu", style="primary")
    kb.adjust(2)
    return kb.as_markup()


def digest_menu():
    s = bot_settings.all_settings()
    cur = str(s.get("report_hour") or "")
    kb = InlineKeyboardBuilder()
    for val, key in (
        ("", "digest_disable_button"),
        ("9", "digest_09_button"),
        ("21", "digest_21_button"),
    ):
        active = cur == val
        mark = "✅" if active else ""
        kb.button(
            text=_limit_button_label(f"{mark} {_lex(key)}".strip()),
            callback_data=f"digest:{val}",
            style="success" if active else "primary",
        )
    kb.button(text=_lex("events_back_button"), callback_data="ev:menu", style="primary")
    kb.adjust(2)
    return kb.as_markup()


def admins_menu():
    s = bot_settings.all_settings()
    kb = InlineKeyboardBuilder()
    kb.button(
        text=_lex("admin_owner_id_button", user_id=str(ADMIN_ID)),
        callback_data="noop",
        style="primary",
    )
    for a in s.get("admins") or []:
        kb.button(
            text=_lex("admin_remove_id_button", user_id=str(a)),
            callback_data=f"adm:rm:{a}",
            style="danger",
        )
    kb.button(text=_lex("admins_add_button"), callback_data="adm:add", style="success")
    kb.button(text=_lex("events_back_button"), callback_data="ev:menu", style="primary")
    kb.adjust(1)
    return kb.as_markup()


ROLE_XLEX_KEYS = {
    Role.OWNER: "role_owner",
    Role.USER: "role_user",
    Role.GUEST: "role_guest",
    Role.BLOCKED: "role_blocked",
}

USER_PERMISSION_CHOICES = {
    "cmd:status": "perm_status",
    "cmd:sysinfo": "perm_sysinfo",
    "cmd:battery": "perm_battery",
    "cmd:screenshot": "perm_screenshot",
    "cmd:lock": "perm_lock",
    "full_device": "perm_full_device",
}


def _user_label(record: dict) -> str:
    username = str(record.get("username") or "").strip()
    name = str(record.get("display_name") or "").strip()
    if username:
        return f"@{username}"
    if name:
        return name
    return f"id:{record.get('id', '?')}"


def _limit_button_label(value: object, max_utf16_units: int = 64) -> str:
    """Fit Telegram button text by its UTF-16 limit and add an ellipsis if cut."""
    text = str(value)
    units = len(text.encode("utf-16-le")) // 2
    if units <= max_utf16_units:
        return text
    budget = max(0, max_utf16_units - 1)
    output: list[str] = []
    used = 0
    for char in text:
        char_units = 2 if ord(char) > 0xFFFF else 1
        if used + char_units > budget:
            break
        output.append(char)
        used += char_units
    return "".join(output) + "…"


def _role_label(role: str) -> str:
    key = ROLE_XLEX_KEYS.get(role)
    return _lex(key) if key else str(role)


def admin_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text=_nav("admin_users"), callback_data="admin:users", style="primary")
    kb.button(text=_nav("admin_audit"), callback_data="admin:audit", style="primary")
    kb.button(text=_nav("admin_texts"), callback_data="admin:texts", style="primary")
    mode = xlex.normalize_style(bot_settings.get("ui_style", "technical"))
    kb.button(
        text=_lex("admin_style_button", style_name=xlex.STYLE_NAMES[mode]),
        callback_data="admin:style",
        style="success",
    )
    kb.button(text=_nav("admin_server"), callback_data="menu:server", style="primary")
    kb.button(text=_nav("home"), callback_data="menu:main", style="primary")
    kb.adjust(2)
    return kb.as_markup()


_TEXT_TYPES = {
    "start_guest": "Приветствие гостя",
    "start_user": "Приветствие пользователя",
    "start_owner": "Приветствие владельца",
    "blocked": "Сообщение заблокированному",
}
_TEXT_NAV_KEYS = {
    "start_guest": "text_start_guest",
    "start_user": "text_start_user",
    "start_owner": "text_start_owner",
    "blocked": "text_blocked",
}
TEXT_LABELS = {
    f"{style}_{key}": f"{xlex.STYLE_NAMES[style]} · {label}"
    for style in xlex.STYLES
    for key, label in _TEXT_TYPES.items()
}


def admin_texts_menu():
    kb = InlineKeyboardBuilder()
    mode = xlex.normalize_style(bot_settings.get("ui_style", "technical"))
    for suffix in _TEXT_TYPES:
        key = f"{mode}_{suffix}"
        label = _nav(_TEXT_NAV_KEYS[suffix])
        preview = text_store.get(key).replace("\n", " ")[:34]
        text = _lex("admin_text_preview_button", section=label, preview=preview)
        kb.button(
            text=_limit_button_label(text, 62),
            callback_data=f"admin:text:{key}",
            style="primary",
        )
    kb.button(text=_nav("change_style"), callback_data="admin:style", style="primary")
    kb.button(text=_nav("back"), callback_data="menu:admin", style="primary")
    kb.adjust(2)
    return kb.as_markup()


def admin_users_menu():
    kb = InlineKeyboardBuilder()
    users = access_store.list_users()
    if not users:
        kb.button(text=_lex("no_users"), callback_data="noop", style="primary")
    for record in users[:30]:
        uid = int(record.get("id") or 0)
        role = access_store.get_role(uid, ADMIN_ID)
        blocked = role == Role.BLOCKED
        blocked_prefix = _lex("admin_user_blocked_prefix") if blocked else ""
        text = _lex(
            "admin_user_row",
            blocked_prefix=blocked_prefix,
            user=_user_label(record),
            role=_role_label(role),
        )
        kb.button(
            text=_limit_button_label(text, 62),
            callback_data=f"admin:user:{uid}",
            style="danger" if blocked else "primary",
        )
    kb.button(text=_nav("back"), callback_data="menu:admin", style="primary")
    kb.adjust(1)
    return kb.as_markup()


def admin_user_menu(user_id: int):
    record = access_store.get_user(user_id) or {"id": user_id, "role": Role.GUEST, "permissions": {}}
    role = access_store.get_role(user_id, ADMIN_ID)
    blocked = role == Role.BLOCKED
    kb = InlineKeyboardBuilder()
    if user_id != ADMIN_ID:
        kb.button(text=_nav("make_user"), callback_data=f"admin:role:{user_id}:user", style="success")
        kb.button(text=_nav("make_guest"), callback_data=f"admin:role:{user_id}:guest", style="primary")
        kb.button(
            text=_nav("unblock_user" if blocked else "block_user"),
            callback_data=f"admin:block:{user_id}",
            style="success" if blocked else "danger",
        )
        kb.button(text=_nav("grant_devices"), callback_data=f"admin:devices:{user_id}", style="primary")
        kb.button(text=_nav("grant_buttons"), callback_data=f"admin:perms:{user_id}", style="primary")
        kb.button(text=_nav("message_user"), callback_data=f"admin:message:{user_id}", style="primary")
    kb.button(text=_nav("back_users"), callback_data="admin:users", style="primary")
    kb.adjust(1)
    return kb.as_markup()


def admin_devices_menu(user_id: int):
    record = access_store.get_user(user_id) or {}
    granted = set((record.get("permissions") or {}).get("devices") or [])
    kb = InlineKeyboardBuilder()
    for device_id, info in sorted(devices.all().items()):
        name = str(info.get("name") or device_id)
        kb.button(
            text=_limit_button_label(
                _lex("permission_enabled" if device_id in granted else "permission_disabled", item=name),
                62,
            ),
            callback_data=f"admin:device:{user_id}:{device_id}",
            style="success" if device_id in granted else "primary",
        )
    kb.button(text=_nav("back_to_user"), callback_data=f"admin:user:{user_id}", style="primary")
    kb.adjust(1)
    return kb.as_markup()


def admin_permissions_menu(user_id: int):
    record = access_store.get_user(user_id) or {}
    granted = set((record.get("permissions") or {}).get("callbacks") or [])
    kb = InlineKeyboardBuilder()
    for callback, label_key in USER_PERMISSION_CHOICES.items():
        enabled = callback in granted
        kb.button(
            text=_limit_button_label(
                _lex("permission_enabled" if enabled else "permission_disabled", item=_nav(label_key)),
                62,
            ),
            callback_data=f"admin:perm:{user_id}:{callback.replace(':', '_')}",
            style="success" if enabled else "primary",
        )
    kb.button(text=_nav("back_to_user"), callback_data=f"admin:user:{user_id}", style="primary")
    kb.adjust(1)
    return kb.as_markup()


def guest_devices_menu():
    kb = InlineKeyboardBuilder()
    for _, info in sorted(devices.all().items()):
        name = str(info.get("name") or "Устройство")
        status = "онлайн" if _status_dot(info) in ("🟢", "🟡") else "офлайн"
        kb.button(
            text=_limit_button_label(_lex("guest_device_row", name=name, status=status), 62),
            callback_data="guest:readonly",
            style="primary",
        )
    kb.button(text=_nav("guest_home"), callback_data="menu:main", style="primary")
    kb.adjust(1)
    return kb.as_markup()


def server_menu(user_id: int | None = None):
    user_id = int(user_id if user_id is not None else CURRENT_TG_USER.get())
    role = get_user_role(user_id)
    kb = InlineKeyboardBuilder()
    if role == Role.OWNER:
        approval = bool(bot_settings.get("require_device_approval", True))
        kb.button(text=_lex("server_versions", version=VERSION), callback_data="versions:server", style="success")
        kb.button(text=_lex("server_status"), callback_data="server:status", style="primary")
        kb.button(text=_lex("server_specs"), callback_data="server:specs", style="primary")
        kb.button(text=_lex("server_metrics"), callback_data="server:metrics", style="primary")
        kb.button(text=_lex("server_chart"), callback_data="server:chart", style="primary")
        kb.button(text=_lex("server_logs"), callback_data="server:logs", style="primary")
        kb.button(text=_lex("server_terminal"), callback_data="server:terminal", style="primary")
        kb.button(
            text=_lex("server_approval", state='включено' if approval else 'выключено'),
            callback_data="server:approval",
            style="success" if approval else "danger",
        )
        kb.button(text=_lex("server_restart"), callback_data="server:restart", style="danger")
        kb.button(text=_lex("server_update"), callback_data="server:update", style="primary")
        kb.button(text=_lex("server_rollback"), callback_data="server:rollback", style="danger")
    kb.button(text=_nav("home"), callback_data="menu:main", style="primary")
    kb.adjust(2)
    return kb.as_markup()


def server_overview_text(*, approval: bool, connected: bool) -> str:
    approval_state = "включено" if approval else "выключено"
    broker_state = "подключён" if connected else "ожидает подключения"
    return (
        f"<b>{html.escape(_lex('server_overview_title'))}</b>\n"
        f"{html.escape(_lex('server_build_line', build=XIDER_BUILD_CODE))}\n"
        f"{html.escape(_lex('server_mqtt_line', state=broker_state))}\n"
        f"{html.escape(_lex('server_approval_line', state=approval_state))}\n"
        f"{html.escape(_lex('server_secret_notice'))}"
    )


def server_confirmation_text(action: str) -> str:
    action_keys = {
        "restart": "server_action_restart",
        "update": "server_action_update",
        "rollback": "server_action_rollback",
    }
    action_key = action_keys.get(action)
    if action_key is None:
        return f"<b>{html.escape(_lex('server_unknown_action'))}</b>"
    return (
        f"<b>{html.escape(_lex('server_confirm_title'))}</b>\n"
        f"{html.escape(_lex('server_confirm_warning'))}\n"
        f"{html.escape(_lex(action_key))}"
    )


def server_confirm_menu(action: str):
    label_keys = {
        "restart": "server_action_short_restart",
        "update": "server_action_short_update",
        "rollback": "server_action_short_rollback",
    }
    kb = InlineKeyboardBuilder()
    if action in label_keys:
        label = _lex("server_confirm_button", action=_lex(label_keys[action]))
        kb.button(text=_limit_button_label(label), callback_data=f"server_confirm:{action}", style="danger")
    kb.button(text=_nav("cancel"), callback_data="menu:server", style="primary")
    kb.adjust(1)
    return kb.as_markup()


def server_metrics_text(snapshot: dict) -> str:
    return (
        f"<b>{html.escape(_lex('server_metrics_title'))}</b>\n"
        f"{html.escape(_lex('server_cpu'))}: <b>{float(snapshot['load']):.1f}%</b>\n"
        f"{html.escape(_lex('server_ram'))}: <b>{float(snapshot['memory']):.1f}%</b>\n"
        f"{html.escape(_lex('server_disk'))}: <b>{float(snapshot['disk']):.1f}%</b>\n\n"
        f"{html.escape(_lex('server_metrics_hint'))}"
    )


def server_terminal_text() -> str:
    commands = (
        "<code>uptime</code>, <code>memory</code>, <code>disk</code>, "
        "<code>processes</code>, <code>service</code>, <code>logs</code>, "
        "<code>specs</code> / <code>fastfetch</code>"
    )
    return (
        f"<b>{html.escape(_lex('server_terminal_title'))}</b>\n"
        f"{html.escape(_lex('server_terminal_intro'))}\n"
        f"{html.escape(_lex('server_terminal_allowlist'))}\n{commands}\n\n"
        f"{html.escape(_lex('server_terminal_prompt'))}"
    )


def blocked_menu():
    s = bot_settings.all_settings()
    kb = InlineKeyboardBuilder()
    for bid in s.get("blocked_ids") or []:
        kb.button(
            text=_lex("blocked_unblock_button", device_id=str(bid)[:24]),
            callback_data=f"devmg:unblock:{bid}",
            style="success",
        )
    kb.button(text=_lex("blocked_back_button"), callback_data="menu:target", style="primary")
    kb.adjust(1)
    return kb.as_markup()


def _favorites_kb(device_id: str, favs: list):
    kb = InlineKeyboardBuilder()
    for action in FAVORITABLE:
        is_fav = action in favs
        kb.button(
            text=_lex(
                "favorite_remove_button" if is_fav else "favorite_add_button",
                action=ACTION_LABELS[action],
            ),
            callback_data=f"fav:toggle:{action}",
            style="success" if is_fav else "primary",
        )
    kb.button(text=_nav("back_device"), callback_data="back:device", style="primary")
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

def publish(command_name: str, *, _target: str | None = None, **kwargs) -> bool:
    """Send to the selected target, or to an explicit target for an in-flight form."""
    target = _target or SESSION.get("target")
    if not target:
        log.warning("⚠️ Попытка отправки '%s' без выбранной цели", command_name)
        return False
    audit("cmd_publish", action=command_name, target=target, kwargs_keys=",".join(kwargs.keys()))
    args_str = f" args={kwargs}" if kwargs else ""
    log.info("🚀 [CMD] -> %s (%s) | action='%s'%s", target, target_label(target), command_name, args_str)
    HISTORY.setdefault(target, []).append((command_name, dict(kwargs), time.time()))
    del HISTORY[target][:-20]
    return transport.publish_command(target, command_name, **kwargs)


def publish_tracked(action: str, *, _target: str | None = None, **kwargs) -> tuple[bool, str]:
    """Send with a correlation ID and optionally pin it to a captured target."""
    command_id = uuid.uuid4().hex[:12]
    ok = publish(action, _target=_target, id=command_id, **kwargs)
    return ok, command_id

def _no_target_text() -> str:
    return _lex("target_required")


async def _stale_card_allows_replacement(chat_id: int, message_id: int, exc: Exception) -> bool:
    """Return true only when Telegram confirms the old card is gone or removed."""
    detail = str(exc).lower()
    if "message to edit not found" in detail:
        return True
    if not any(
        marker in detail
        for marker in (
            "message can't be edited",
            "message_id_invalid",
            "message identifier is not specified",
        )
    ):
        return False
    try:
        await bot.delete_message(chat_id, message_id)
        return True
    except Exception as delete_exc:
        log.warning("Не удалось удалить карточку %s для безопасной замены: %s", message_id, delete_exc)
        return False


async def _replace_callback_message(cq: CallbackQuery, text: str, reply_markup=None):
    """Edit the current card; avoid duplicates on transient Telegram failures."""
    try:
        await cq.message.edit_text(text, reply_markup=reply_markup)
        try:
            ui_cards.set_card(cq.message.chat.id, cq.from_user.id, cq.message.message_id)
        except OSError:
            log.exception("Не удалось сохранить ID карточки")
        return cq.message
    except Exception as exc:
        # Telegram возвращает эту ошибку, когда текст уже такой же. В этом
        # случае нельзя удалять карточку и создавать дубль.
        if "not modified" in str(exc).lower():
            return cq.message
        if not await _stale_card_allows_replacement(
            cq.message.chat.id, cq.message.message_id, exc
        ):
            log.warning("Не удалось обновить карточку %s: %s", cq.message.message_id, exc)
            return cq.message
        sent = await cq.message.answer(text, reply_markup=reply_markup)
        try:
            ui_cards.set_card(sent.chat.id, cq.from_user.id, sent.message_id)
        except OSError:
            log.exception("Не удалось сохранить ID новой карточки")
        return sent


async def _replace_message_card(message: Message, user_id: int, text: str, reply_markup=None):
    """Replace one known operation card, creating a new one only if old is proven stale."""
    try:
        await message.edit_text(text, reply_markup=reply_markup)
        try:
            ui_cards.set_card(message.chat.id, user_id, message.message_id)
        except OSError:
            log.exception("Не удалось сохранить ID карточки")
        return message
    except Exception as exc:
        if "not modified" in str(exc).lower():
            return message
        if not await _stale_card_allows_replacement(
            message.chat.id, message.message_id, exc
        ):
            log.warning("Не удалось обновить карточку %s: %s", message.message_id, exc)
            return message
        sent = await message.answer(text, reply_markup=reply_markup)
        try:
            ui_cards.set_card(sent.chat.id, user_id, sent.message_id)
        except OSError:
            log.exception("Не удалось сохранить ID новой карточки")
        return sent


async def _replace_user_card(message: Message, text: str, reply_markup=None) -> int:
    """Edit this user's registered bot card; create a replacement only if stale."""
    chat_id = message.chat.id
    user_id = message.from_user.id
    card_id = ui_cards.get(chat_id, user_id)
    if card_id:
        try:
            await bot.edit_message_text(
                text,
                chat_id=chat_id,
                message_id=card_id,
                reply_markup=reply_markup,
            )
            return card_id
        except Exception as exc:
            detail = str(exc).lower()
            if "not modified" in detail:
                return card_id
            if not await _stale_card_allows_replacement(chat_id, card_id, exc):
                log.warning("Не удалось обновить карточку %s: %s", card_id, exc)
                return card_id

    sent = await message.answer(text, reply_markup=reply_markup)
    try:
        ui_cards.set_card(chat_id, user_id, sent.message_id)
    except OSError:
        log.exception("Не удалось сохранить ID пользовательской карточки")
    return sent.message_id


async def _replace_user_card_with_document(
    message: Message, document: BufferedInputFile, caption: str, reply_markup=None
) -> int:
    """Make a delivered document the new main card and remove the old text card."""
    chat_id = message.chat.id
    user_id = message.from_user.id
    old_card_id = ui_cards.get(chat_id, user_id)
    sent = await message.answer_document(
        document,
        caption=caption,
        reply_markup=reply_markup,
    )
    if old_card_id and old_card_id != sent.message_id:
        try:
            await bot.delete_message(chat_id, old_card_id)
        except Exception:
            log.debug("Старая карточка не удалена при отправке файла", exc_info=True)
    try:
        ui_cards.set_card(chat_id, user_id, sent.message_id)
    except OSError:
        log.exception("Не удалось сохранить ID карточки с файлом")
    return sent.message_id


async def _show_start_card(message: Message, text: str, reply_markup) -> int:
    """Always send a visible /start card, even after the user clears chat history."""
    chat_id = message.chat.id
    user_id = message.from_user.id
    old_card_id = ui_cards.get(chat_id, user_id)
    # Telegram can still edit an old card that the user has deleted locally.
    # Sending first is the only reliable way to make /start visible again.
    sent = await message.answer(text, reply_markup=reply_markup)
    if old_card_id and old_card_id != sent.message_id:
        try:
            await bot.delete_message(chat_id, old_card_id)
        except Exception:
            log.debug("Старая карточка /start уже удалена или недоступна", exc_info=True)
    try:
        ui_cards.set_card(sent.chat.id, user_id, sent.message_id)
    except OSError:
        log.exception("Не удалось сохранить ID главной карточки")
    return sent.message_id

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
_PROMPTED_TEXT_ACTIONS = frozenset({"msgbox_spam", "prank_shout_tts"})
_PENDING_VERSION_INSTALLS: dict[int, dict[str, object]] = {}
_PENDING_VERSION_INSTALLS_LOCK = threading.Lock()

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
# Подтверждение перехода состояния отдельно от heartbeat-ов: heartbeat не
# должен сбрасывать таймер уведомлений и терять реальное возвращение online.
_STATUS_NOTICE_STATE: dict[str, bool] = {}
_STATUS_NOTICE_PENDING: dict[str, object] = {}
_STATUS_NOTICE_LOCK = threading.Lock()
_STATUS_NOTICE_DELAY_SEC = 8
_START_NOTIFY_LAST: dict[int, float] = {}
_LIFECYCLE_NOTICE_LAST: dict[tuple[str, str, str], float] = {}
_LIFECYCLE_NOTICE_LOCK = threading.Lock()
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


async def _notify_owner(text: str) -> None:
    try:
        await bot.send_message(int(ADMIN_ID), text)
    except Exception:
        log.exception("Не удалось отправить владельцу результат операции жизненного цикла")


def _schedule_owner_notice(text: str) -> bool:
    if LOOP is None or LOOP.is_closed():
        return False
    future = asyncio.run_coroutine_threadsafe(_notify_owner(text), LOOP)
    future.add_done_callback(
        lambda done: done.exception() if not done.cancelled() else None
    )
    return True


def _schedule_lifecycle_failure_notice(device_id: str, payload: dict) -> None:
    """Deferred failures must remain visible after a command waiter closes."""
    kind, state = payload.get("type"), payload.get("state")
    if not isinstance(kind, str) or not isinstance(state, str):
        return
    failures = {
        ("agent_update", "restart_failed"): False,
        ("agent_update", "guardian_restart_failed"): True,
        ("uninstall_agent", "uninstall_incomplete"): False,
    }
    expected_ok = failures.get((kind, state))
    if (kind, state) not in failures or payload.get("ok") is not expected_ok:
        return
    if bot_settings.is_blocked(device_id):
        return
    command_id = str(payload.get("id") or "")[:128]
    key = (device_id, command_id, state)
    now = time.monotonic()
    with _LIFECYCLE_NOTICE_LOCK:
        for old_key, seen in list(_LIFECYCLE_NOTICE_LAST.items()):
            if now - seen >= 600:
                _LIFECYCLE_NOTICE_LAST.pop(old_key, None)
        if key in _LIFECYCLE_NOTICE_LAST:
            return
        text = _lex_html(
            "lifecycle_failure_notice", device=target_label(device_id),
            state=state, text=str(payload.get("text") or state)[:2000],
        )
        if not _schedule_owner_notice(text):
            return
        if len(_LIFECYCLE_NOTICE_LAST) >= 128:
            oldest = min(_LIFECYCLE_NOTICE_LAST, key=_LIFECYCLE_NOTICE_LAST.get)
            _LIFECYCLE_NOTICE_LAST.pop(oldest, None)
        _LIFECYCLE_NOTICE_LAST[key] = now
    audit("agent_lifecycle_failure", device_id=device_id, command_id=command_id, state=state)

def _queue_device_status_notice(
    device_id: str,
    online: bool,
    name: str,
    *,
    delay: float | None = None,
    offline_reason: str | None = None,
) -> None:
    """Send one notice only after the device has held the new state steadily."""
    loop = LOOP
    if loop is None or loop.is_closed():
        log.warning("Статусное уведомление не поставлено в очередь: asyncio loop недоступен")
        return

    async def _send_if_stable() -> None:
        await asyncio.sleep(_STATUS_NOTICE_DELAY_SEC if delay is None else max(0.0, delay))
        current = devices.get(device_id) or {}
        if bool(current.get("online", False)) != online:
            return
        with _STATUS_NOTICE_LOCK:
            previous = _STATUS_NOTICE_STATE.get(device_id)
            if previous is None or previous == online:
                return
            _STATUS_NOTICE_STATE[device_id] = online

        quiet = bot_settings.quiet_active()
        enabled = bot_settings.get("notify_online" if online else "notify_offline", True)
        if not enabled or quiet:
            return
        event = "вернулся онлайн" if online else (offline_reason or "ушёл в оффлайн")
        icon = "🟢" if online else "🔴"
        _schedule_admin_notice(f"{icon} <b>{html.escape(name)}</b> {event}")

    future = asyncio.run_coroutine_threadsafe(_send_if_stable(), loop)
    with _STATUS_NOTICE_LOCK:
        previous_future = _STATUS_NOTICE_PENDING.get(device_id)
        _STATUS_NOTICE_PENDING[device_id] = future
    if previous_future is not None:
        previous_future.cancel()


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
        update_mode = payload.get("update_mode")
        info["update_mode"] = (
            update_mode
            if isinstance(update_mode, str) and update_mode in {"source", "frozen"}
            else "unknown"
        )
        info["release_update_ready"] = (
            info["update_mode"] == "source"
            and payload.get("release_update_ready") is True
        )
        if online:
            info["last_seen"] = time.time()
        is_new = device_id not in devices.all()
        devices.upsert(device_id, info)
        status_collector.submit(device_id, info)
        multi_status.submit(device_id, info)
        audit("device_online" if online else "device_offline",
              device_id=device_id, name=info["name"], is_new=is_new)
        with _STATUS_NOTICE_LOCK:
            _STATUS_NOTICE_STATE.setdefault(device_id, online if is_new else was_online)
            if is_new:
                _STATUS_NOTICE_STATE[device_id] = online
        if not is_new and online != was_online:
            _queue_device_status_notice(device_id, online, info["name"])
        if is_new and LOOP is not None and bot_settings.get("require_device_approval", True):
            kb = InlineKeyboardBuilder()
            kb.button(text=_lex("device_approve_button"), callback_data=f"devmg:allow:{device_id}", style="success")
            kb.button(text=_lex("device_block_button"), callback_data=f"devmg:block:{device_id}", style="danger")
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
        # X-MAP stores only bounded declarations, never arbitrary agent fields
        # or credentials. A declaration is not proof that hardware works.
        commands = payload.get("commands")
        features = payload.get("features")
        if isinstance(commands, list):
            clean_commands = sorted({
                str(command) for command in commands[:256]
                if isinstance(command, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", command)
            })
            clean_features = {
                str(key): value for key, value in (features or {}).items()
                if isinstance(key, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key)
                and isinstance(value, bool)
            } if isinstance(features, dict) else {}
            allowed_states = {
                "supported", "dependency_missing", "permission_unverified",
                "device_unverified", "unavailable", "approximate", "unknown",
            }
            raw_status = payload.get("feature_status")
            clean_status = {
                key: state for key, state in raw_status.items()
                if isinstance(key, str)
                and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key)
                and isinstance(state, str)
                and state in allowed_states
            } if isinstance(raw_status, dict) else {}
            devices.upsert(device_id, {
                "capabilities": {
                    "commands": clean_commands,
                    "features": clean_features,
                    "feature_status": clean_status,
                },
                "capabilities_seen": time.time(),
            })
        capabilities_collector.submit(device_id, payload)
    elif msg_type == "guardian" and payload.get("type") == "guardian":
        # Храним только последний подписанный ответ Guardian без лишних
        # данных. Это позволяет показать его состояние в карточке устройства.
        devices.upsert(device_id, {
            "guardian": {
                "version": str(payload.get("guardian") or payload.get("version") or "?"),
                "agent_running": bool(payload.get("agent_running")),
                "launchd_loaded": bool(payload.get("launchd_loaded")),
                "task_registered": bool(payload.get("guardian_task_registered", payload.get("launchd_loaded"))),
                "auto_restart": bool(payload.get("auto_restart")),
            },
            "guardian_last_seen": time.time(),
        })
        fun_text_collector.submit(device_id, payload)
    elif msg_type == "ack" and payload.get("type") == "ack":
        status = payload.get("status")
        action = payload.get("action")
        log.info(f"ACK from {device_id}: {action} -> {status}")
    elif ((msg_type in FUN_RESPONSE_TYPES and payload.get("type") == msg_type)
          or (msg_type == "output" and payload.get("type") in FUN_RESPONSE_TYPES)):
        # Волна новых команд: ответы типа file_get → файловый коллектор,
        # все остальные ответы → текстовый коллектор. Успешные file_put,
        # file_del и path_open часто содержат только ok/path и иначе теряются.
        if payload.get("type") == "file_get":
            file_collector.submit(device_id, payload)
        else:
            fun_text_collector.submit(device_id, payload)
            _schedule_lifecycle_failure_notice(device_id, payload)


async def _device_offline_watchdog() -> None:
    """Страховка на случай, если брокер не доставил MQTT Last Will."""
    while True:
        await asyncio.sleep(30)
        now = time.time()
        for device_id, info in devices.all().items():
            if not info.get("online"):
                continue
            last_seen = float(info.get("last_seen", 0) or 0)
            if not last_seen or now - last_seen < _OFFLINE_TIMEOUT_SEC:
                continue
            with _STATUS_NOTICE_LOCK:
                _STATUS_NOTICE_STATE.setdefault(device_id, True)
            devices.upsert(device_id, {"online": False, "standby": False})
            _queue_device_status_notice(
                device_id,
                False,
                str(info.get("name") or device_id),
                delay=0,
                offline_reason="не выходит на связь (heartbeat просрочен)",
            )
            audit("device_offline_watchdog", device_id=device_id, name=info.get("name", device_id))

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
            kb.button(text=_lex("admin_open_user_button"), callback_data=f"admin:user:{message.from_user.id}", style="primary")
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
        await _show_start_card(
            message,
            text_store.get_for_style("blocked", bot_settings.get("ui_style", "technical")),
            reply_markup=None,
        )
        return
    if role == Role.GUEST:
        intro_key = "start_guest"
    elif role == Role.USER:
        intro_key = "start_user"
    else:
        intro_key = "start_owner"
    intro = text_store.get_for_style(intro_key, bot_settings.get("ui_style", "technical"))
    start_text = (
        f"<b>XIDER {XIDER_BUILD_CODE}</b>\n"
        f"{html.escape(_lex('role_label', role=_role_label(role)))}\n"
        f"{html.escape(_lex('online_label', online=str(online_count), total=str(total_count)))}\n\n"
        f"{html.escape(intro)}"
        # Telegram отвечает `message is not modified`, если /start нажали
        # повторно до изменения текста. Невидимый nonce заставляет обновить
        # ту же карточку, не создавая новое сообщение в чате.
        f"\u2063{uuid.uuid4().hex[:8]}"
    )
    await _show_start_card(message, start_text, main_menu(message.from_user.id))


@router.message(AdminFilter(), Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    cur = await state.get_state()
    await state.clear()
    if cur:
        await _replace_user_card(
            message,
            "🚫 Ввод отменён.",
            reply_markup=back_to_device_kb() if SESSION.get("target") else main_menu(),
        )
    else:
        await _replace_user_card(message, "Нечего отменять.", reply_markup=main_menu())


@router.callback_query(ReadOnlyFilter(), F.data == "menu:about")
async def on_menu_about(cq: CallbackQuery):
    """XIDER handbook: short index, then individually readable chapters."""
    kb = InlineKeyboardBuilder()
    for slug, title, _ in info_book.CHAPTERS:
        label = _lex("about_chapter_button", title=title)
        kb.button(text=_limit_button_label(label), callback_data=f"about:chapter:{slug}", style="primary")
    kb.button(text=_nav("home"), callback_data="menu:main", style="primary")
    kb.adjust(1)
    await _replace_callback_message(
        cq,
        "<b>XIDER · книга проекта</b>\n"
        f"Сборка бота: <code>{html.escape(XIDER_BUILD_CODE)}</code>\n"
        "Выбери главу. Внутри — назначение частей, версии, ограничения и план TARPED.",
        reply_markup=kb.as_markup(),
    )
    await cq.answer()


@router.callback_query(ReadOnlyFilter(), F.data.startswith("about:chapter:"))
async def on_about_chapter(cq: CallbackQuery):
    slug = cq.data.rsplit(":", 1)[-1]
    try:
        index, title, body = info_book.chapter(slug)
    except KeyError:
        await cq.answer("Такой главы нет", show_alert=True)
        return
    kb = InlineKeyboardBuilder()
    if index:
        kb.button(text=_lex("about_prev_button"), callback_data=f"about:chapter:{info_book.CHAPTERS[index - 1][0]}", style="primary")
    if index + 1 < len(info_book.CHAPTERS):
        kb.button(text=_lex("about_next_button"), callback_data=f"about:chapter:{info_book.CHAPTERS[index + 1][0]}", style="primary")
    kb.button(text=_lex("about_toc_button"), callback_data="menu:about", style="primary")
    kb.adjust(2, 1)
    await _replace_callback_message(cq, f"<b>{html.escape(title)}</b>\n\n{html.escape(body)}", reply_markup=kb.as_markup())
    await cq.answer()


@router.message(AdminFilter(), Form.wait_url)
async def on_url_input(message: Message, state: FSMContext):
    form_data = await state.get_data()
    await state.clear()
    url = (message.text or "").strip()
    if not url:
        await _replace_user_card(
            message, _lex("empty_text"), reply_markup=back_to_device_kb()
        )
        return
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "https://" + url
    target = form_data.get("command_target")
    if not target:
        await _replace_user_card(
            message, _lex("target_required"), reply_markup=back_to_device_kb()
        )
        return
    if target == "all":
        await _replace_user_card(
            message, _lex("single_device_only"), reply_markup=back_to_device_kb()
        )
        return
    if not _pending_device_input_still_allowed(message, form_data, target):
        await _reject_revoked_device_input(message)
        return
    if publish("open_url", _target=target, url=url):
        await _replace_user_card(
            message,
            _lex_html("url_request_sent", device=target_label(target), url=url),
            reply_markup=back_to_device_kb(),
        )
    else:
        await _replace_user_card(
            message, _lex("mqtt_disconnected"), reply_markup=back_to_device_kb()
        )

@router.message(AdminFilter(), Form.wait_text)
async def on_text_input(message: Message, state: FSMContext):
    form_data = await state.get_data()
    await state.clear()
    text = (message.text or "").strip()
    if not text:
        await _replace_user_card(
            message, _lex("empty_text"), reply_markup=back_to_device_kb()
        )
        return
    target = form_data.get("command_target")
    if not target:
        await _replace_user_card(
            message, _lex("target_required"), reply_markup=back_to_device_kb()
        )
        return
    if target == "all":
        await _replace_user_card(
            message, _lex("single_device_only"), reply_markup=back_to_device_kb()
        )
        return
    if not _pending_device_input_still_allowed(message, form_data, target):
        await _reject_revoked_device_input(message)
        return
    if publish("notify", _target=target, text=text):
        await _replace_user_card(
            message,
            _lex_html("notify_text_request_sent", device=target_label(target)),
            reply_markup=back_to_device_kb(),
        )
    else:
        await _replace_user_card(
            message, _lex("mqtt_disconnected"), reply_markup=back_to_device_kb()
        )

@router.message(AdminFilter(), Form.wait_sound)
async def on_sound_input(message: Message, state: FSMContext):
    form_data = await state.get_data()
    await state.clear()
    text = (message.text or "").strip()
    if not text:
        await _replace_user_card(
            message, _lex("empty_text"), reply_markup=back_to_device_kb()
        )
        return
    target = form_data.get("command_target")
    if not target:
        await _replace_user_card(
            message, _lex("target_required"), reply_markup=back_to_device_kb()
        )
        return
    if target == "all":
        await _replace_user_card(
            message, _lex("single_device_only"), reply_markup=back_to_device_kb()
        )
        return
    if not _pending_device_input_still_allowed(message, form_data, target):
        await _reject_revoked_device_input(message)
        return
    if publish("sound", _target=target, text=text):
        await _replace_user_card(
            message,
            _lex_html("sound_request_sent", device=target_label(target)),
            reply_markup=back_to_device_kb(),
        )
    else:
        await _replace_user_card(
            message, _lex("mqtt_disconnected"), reply_markup=back_to_device_kb()
        )

# ---------- XIDER 3: доступ, администрирование и серверная ----------

@router.callback_query(ReadOnlyFilter(), F.data == "menu:server")
async def on_menu_server(cq: CallbackQuery):
    await cq.message.edit_text(
        server_overview_text(
            approval=bool(bot_settings.get("require_device_approval", True)),
            connected=transport.connected.is_set(),
        ),
        reply_markup=server_menu(cq.from_user.id),
    )
    await cq.answer()


@router.callback_query(OwnerFilter(), F.data == "server:approval")
async def on_server_approval(cq: CallbackQuery):
    value = bot_settings.toggle("require_device_approval")
    access_store.append_audit("device_approval_policy", actor_id=cq.from_user.id, detail=str(value))
    await cq.message.edit_text(
        server_overview_text(approval=bool(value), connected=transport.connected.is_set()),
        reply_markup=server_menu(cq.from_user.id),
    )
    await cq.answer(_lex("server_approval_on_answer" if value else "server_approval_off_answer"))


@router.callback_query(OwnerFilter(), F.data.in_({"server:restart", "server:update", "server:rollback"}))
async def on_server_dangerous_request(cq: CallbackQuery):
    action = cq.data.split(":", 1)[1]
    await cq.message.edit_text(
        server_confirmation_text(action),
        reply_markup=server_confirm_menu(action),
    )
    await cq.answer()


@router.callback_query(OwnerFilter(), F.data == "server:status")
async def on_server_status(cq: CallbackQuery):
    result = await asyncio.to_thread(server_ops.status)
    access_store.append_audit("server_status", actor_id=cq.from_user.id, detail=f"ok={result.ok}")
    await cq.message.edit_text(
        f"<b>{html.escape(_lex('server_status_title'))}</b>\n<pre>{html.escape(result.text)}</pre>",
        reply_markup=server_menu(cq.from_user.id),
    )
    await cq.answer(_lex("server_done" if result.ok else "server_failed"), show_alert=not result.ok)


@router.callback_query(OwnerFilter(), F.data == "server:logs")
async def on_server_logs(cq: CallbackQuery):
    result = await asyncio.to_thread(server_ops.logs, 45)
    access_store.append_audit("server_logs", actor_id=cq.from_user.id, detail=f"ok={result.ok}")
    await cq.message.edit_text(
        f"<b>{html.escape(_lex('server_logs_title'))}</b>\n<pre>{html.escape(result.text[-3600:])}</pre>",
        reply_markup=server_menu(cq.from_user.id),
    )
    await cq.answer(_lex("server_done" if result.ok else "server_failed"), show_alert=not result.ok)


@router.callback_query(OwnerFilter(), F.data == "server:metrics")
async def on_server_metrics(cq: CallbackQuery):
    snapshot = await asyncio.to_thread(server_ops.metrics)
    access_store.append_audit("server_metrics", actor_id=cq.from_user.id, detail="snapshot")
    await cq.message.edit_text(
        server_metrics_text(snapshot),
        reply_markup=server_menu(cq.from_user.id),
    )
    await cq.answer(_lex("server_done"))


@router.callback_query(OwnerFilter(), F.data == "server:specs")
async def on_server_specs(cq: CallbackQuery):
    result = await asyncio.to_thread(server_ops.specs)
    access_store.append_audit("server_specs", actor_id=cq.from_user.id, detail=f"ok={result.ok}")
    await cq.message.edit_text(
        f"<b>{html.escape(_lex('server_specs_title'))}</b>\n<pre>{html.escape(result.text[-3600:])}</pre>",
        reply_markup=server_menu(cq.from_user.id),
    )
    await cq.answer(_lex("server_done" if result.ok else "server_failed"), show_alert=not result.ok)


@router.callback_query(OwnerFilter(), F.data == "server:chart")
async def on_server_chart(cq: CallbackQuery):
    snapshot = await asyncio.to_thread(server_ops.metrics)
    image = await asyncio.to_thread(server_ops.render_metrics_chart, snapshot)
    access_store.append_audit("server_chart", actor_id=cq.from_user.id, detail="snapshot")
    await cq.message.edit_text(
        f"<b>{html.escape(_lex('server_chart_title'))}</b>\n"
        f"{html.escape(_lex('server_metrics_hint'))}",
        reply_markup=server_menu(cq.from_user.id),
    )
    await cq.message.answer_photo(
        BufferedInputFile(image, filename="xider-server-load.png"),
        caption=_lex("server_chart_caption"),
    )
    await cq.answer(_lex("server_done"))


@router.callback_query(OwnerFilter(), F.data == "server:terminal")
async def on_server_terminal(cq: CallbackQuery, state: FSMContext):
    await state.set_state(Form.wait_server_command)
    await cq.message.edit_text(
        server_terminal_text(),
        reply_markup=server_menu(cq.from_user.id),
    )
    await cq.answer()


@router.message(OwnerFilter(), Form.wait_server_command)
async def on_server_terminal_command(message: Message, state: FSMContext):
    await state.clear()
    command = (message.text or "").strip()
    if not command:
        await _replace_user_card(
            message, _lex("generic_empty_command"),
            reply_markup=server_menu(message.from_user.id),
        )
        return
    result = await asyncio.to_thread(server_ops.run_terminal, command)
    access_store.append_audit(
        "server_terminal", actor_id=message.from_user.id,
        detail=f"command={command[:32]!r}; ok={result.ok}",
    )
    await _replace_user_card(
        message,
        f"<b>{html.escape(_lex('server_terminal_result'))}</b>\n"
        f"<pre>{html.escape(result.text[-3600:])}</pre>",
        reply_markup=server_menu(message.from_user.id),
    )


@router.callback_query(OwnerFilter(), F.data.startswith("server_confirm:"))
async def on_server_confirm(cq: CallbackQuery):
    action = cq.data.split(":", 1)[1]
    operations = {"restart": server_ops.restart, "update": server_ops.update, "rollback": server_ops.rollback}
    operation = operations.get(action)
    if operation is None:
        await cq.answer(_lex("server_unknown_action"), show_alert=True)
        return
    await cq.message.edit_text(_lex("server_operation_pending"))
    result = await asyncio.to_thread(operation)
    access_store.append_audit("server_operation", actor_id=cq.from_user.id, detail=f"action={action}; ok={result.ok}; code={result.code}")
    await cq.message.edit_text(
        f"<b>{html.escape(_lex('server_operation_title', action=_lex(f'server_action_short_{action}')))}</b>\n"
        f"{html.escape(_lex('server_result_line', state=_lex('server_result_success' if result.ok else 'server_result_error')))}\n"
        f"<pre>{html.escape(result.text[-3500:])}</pre>",
        reply_markup=server_menu(cq.from_user.id),
    )
    await cq.answer(_lex("server_done" if result.ok else "server_failed"), show_alert=not result.ok)


@router.callback_query(ReadOnlyFilter(), F.data == "menu:guest_devices")
async def on_guest_devices(cq: CallbackQuery):
    devs = devices.all()
    online = sum(1 for info in devs.values() if _status_dot(info) in ("🟢", "🟡"))
    await cq.message.edit_text(
        _lex("guest_devices_overview", online=str(online), total=str(len(devs))),
        reply_markup=guest_devices_menu(),
    )
    await cq.answer()


@router.callback_query(ReadOnlyFilter(), F.data == "guest:readonly")
async def on_guest_readonly(cq: CallbackQuery):
    await cq.answer("Режим просмотра: эта кнопка недоступна гостю.", show_alert=True)


@router.callback_query(OwnerFilter(), F.data == "menu:admin")
async def on_menu_admin(cq: CallbackQuery):
    await cq.message.edit_text(
        f"<b>{html.escape(_lex('admin_title'))}</b>\n"
        f"{html.escape(_lex('admin_intro'))}",
        reply_markup=admin_menu(),
    )
    await cq.answer()


@router.callback_query(OwnerFilter(), F.data == "admin:users")
async def on_admin_users(cq: CallbackQuery):
    users = access_store.list_users()
    await cq.message.edit_text(
        f"<b>{html.escape(_nav('admin_users'))}</b>\n"
        f"{html.escape(_lex('users_intro', total=str(len(users))))}",
        reply_markup=admin_users_menu(),
    )
    await cq.answer()


@router.callback_query(OwnerFilter(), F.data == "admin:texts")
async def on_admin_texts(cq: CallbackQuery):
    mode = xlex.normalize_style(bot_settings.get("ui_style", "technical"))
    await cq.message.edit_text(
        f"<b>X-LEX · {html.escape(xlex.STYLE_NAMES[mode])}</b>\n"
        f"{html.escape(_lex('texts_intro'))}",
        reply_markup=admin_texts_menu(),
    )
    await cq.answer()


@router.callback_query(OwnerFilter(), F.data.startswith("admin:text:"))
async def on_admin_text_edit(cq: CallbackQuery, state: FSMContext):
    key = cq.data.split(":", 2)[2]
    if key not in TEXT_LABELS:
        await cq.answer(_lex("admin_invalid_data"), show_alert=True)
        return
    await state.set_state(Form.wait_bot_text)
    await state.update_data(bot_text_key=key)
    await _replace_callback_message(
        cq,
        _lex_html(
            "text_edit_prompt",
            label=TEXT_LABELS[key],
            current=text_store.get(key),
        ),
        reply_markup=admin_texts_menu(),
    )
    await cq.answer()


@router.message(OwnerFilter(), Form.wait_bot_text)
async def on_admin_text_value(message: Message, state: FSMContext):
    data = await state.get_data()
    key = str(data.get("bot_text_key") or "")
    await state.clear()
    if key not in TEXT_LABELS:
        await _replace_user_card(message, _lex("text_edit_expired"), reply_markup=admin_menu())
        return
    try:
        text_store.set_text(key, message.text or "")
    except (KeyError, ValueError) as exc:
        log.warning("Не удалось сохранить пользовательский текст %r: %s", key, exc)
        await _replace_user_card(message, _lex("text_save_failed"), reply_markup=admin_texts_menu())
        return
    access_store.append_audit("bot_text_updated", actor_id=message.from_user.id, detail=key)
    await _replace_user_card(message, _lex("text_saved"), reply_markup=admin_texts_menu())


async def _render_admin_user_card(cq: CallbackQuery, user_id: int) -> bool:
    record = access_store.get_user(user_id)
    if not record:
        return False
    role = access_store.get_role(user_id, ADMIN_ID)
    perms = record.get("permissions") or {}
    device_count = len(perms.get("devices") or [])
    button_count = len(perms.get("callbacks") or [])
    await _replace_callback_message(
        cq,
        _lex_html(
            "admin_user_profile",
            name=_user_label(record),
            user_id=user_id,
            role=_role_label(role),
            devices=device_count,
            buttons=button_count,
        ),
        reply_markup=admin_user_menu(user_id),
    )
    return True


@router.callback_query(OwnerFilter(), F.data.startswith("admin:user:"))
async def on_admin_user(cq: CallbackQuery):
    raw = cq.data.rsplit(":", 1)[-1]
    if not raw.isdigit():
        await cq.answer(_lex("admin_invalid_user"), show_alert=True)
        return
    user_id = int(raw)
    if not await _render_admin_user_card(cq, user_id):
        await cq.answer(_lex("admin_user_not_found"), show_alert=True)
        return
    await cq.answer()


@router.callback_query(OwnerFilter(), F.data.startswith("admin:role:"))
async def on_admin_role(cq: CallbackQuery):
    parts = cq.data.split(":")
    if len(parts) != 4 or not parts[2].isdigit():
        await cq.answer(_lex("admin_invalid_data"), show_alert=True)
        return
    user_id, role = int(parts[2]), parts[3]
    if not access_store.set_role(cq.from_user.id, user_id, role, ADMIN_ID):
        await cq.answer(_lex("owner_immutable"), show_alert=True)
        return
    await cq.answer(_lex("admin_role_changed", role=_role_label(role)))
    if not await _render_admin_user_card(cq, user_id):
        await _replace_callback_message(cq, _lex("admin_user_not_found"), reply_markup=admin_menu())


@router.callback_query(OwnerFilter(), F.data.startswith("admin:block:"))
async def on_admin_block(cq: CallbackQuery):
    raw = cq.data.rsplit(":", 1)[-1]
    if not raw.isdigit():
        await cq.answer(_lex("admin_invalid_user"), show_alert=True)
        return
    user_id = int(raw)
    blocked = access_store.get_role(user_id, ADMIN_ID) != Role.BLOCKED
    if not access_store.set_blocked(cq.from_user.id, user_id, blocked, ADMIN_ID):
        await cq.answer(_lex("owner_immutable"), show_alert=True)
        return
    await cq.answer(_lex("admin_user_blocked" if blocked else "admin_user_unblocked"))
    if not await _render_admin_user_card(cq, user_id):
        await _replace_callback_message(cq, _lex("admin_user_not_found"), reply_markup=admin_menu())


@router.callback_query(OwnerFilter(), F.data.startswith("admin:devices:"))
async def on_admin_devices(cq: CallbackQuery):
    raw = cq.data.rsplit(":", 1)[-1]
    if not raw.isdigit():
        await cq.answer(_lex("admin_invalid_user"), show_alert=True)
        return
    await cq.message.edit_text(
        _lex("admin_device_access_intro"),
        reply_markup=admin_devices_menu(int(raw)),
    )
    await cq.answer()


@router.callback_query(OwnerFilter(), F.data.startswith("admin:device:"))
async def on_admin_device_toggle(cq: CallbackQuery):
    parts = cq.data.split(":", 3)
    if len(parts) != 4 or not parts[2].isdigit():
        await cq.answer(_lex("admin_invalid_data"), show_alert=True)
        return
    user_id, device_id = int(parts[2]), parts[3]
    if device_id not in devices.all():
        await cq.answer(_lex("device_unavailable"), show_alert=True)
        return
    enabled = access_store.toggle_device(cq.from_user.id, user_id, device_id, ADMIN_ID)
    await cq.answer(_lex(
        "admin_device_access_changed",
        device=target_label(device_id),
        action="выдан" if enabled else "отозван",
    ))
    await cq.message.edit_text(
        _lex("admin_device_access_intro"),
        reply_markup=admin_devices_menu(user_id),
    )


@router.callback_query(OwnerFilter(), F.data.startswith("admin:perms:"))
async def on_admin_permissions(cq: CallbackQuery):
    raw = cq.data.rsplit(":", 1)[-1]
    if not raw.isdigit():
        await cq.answer(_lex("admin_invalid_user"), show_alert=True)
        return
    await cq.message.edit_text(
        _lex("admin_permissions_intro"),
        reply_markup=admin_permissions_menu(int(raw)),
    )
    await cq.answer()


@router.callback_query(OwnerFilter(), F.data.startswith("admin:perm:"))
async def on_admin_permission_toggle(cq: CallbackQuery):
    parts = cq.data.split(":", 3)
    if len(parts) != 4 or not parts[2].isdigit():
        await cq.answer(_lex("admin_invalid_data"), show_alert=True)
        return
    user_id, encoded = int(parts[2]), parts[3]
    callback = next((item for item in USER_PERMISSION_CHOICES if item.replace(":", "_") == encoded), None)
    if not callback:
        await cq.answer(_lex("admin_invalid_data"), show_alert=True)
        return
    enabled = access_store.toggle_callback(cq.from_user.id, user_id, callback, ADMIN_ID)
    await cq.answer(_lex(
        "admin_permission_changed",
        permission=callback,
        action="выдано" if enabled else "отозвано",
    ))
    await cq.message.edit_text(
        _lex("admin_permissions_intro"),
        reply_markup=admin_permissions_menu(user_id),
    )


@router.callback_query(OwnerFilter(), F.data.startswith("admin:message:"))
async def on_admin_message(cq: CallbackQuery, state: FSMContext):
    raw = cq.data.rsplit(":", 1)[-1]
    if not raw.isdigit() or not access_store.get_user(int(raw)):
        await cq.answer(_lex("admin_user_not_found"), show_alert=True)
        return
    await state.set_state(Form.wait_admin_message)
    await state.update_data(admin_message_user=int(raw))
    await _replace_callback_message(cq, _lex("admin_message_prompt"))
    await cq.answer()


@router.message(OwnerFilter(), Form.wait_admin_message)
async def on_admin_message_text(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    target_id = int(data.get("admin_message_user") or 0)
    text = (message.text or "").strip()
    if not target_id or not text:
        await _replace_user_card(message, _lex("admin_message_empty"), reply_markup=admin_menu())
        return
    try:
        await bot.send_message(target_id, html.escape(text))
    except Exception:
        log.exception("Не удалось отправить администраторское сообщение")
        await _replace_user_card(message, _lex("admin_message_failed"), reply_markup=admin_menu())
        return
    access_store.append_audit("admin_message_sent", actor_id=message.from_user.id, target_id=target_id, detail=text)
    await _replace_user_card(message, _lex("admin_message_sent"), reply_markup=admin_menu())


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
    kb = InlineKeyboardBuilder()
    current = xlex.normalize_style(bot_settings.get("ui_style", "technical"))
    for style in xlex.STYLES:
        prefix = "● " if style == current else "○ "
        kb.button(text=_limit_button_label(prefix + xlex.STYLE_NAMES[style]), callback_data=f"admin:style:set:{style}", style="success" if style == current else "primary")
    kb.button(text=_nav("back"), callback_data="menu:admin", style="primary")
    kb.adjust(1)
    await cq.message.edit_text(
        _lex("style_select_intro"),
        reply_markup=kb.as_markup(),
    )
    await cq.answer()


@router.callback_query(OwnerFilter(), F.data.startswith("admin:style:set:"))
async def on_admin_style_set(cq: CallbackQuery):
    style = cq.data.rsplit(":", 1)[-1]
    if style not in xlex.STYLES:
        await cq.answer("Неизвестный стиль", show_alert=True)
        return
    bot_settings.set_key("ui_style", style)
    access_store.append_audit("ui_style_set", actor_id=cq.from_user.id, detail=style)
    await cq.message.edit_text(
        f"<b>X-LEX · {html.escape(xlex.STYLE_NAMES[style])}</b>\n"
        f"{html.escape(text_store.get_for_style('start_owner', style))}\n\n"
        + _lex("style_enabled_notice"),
        reply_markup=main_menu(cq.from_user.id),
    )
    await cq.answer("Стиль выбран")

# =====================================================================
#  Хендлеры: навигация
# =====================================================================

@router.callback_query(AdminFilter(), F.data == "noop")
async def on_noop(cq: CallbackQuery):
    await cq.answer()


# ---------- Управление устройствами: переименование / удаление ----------

@router.callback_query(OwnerFilter(), F.data.startswith("devmg:rename:"))
async def on_devmg_rename(cq: CallbackQuery, state: FSMContext):
    device_id = cq.data.split(":", 2)[2]
    if device_id not in devices.all():
        await cq.answer(_lex("device_unavailable"), show_alert=True)
        return
    await state.set_state(Form.wait_rename)
    await state.update_data(rename_device=device_id)
    await _replace_callback_message(
        cq,
        _lex_html(
            "device_rename_prompt",
            device=target_label(device_id),
            device_id=device_id,
        ),
        reply_markup=back_to_device_kb(),
    )
    await cq.answer()


@router.message(OwnerFilter(), Form.wait_rename)
async def on_rename_input(message: Message, state: FSMContext):
    data = await state.get_data()
    device_id = data.get("rename_device")
    name = (message.text or "").strip()
    await state.clear()
    if not device_id:
        await _replace_user_card(message, _lex("device_rename_unselected"), reply_markup=main_menu())
        return
    if not name:
        await _replace_user_card(message, _lex("device_name_empty"), reply_markup=back_to_device_kb())
        return
    if devices.rename(device_id, name):
        audit("device_rename", device_id=device_id, name=name)
        await _replace_user_card(
            message,
            _lex_html("device_renamed_result", device=name),
            reply_markup=device_menu(device_id),
        )
    else:
        await _replace_user_card(message, _lex("device_unavailable"), reply_markup=main_menu())


@router.callback_query(OwnerFilter(), F.data.startswith("devmg:delete:"))
async def on_devmg_delete(cq: CallbackQuery):
    device_id = cq.data.split(":", 2)[2]
    kb = InlineKeyboardBuilder()
    kb.button(text=_lex("confirm_delete_device_button"), callback_data=f"devmg:delok:{device_id}", style="danger")
    kb.button(text=_nav("cancel"), callback_data="back:device", style="primary")
    kb.adjust(1)
    await _replace_callback_message(
        cq,
        _lex_html("device_delete_confirm", device=target_label(device_id)),
        reply_markup=kb.as_markup(),
    )
    await cq.answer()


@router.callback_query(OwnerFilter(), F.data.startswith("devmg:delok:"))
async def on_devmg_delete_ok(cq: CallbackQuery):
    device_id = cq.data.split(":", 2)[2]
    name = target_label(device_id)
    if devices.remove(device_id):
        access_store.revoke_devices([device_id], actor_id=cq.from_user.id)
        audit("device_remove", device_id=device_id)
        if SESSION.get("target") == device_id:
            SESSION["target"] = None
        await _replace_callback_message(
            cq,
            _lex_html("device_delete_result", device=name),
            reply_markup=devices_menu(),
        )
    else:
        await _replace_callback_message(cq, _lex("device_unavailable"), reply_markup=devices_menu())
    await cq.answer()


@router.callback_query(OwnerFilter(), F.data.startswith("devmg:uninstall:"))
async def on_devmg_uninstall(cq: CallbackQuery):
    device_id = cq.data.split(":", 2)[2]
    kb = InlineKeyboardBuilder()
    kb.button(text=_lex("confirm_uninstall_agent_button"), callback_data=f"devmg:uninstok:{device_id}", style="danger")
    kb.button(text=_nav("cancel"), callback_data="back:device", style="primary")
    kb.adjust(1)
    await _replace_callback_message(
        cq,
        _lex_html("agent_uninstall_confirm", device=target_label(device_id)),
        reply_markup=kb.as_markup(),
    )
    await cq.answer()


@router.callback_query(OwnerFilter(), F.data.startswith("devmg:uninstok:"))
async def on_devmg_uninstall_ok(cq: CallbackQuery):
    device_id = cq.data.split(":", 2)[2]
    name = target_label(device_id)
    sent, command_id = publish_tracked("uninstall_agent", _target=device_id)
    if not sent:
        await cq.answer(_lex("mqtt_disconnected"), show_alert=True)
        await _replace_callback_message(cq, _lex("mqtt_disconnected"), reply_markup=devices_menu())
        return

    await cq.answer(_lex("agent_uninstall_pending"))
    result = await fun_text_collector.wait_for(
        device_id,
        "uninstall_agent",
        timeout=15.0,
        command_id=command_id,
    )
    if result is None:
        audit("agent_uninstall_unconfirmed", device_id=device_id)
        await _replace_callback_message(
            cq,
            _lex_html("agent_uninstall_timeout", device=name),
            reply_markup=devices_menu(),
        )
        return

    result_text = str(result.get("text") or _lex("agent_uninstall_no_result"))
    if result.get("ok") is True and result.get("state") in {
        "uninstall_prepared", "uninstall_pending", "restarting",
    }:
        # A prepared reply precedes the worker's delayed launchd unload. Keep
        # its identity/permissions so an incomplete outcome remains visible.
        audit("agent_uninstall_prepared", device_id=device_id)
    elif result.get("ok") is True:
        devices.remove(device_id)
        access_store.revoke_devices([device_id], actor_id=cq.from_user.id)
        if SESSION.get("target") == device_id:
            SESSION["target"] = None
        audit("agent_uninstall", device_id=device_id)
    else:
        audit("agent_uninstall_failed", device_id=device_id)

    await _replace_callback_message(
        cq,
        _lex_html("agent_uninstall_result", device=name, text=result_text),
        reply_markup=devices_menu(),
    )


# ---------- Регистрация устройств: подтвердить / заблокировать ----------

@router.callback_query(OwnerFilter(), F.data.startswith("devmg:allow:"))
async def on_devmg_allow(cq: CallbackQuery):
    device_id = cq.data.split(":", 2)[2]
    audit("device_approved", device_id=device_id)
    await _replace_callback_message(
        cq,
        _lex_html("device_confirmed", device=target_label(device_id)),
        reply_markup=main_menu(),
    )
    await cq.answer()


@router.callback_query(OwnerFilter(), F.data.startswith("devmg:block:"))
async def on_devmg_block(cq: CallbackQuery):
    device_id = cq.data.split(":", 2)[2]
    kb = InlineKeyboardBuilder()
    kb.button(text=_lex("confirm_block_device_button"), callback_data=f"devmg:blockok:{device_id}", style="danger")
    kb.button(text=_nav("cancel"), callback_data="back:device", style="primary")
    kb.adjust(1)
    await _replace_callback_message(
        cq,
        _lex_html("device_block_confirm", device=target_label(device_id)),
        reply_markup=kb.as_markup(),
    )
    await cq.answer()


@router.callback_query(OwnerFilter(), F.data.startswith("devmg:blockok:"))
async def on_devmg_blockok(cq: CallbackQuery):
    device_id = cq.data.split(":", 2)[2]
    name = target_label(device_id)
    bot_settings.block(device_id)
    devices.remove(device_id)
    access_store.revoke_devices([device_id], actor_id=cq.from_user.id)
    if SESSION.get("target") == device_id:
        SESSION["target"] = None
    audit("device_blocked", device_id=device_id)
    await _replace_callback_message(
        cq,
        _lex_html("device_blocked_notice", device=name),
        reply_markup=devices_menu(),
    )
    await cq.answer()


@router.callback_query(OwnerFilter(), F.data.startswith("devmg:unblock:"))
async def on_devmg_unblock(cq: CallbackQuery):
    device_id = cq.data.split(":", 2)[2]
    if bot_settings.unblock(device_id):
        audit("device_unblocked", device_id=device_id)
        await _replace_callback_message(
            cq,
            _lex_html("device_unblocked_notice", device_id=device_id),
            reply_markup=blocked_menu(),
        )
    else:
        await _replace_callback_message(cq, _lex("device_not_blocked"), reply_markup=blocked_menu())
    await cq.answer()


@router.callback_query(OwnerFilter(), F.data == "devmg:blocked")
async def on_devmg_blocked(cq: CallbackQuery):
    s = bot_settings.all_settings()
    blocked = s.get("blocked_ids") or []
    if not blocked:
        text = _lex("devices_blocked_empty")
    else:
        text = _lex("devices_blocked_heading") + "\n" + "\n".join(f"• <code>{html.escape(b)}</code>" for b in blocked)
    await _replace_callback_message(cq, text, reply_markup=blocked_menu())
    await cq.answer()


@router.callback_query(OwnerFilter(), F.data == "devmg:clear_all")
async def on_devmg_clear_all(cq: CallbackQuery):
    kb = InlineKeyboardBuilder()
    kb.button(text=_lex("confirm_clear_devices_button"), callback_data="devmg:clear_all_confirm", style="danger")
    kb.button(text=_nav("cancel"), callback_data="menu:devices", style="primary")
    kb.adjust(1, 1)
    await _replace_callback_message(
        cq,
        _lex("devices_clear_confirm"),
        reply_markup=kb.as_markup(),
    )
    await cq.answer()


@router.callback_query(OwnerFilter(), F.data == "devmg:clear_all_confirm")
async def on_devmg_clear_all_confirm(cq: CallbackQuery):
    access_store.revoke_devices(devices.all().keys(), actor_id=cq.from_user.id)
    devices.clear()
    SESSION["target"] = None
    await _replace_callback_message(cq, _lex("devices_cleared"), reply_markup=devices_menu())
    await cq.answer()


@router.callback_query(OwnerFilter(), F.data == "devmg:manualadd")
async def on_devmg_manualadd(cq: CallbackQuery, state: FSMContext):
    await state.set_state(Form.wait_manual_add)
    await _replace_callback_message(
        cq,
        _lex("manual_device_id_prompt"),
        reply_markup=back_to_device_kb(),
    )
    await cq.answer()


@router.message(OwnerFilter(), Form.wait_manual_add)
async def on_manualadd_input(message: Message, state: FSMContext):
    await state.clear()
    device_id = (message.text or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{12}", device_id):
        await _replace_user_card(
            message,
            _lex("device_id_invalid"),
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
        await _replace_user_card(
            message,
            _lex_html("device_added_manual", device_id=device_id),
            reply_markup=devices_menu(),
        )
    else:
        await _replace_user_card(
            message,
            _lex("device_already_present"),
            reply_markup=device_menu(device_id),
        )

@router.callback_query(ReadOnlyFilter(), F.data == "menu:main")
async def on_menu_main(cq: CallbackQuery):
    SESSION["target"] = None
    await cq.message.edit_text(html.escape(_lex("main_title")), reply_markup=main_menu(cq.from_user.id))
    await cq.answer()

@router.callback_query(AdminFilter(), F.data == "menu:devices")
async def on_menu_devices(cq: CallbackQuery):
    lines = []
    visible_devices = _visible_device_items(cq.from_user.id)
    for device_id, info in sorted(visible_devices.items()):
        state = "🟢 онлайн" if _status_dot(info) == "🟢" else "⚪ офлайн"
        os_name = html.escape(str(info.get("os", "?")))
        name = html.escape(str(info.get("name", device_id)))
        lines.append(
            f"{state} <b>{name}</b> (<code>{html.escape(str(device_id))}</code>)\n"
            f"    ОС: {os_name}"
        )
    if lines:
        text = "💻 <b>Устройства</b>\n\n" + "\n".join(lines)
    else:
        if get_user_role(cq.from_user.id) == Role.OWNER:
            text = "💻 Пока нет ни одного устройства.\nЗапустите клиент — оно появится само."
        else:
            text = "💻 Вам пока не выдан доступ к устройствам. Обратитесь к владельцу."
    await _replace_callback_message(cq, text, reply_markup=devices_menu(cq.from_user.id))
    await cq.answer()

@router.callback_query(OwnerFilter(), F.data == "menu:target")
async def on_menu_target(cq: CallbackQuery):
    if not devices.all():
        kb = InlineKeyboardBuilder()
        kb.button(text=_nav("home"), callback_data="menu:main", style="primary")
        await cq.message.edit_text(
            _lex("no_devices_yet"),
            reply_markup=kb.as_markup(),
        )
        await cq.answer()
        return
    await cq.message.edit_text(_lex("device_required"), reply_markup=devices_menu())
    await cq.answer()

@router.callback_query(AdminFilter(), F.data.startswith("dev:"))
async def on_dev(cq: CallbackQuery):
    device_id = cq.data.split(":", 1)[1]
    if not device_id or devices.get(device_id) is None:
        await cq.answer("Устройство уже недоступно.", show_alert=True)
        return
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
    if (
        target
        and (
            devices.get(target) is None
            or (
                get_user_role(cq.from_user.id) == Role.USER
                and target not in _visible_device_items(cq.from_user.id)
            )
        )
    ):
        SESSION["target"] = None
        target = None
    if not target:
        await cq.message.edit_text(f"<b>{html.escape(_lex('main_title'))}</b>", reply_markup=main_menu())
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
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    if target == "all":
        await cq.answer(_lex("single_device_only"), show_alert=True)
        return
    await _start_authorized_input(state, Form.wait_url, cq.data, target, command_target=target)
    await _replace_callback_message(
        cq,
        _lex("url_prompt"),
        reply_markup=back_to_device_kb(),
    )
    await cq.answer()

@router.callback_query(AdminFilter(), F.data == "cmd:text")
async def on_cmd_text(cq: CallbackQuery, state: FSMContext):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    if target == "all":
        await cq.answer(_lex("single_device_only"), show_alert=True)
        return
    await _start_authorized_input(state, Form.wait_text, cq.data, target, command_target=target)
    await _replace_callback_message(
        cq,
        _lex("notify_text_prompt"),
        reply_markup=back_to_device_kb(),
    )
    await cq.answer()

@router.callback_query(AdminFilter(), F.data == "cmd:sound")
async def on_cmd_sound(cq: CallbackQuery, state: FSMContext):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    if target == "all":
        await cq.answer(_lex("single_device_only"), show_alert=True)
        return
    await _start_authorized_input(state, Form.wait_sound, cq.data, target, command_target=target)
    await _replace_callback_message(
        cq,
        _lex("sound_prompt"),
        reply_markup=back_to_device_kb(),
    )
    await cq.answer()

@router.callback_query(AdminFilter(), F.data == "cmd:screenshot")
async def on_cmd_screenshot(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    if target == "all":
        await cq.answer(_lex("single_device_only"), show_alert=True)
        return
    await cq.answer("📸 Делаю скриншот...")
    if not await _callback_result_access_or_report(cq, target):
        return
    sent, command_id = publish_tracked("screenshot", _target=target)
    if not sent:
        await _replace_callback_message(
            cq,
            _lex("mqtt_disconnected"),
            reply_markup=back_to_device_kb(),
        )
        return
    result = await screenshot_collector.wait_for(target, "screenshot", 15.0, command_id)
    if not await _callback_result_access_or_report(cq, target):
        return
    if result is None:
        await _replace_callback_message(
            cq,
            "⏳ Скриншот не получен за 15 сек — устройство офлайн.",
            reply_markup=back_to_device_kb(),
        )
        return
    img_b64 = result.get("image")
    if not img_b64:
        agent_error = str(result.get("error") or "").strip()
        detail = f"\n<pre>{html.escape(agent_error[:500])}</pre>" if agent_error else ""
        await _replace_callback_message(
            cq,
            "⚠️ Устройство ответило, но скриншот не получен." + detail,
            reply_markup=back_to_device_kb(),
        )
        return
    try:
        img_bytes = base64.b64decode(img_b64)
        photo = BufferedInputFile(img_bytes, filename="screenshot.png")
        sent_photo = await bot.send_photo(
            cq.from_user.id,
            photo=photo,
            caption=f"📸 <b>{target_label(target)}</b>",
            reply_markup=back_to_device_kb(),
        )
        # Фото Telegram нельзя встроить в исходную текстовую карточку;
        # удаляем её после отправки, чтобы в чате не оставался дубль.
        try:
            await cq.message.delete()
        except Exception:
            pass
        try:
            ui_cards.set_card(sent_photo.chat.id, cq.from_user.id, sent_photo.message_id)
        except OSError:
            log.exception("Не удалось сохранить ID карточки скриншота")
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
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    if target == "all":
        await cq.answer(_lex("single_device_only"), show_alert=True)
        return
    await cq.answer("📷 Делаю снимок с вебки (может занять пару секунд)...")
    if not await _callback_result_access_or_report(cq, target):
        return
    sent, command_id = publish_tracked("webcam", _target=target)
    if not sent:
        await _replace_callback_message(
            cq,
            _lex("mqtt_disconnected"),
            reply_markup=back_to_device_kb(),
        )
        return
    result = await webcam_collector.wait_for(target, "webcam", 20.0, command_id)
    if not await _callback_result_access_or_report(cq, target):
        return
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
        sent_photo = await bot.send_photo(
            cq.from_user.id,
            photo=photo,
            caption=f"📷 <b>{target_label(target)}</b>",
            reply_markup=back_to_device_kb(),
        )
        try:
            await cq.message.delete()
        except Exception:
            pass
        try:
            ui_cards.set_card(sent_photo.chat.id, cq.from_user.id, sent_photo.message_id)
        except OSError:
            log.exception("Не удалось сохранить ID карточки веб-камеры")
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
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    if target == "all":
        await cq.answer(_lex("single_device_only"), show_alert=True)
        return
    await cq.answer("🔋 Запрашиваю батарею...")
    if not await _callback_result_access_or_report(cq, target):
        return
    sent, command_id = publish_tracked("battery", _target=target)
    if not sent:
        await _replace_callback_message(cq, _lex("mqtt_disconnected"), reply_markup=back_to_device_kb())
        return
    res = await battery_collector.wait_for(target, "battery", 15.0, command_id)
    if not await _callback_result_access_or_report(cq, target):
        return
    if not res:
        await _replace_callback_message(cq, _lex("device_no_response"), reply_markup=back_to_device_kb())
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
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    if target == "all":
        await cq.answer(_lex("single_device_only"), show_alert=True)
        return
    await cq.answer("🌐 Запрашиваю сеть...")
    if not await _callback_result_access_or_report(cq, target):
        return
    sent, command_id = publish_tracked("network", _target=target)
    if not sent:
        await _replace_callback_message(cq, _lex("mqtt_disconnected"), reply_markup=back_to_device_kb())
        return
    res = await network_collector.wait_for(target, "network", 15.0, command_id)
    if not await _callback_result_access_or_report(cq, target):
        return
    if not res:
        await _replace_callback_message(cq, _lex("device_no_response"), reply_markup=back_to_device_kb())
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
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    if target == "all":
        await cq.answer(_lex("single_device_only"), show_alert=True)
        return
    await cq.answer("🛠 Запрашиваю службы...")
    if not await _callback_result_access_or_report(cq, target):
        return
    sent, command_id = publish_tracked("services", _target=target)
    if not sent:
        await _replace_callback_message(cq, _lex("mqtt_disconnected"), reply_markup=back_to_device_kb())
        return
    res = await services_collector.wait_for(target, "services", 15.0, command_id)
    if not await _callback_result_access_or_report(cq, target):
        return
    if not res:
        await _replace_callback_message(cq, _lex("device_no_response"), reply_markup=back_to_device_kb())
        return
    tot = res.get("total", "?")
    run = res.get("running", "?")
    await _replace_callback_message(cq, f"🛠 <b>Службы:</b>\nВсего: {tot}\nЗапущено: {run}", reply_markup=back_to_device_kb())

@router.callback_query(AdminFilter(), F.data == "cmd:capabilities")
async def on_cmd_capabilities(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    if target == "all":
        await cq.answer(_lex("single_device_only"), show_alert=True)
        return
    await cq.answer("📊 Запрашиваю возможности...")
    if not await _callback_result_access_or_report(cq, target):
        return
    sent, command_id = publish_tracked("capabilities", _target=target)
    if not sent:
        await _replace_callback_message(cq, _lex("mqtt_disconnected"), reply_markup=back_to_device_kb())
        return
    res = await capabilities_collector.wait_for(target, "capabilities", 15.0, command_id)
    if not await _callback_result_access_or_report(cq, target):
        return
    if not res:
        await _replace_callback_message(cq, _lex("device_no_response"), reply_markup=back_to_device_kb())
        return
    await _replace_callback_message(cq, _format_capabilities(res), reply_markup=back_to_device_kb())


_CAPABILITY_LABELS = {
    "screenshot": "Снимок экрана",
    "webcam": "Камера",
    "microphone": "Микрофон",
    "geolocation": "Локация",
    "battery": "Батарея",
    "clipboard": "Буфер обмена",
    "shell": "Командная строка",
    "open_app": "Запуск приложений",
}
_CAPABILITY_STATES = {
    "supported": "базовая поддержка заявлена; live-проверки не было",
    "dependency_missing": "не найдена нужная библиотека",
    "permission_unverified": "разрешение ОС ещё не проверено",
    "device_unverified": "устройство или активный пользовательский сеанс не проверен",
    "unavailable": "не обнаружено или недоступно на этой машине",
    "approximate": "только приблизительно по публичному IP, не GPS",
    "unknown": "агент не смог определить состояние",
}


def _format_capabilities(result: dict) -> str:
    """Render bounded capability data without treating declarations as health checks."""
    def safe(value, limit=100):
        return html.escape(str(value or "?")[:limit], quote=False)

    lines = ["📊 <b>Проверка возможностей</b>"]
    lines.append(f"Устройство: <b>{safe(result.get('hostname'))}</b>")
    lines.append(f"ОС: {safe(result.get('platform'))} · агент: {safe(result.get('version'), 40)}")

    raw_statuses = result.get("feature_status")
    raw_features = result.get("features")
    statuses = raw_statuses if isinstance(raw_statuses, dict) else {}
    legacy = raw_features if isinstance(raw_features, dict) else {}
    lines.append("\n<b>Функции</b> <i>(это не тест реального действия)</i>")
    for key, label in _CAPABILITY_LABELS.items():
        state = statuses.get(key)
        if isinstance(state, dict):
            state = state.get("state")
        if not isinstance(state, str) or state not in _CAPABILITY_STATES:
            old_key = {"microphone": "mic", "webcam": "camera_api"}.get(key, key)
            old_value = legacy.get(old_key)
            state_text = (
                "заявлено старым агентом; реальная проверка не выполнена"
                if old_value is True
                else "нет свежего статуса"
            )
        else:
            state_text = _CAPABILITY_STATES[state]
        lines.append(f"• {label}: {state_text}")

    commands = result.get("commands")
    if isinstance(commands, list):
        clean = sorted({
            command for command in commands[:256]
            if isinstance(command, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", command)
        })
        shown = clean[:32]
        suffix = f"\n… ещё {len(clean) - len(shown)}" if len(clean) > len(shown) else ""
        lines.append(f"\n<b>Заявлено команд:</b> {len(clean)}")
        if shown:
            lines.append(html.escape(", ".join(shown), quote=False) + suffix)
    else:
        lines.append("\nСписок команд не получен.")
    return "\n".join(lines)

@router.callback_query(AdminFilter(), F.data == "cmd:sysinfo")
async def on_cmd_sysinfo(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    if target == "all":
        await cq.answer(_lex("single_device_only"), show_alert=True)
        return
    await cq.answer("💻 Запрашиваю...")
    if not await _callback_result_access_or_report(cq, target):
        return
    sent, command_id = publish_tracked("sysinfo", _target=target)
    if not sent:
        await _replace_callback_message(
            cq,
            _lex("mqtt_disconnected"),
            reply_markup=back_to_device_kb(),
        )
        return
    result = await sysinfo_collector.wait_for(target, "sysinfo", 12.0, command_id)
    if not await _callback_result_access_or_report(cq, target):
        return
    if result is None:
        await _replace_callback_message(
            cq,
            _lex("device_timeout", seconds="12"),
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
        await cq.answer(_lex("target_required"), show_alert=True)
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
            _lex("mqtt_disconnected"),
            reply_markup=back_to_device_kb(),
        )


@router.callback_query(AdminFilter(), F.data == "cmd:volume")
async def on_cmd_volume(cq: CallbackQuery):
    await simple_command(cq, "volume_toggle", "🔇", "Mute/Unmute звука", timeout=8.0)


@router.callback_query(AdminFilter(), F.data == "cmd:status")
async def on_cmd_status(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("target_required"), show_alert=True)
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
                _lex("mqtt_disconnected"),
                reply_markup=system_menu(),
            )
        return
    if not await _callback_result_access_or_report(cq, target):
        return
    sent, command_id = publish_tracked("status_request", _target=target)
    if not sent:
        await _replace_callback_message(
            cq,
            _lex("mqtt_disconnected"),
            reply_markup=system_menu(),
        )
        return
    try:
        await cq.message.edit_text(
            _lex_html("status_waiting", device=target_label(target)),
            reply_markup=system_menu(),
        )
    except Exception:
        pass
    status = await status_collector.wait_for(target, "status", 12.0, command_id)
    if not await _callback_result_access_or_report(cq, target):
        return
    if status is None:
        await _replace_callback_message(
            cq,
            _lex("device_no_response"),
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
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    await cq.message.edit_text(
        _lex_html("power_menu_intro", device=target_label(target)),
        reply_markup=power_menu_new(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.startswith("power:"))
async def on_power_action(cq: CallbackQuery):
    action = cq.data.split(":", 1)[1]
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    action_key = {
        "shutdown": "power_action_shutdown",
        "reboot": "power_action_reboot",
        "sleep": "power_action_sleep",
    }.get(action)
    label = _lex(action_key) if action_key else action.upper()
    await cq.message.edit_text(
        _lex_html("power_confirm_prompt", action=label, device=target_label(target)),
        reply_markup=confirm_power_menu(action),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.startswith("power_confirm:"))
async def on_power_confirm(cq: CallbackQuery):
    action = cq.data.split(":", 1)[1]
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    fun_text_collector.reset()
    if publish("power", _target=target, action=action):
        emojis = {"reboot": "🔄", "shutdown": "⚡", "sleep": "😴"}
        labels = {"reboot": "Перезагрузка", "shutdown": "Выключение", "sleep": "Сон"}
        emoji = emojis.get(action, "⚡")
        label = labels.get(action, action)
        await cq.answer(f"{emoji} {label} запущена...")
        res = await fun_text_collector.wait(5.0)
        msg = (res or {}).get("text") or f"{emoji} {label} отправлена: <b>{target_label(target)}</b>"
        await cq.message.edit_text(
            _lex_html("power_action_result", text=msg),
            reply_markup=back_to_device_kb(),
        )
    else:
        await cq.message.edit_text(
            _lex("mqtt_disconnected"),
            reply_markup=back_to_device_kb(),
        )
        await cq.answer()


@router.callback_query(AdminFilter(), F.data == "cmd:stop")
async def on_cmd_stop(cq: CallbackQuery):
    # Stop through Guardian so its desired-state is updated too; a raw worker
    # stop would be interpreted as a crash and immediately undone by Keeper.
    await _guardian_command(cq, "stop")


# =====================================================================
#  Хендлеры: категории нового меню (9 разделов)
# =====================================================================

@router.callback_query(AdminFilter(), F.data == "cat:media")
async def on_cat_media(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("device_required"), show_alert=True)
        return
    await cq.message.edit_text(
        _lex_html("category_media", device=target_label(target)),
        reply_markup=media_menu(cq.from_user.id if cq.from_user else None),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "cat:screen")
async def on_cat_screen(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("device_required"), show_alert=True)
        return
    await cq.message.edit_text(
        _lex_html("category_screen", device=target_label(target)),
        reply_markup=screen_menu(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.in_({"cat:input", "cat:control"}))
async def on_cat_input(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("device_required"), show_alert=True)
        return
    await cq.message.edit_text(
        _lex_html("category_input", device=target_label(target)),
        reply_markup=input_menu(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "cat:system")
async def on_cat_system(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("device_required"), show_alert=True)
        return
    await cq.message.edit_text(
        _lex_html("category_system", device=target_label(target)),
        reply_markup=system_menu(cq.from_user.id if cq.from_user else None),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "cat:network")
async def on_cat_network(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("device_required"), show_alert=True)
        return
    await cq.message.edit_text(
        _lex_html("category_network", device=target_label(target)),
        reply_markup=network_menu(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.in_({"cat:files", "files:menu"}))
async def on_cat_files(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target or target == "all":
        await cq.answer(_lex("device_required"), show_alert=True)
        return
    await cq.message.edit_text(
        _lex_html("files_menu_title", device=target_label(target))
        + "\n"
        + _lex("files_menu_description"),
        reply_markup=files_menu(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.in_({"cat:terminal", "cat:fun", "fun:menu"}))
async def on_cat_terminal(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("device_required"), show_alert=True)
        return
    await cq.message.edit_text(
        _lex_html("category_terminal", device=target_label(target)),
        reply_markup=terminal_menu(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "cat:power")
async def on_cat_power(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("device_required"), show_alert=True)
        return
    await cq.message.edit_text(
        _lex_html("category_power", device=target_label(target)),
        reply_markup=power_menu_new(cq.from_user.id if cq.from_user else None),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "cat:pranks")
async def on_cat_pranks(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("device_required"), show_alert=True)
        return
    await cq.message.edit_text(
        _lex_html("category_pranks", device=target_label(target)),
        reply_markup=pranks_menu(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "cat:device")
async def on_cat_device(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("device_required"), show_alert=True)
        return
    await cq.message.edit_text(
        _lex_html("category_device_settings", device=target_label(target)),
        reply_markup=device_settings_menu(),
    )

    await cq.answer()


# ---------- Процессы: пагинация + kill по кнопке ----------

@router.callback_query(AdminFilter(), F.data.startswith("proc:"))
async def on_proc_page(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target or target == "all":
        await cq.answer(_lex("single_device_only"), show_alert=True)
        return
    refresh = cq.data == "proc:refresh"
    page = 0 if refresh else int(cq.data.split(":", 1)[1] or 0)
    cache = None if refresh else LAST_PROCESSES.get(target)
    if not cache or time.time() - cache["ts"] > 30:
        await cq.answer("⚙️ Запрашиваю процессы...")
        processes_collector.reset()
        if not transport.publish_command(target, "processes", top_n=15):
            await cq.message.answer(_lex("mqtt_disconnected"))
            return
        res = await processes_collector.wait(15.0)
        if not res:
            await cq.message.answer(
                _lex("device_no_response"),
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
        kb.button(text=_limit_button_label(label), callback_data=f"killq:{it.get('pid')}", style="danger")
    if pages > 1:
        if page > 0:
            kb.button(text="◀️", callback_data=f"proc:{page - 1}", style="primary")
        kb.button(text=_limit_button_label(f"{page + 1}/{pages}"), callback_data="noop", style="primary")
        if page < pages - 1:
            kb.button(text="▶️", callback_data=f"proc:{page + 1}", style="primary")
    kb.button(text=_nav("refresh"), callback_data="proc:refresh", style="success")
    kb.button(text=_nav("back_device"), callback_data="back:device", style="primary")
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
        reply_markup=confirm_kb(
            f"kill:{pid}", yes_text=_lex("confirm_kill_process_button")
        ),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.startswith("kill:"))
async def on_kill(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target or target == "all":
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    pid = cq.data.split(":", 1)[1]
    if publish("kill_process", pid=pid):
        await cq.message.answer(f"🔪 Команда kill отправлена (pid {pid}).")
    else:
        await cq.message.answer(_lex("mqtt_disconnected"))
    await cq.answer()


# ---------- Массовые команды (все устройства) ----------

@router.callback_query(AdminFilter(), F.data == "all:status")
async def on_all_status(cq: CallbackQuery):
    multi_status.reset()
    if not transport.publish_command("all", "status_request"):
        await cq.message.answer(_lex("mqtt_disconnected"))
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
        await cq.message.answer(_lex("mqtt_disconnected"))
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
            await cq.message.answer(_lex("mqtt_disconnected"))
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
        await cq.answer(_lex("single_device_only"), show_alert=True)
        return
    await cq.answer("🗂 Запрашиваю диски...")
    if not await _callback_result_access_or_report(cq, target):
        return
    sent, command_id = publish_tracked("disks", _target=target)
    if not sent:
        await _replace_callback_message(
            cq, _lex("mqtt_disconnected"), reply_markup=back_to_device_kb()
        )
        return
    res = await disks_collector.wait_for(target, "disks", 15.0, command_id)
    if not await _callback_result_access_or_report(cq, target):
        return
    if not res:
        await _replace_callback_message(
            cq, _lex("device_no_response"), reply_markup=back_to_device_kb()
        )
        return
    lines = res.get("lines", [])
    text = "🗂 <b>Диски</b>:\n" + "\n".join(html.escape(l) for l in lines)
    await _replace_callback_message(cq, text, reply_markup=back_to_device_kb())


@router.callback_query(AdminFilter(), F.data == "vol:opts")
async def on_vol_opts(cq: CallbackQuery):
    await cq.message.edit_text(_lex("volume_options_title"), reply_markup=vol_options_menu())
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.startswith("volq:"))
async def on_vol_set(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target or target == "all":
        await cq.answer(_lex("single_device_only"), show_alert=True)
        return
    level = int(cq.data.split(":", 1)[1] or 50)
    await simple_command(cq, "volume_set", "🎚", f"Громкость {level}%", timeout=8.0, level=level)


@router.callback_query(AdminFilter(), F.data == "mic:opts")
async def on_mic_opts(cq: CallbackQuery):
    await cq.message.edit_text(
        _lex("mic_duration_prompt"),
        reply_markup=mic_options_menu(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.startswith("micdur:"))
async def on_mic_dur(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target or target == "all":
        await cq.answer(_lex("single_device_only"), show_alert=True)
        return
    dur = int(cq.data.split(":", 1)[1] or 5)
    await cq.answer(f"🎙 Запись {dur} сек...")
    if not await _callback_result_access_or_report(cq, target):
        return
    sent, command_id = publish_tracked("mic", _target=target, duration=dur)
    if not sent:
        await cq.message.answer(_lex("mqtt_disconnected"))
        await cq.answer()
        return
    res = await mic_collector.wait_for(target, "mic", dur + 12.0, command_id)
    if not await _callback_result_access_or_report(cq, target):
        return
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
        await cq.message.answer(_lex("mqtt_publish_failed"))
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "cmd:clipset")
async def on_cmd_clipset(cq: CallbackQuery, state: FSMContext):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    if target == "all":
        await cq.answer(_lex("single_device_only"), show_alert=True)
        return
    await _start_authorized_input(state, Form.wait_clipset, cq.data, target, command_target=target)
    await _replace_callback_message(
        cq,
        "📥 Отправь текст — он попадёт в буфер обмена устройства.",
        reply_markup=back_to_device_kb(),
    )
    await cq.answer()


@router.message(AdminFilter(), Form.wait_clipset)
async def on_clipset_input(message: Message, state: FSMContext):
    form_data = await state.get_data()
    await state.clear()
    text = (message.text or "").strip()
    target = form_data.get("command_target")
    if not text:
        await _replace_user_card(message, _lex("empty_text"), reply_markup=back_to_device_kb())
        return
    if not target:
        await _replace_user_card(message, _lex("target_required"), reply_markup=back_to_device_kb())
        return
    if target == "all":
        await _replace_user_card(message, _lex("single_device_only"), reply_markup=back_to_device_kb())
        return
    if not _pending_device_input_still_allowed(message, form_data, target):
        await _reject_revoked_device_input(message)
        return
    if publish("clipboard_set", _target=target, text=text):
        await _replace_user_card(message, f"📥 Запрос записи текста в буфер отправлен на <b>{html.escape(target_label(target))}</b>.", reply_markup=back_to_device_kb())
    else:
        await _replace_user_card(message, _lex("mqtt_disconnected"), reply_markup=back_to_device_kb())


# ---------- События (уведомления) ----------

@router.callback_query(AdminFilter(), F.data == "ev:server_autostart:toggle")
async def on_server_autostart_toggle(cq: CallbackQuery):
    is_on, msg = toggle_server_autostart()
    await cq.answer(msg, show_alert=True)
    await cq.message.edit_text(
        _lex("events_menu_intro"),
        reply_markup=events_menu(),
    )


@router.callback_query(AdminFilter(), F.data == "ev:menu")
async def on_ev_menu(cq: CallbackQuery):
    await cq.message.edit_text(
        _lex("events_menu_intro"),
        reply_markup=events_menu(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.startswith("ev:toggle:"))
async def on_ev_toggle(cq: CallbackQuery):
    key = cq.data.split(":", 2)[2]
    new_val = bot_settings.toggle(key)
    audit("settings_toggle", key=key, value=new_val)
    await cq.message.edit_text(
        _lex("events_menu_intro"),
        reply_markup=events_menu(),
    )
    await cq.answer("Включено ✅" if new_val else "Выключено ❌")


# ---------- Тихие часы / дайджест / администраторы ----------

@router.callback_query(AdminFilter(), F.data == "ev:quiet")
async def on_ev_quiet(cq: CallbackQuery):
    await cq.message.edit_text(
        _lex("quiet_hours_intro"),
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
        _lex("quiet_hours_intro"),
        reply_markup=quiet_hours_menu(),
    )


@router.callback_query(AdminFilter(), F.data == "ev:digest")
async def on_ev_digest(cq: CallbackQuery):
    await cq.message.edit_text(
        _lex("digest_menu_intro"),
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
        _lex("digest_menu_intro"),
        reply_markup=digest_menu(),
    )


@router.callback_query(AdminFilter(), F.data == "ev:admins")
async def on_ev_admins(cq: CallbackQuery):
    await cq.message.edit_text(
        _lex("admins_menu_intro"),
        reply_markup=admins_menu(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "adm:add")
async def on_adm_add(cq: CallbackQuery, state: FSMContext):
    await state.set_state(Form.wait_admin)
    await _replace_callback_message(
        cq,
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
        await _replace_user_card(
            message,
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
        await _replace_user_card(
            message,
            f"✅ <b>{html.escape(str(label))}</b> добавлен администратором.",
            reply_markup=admins_menu(),
        )
    else:
        await _replace_user_card(
            message,
            "ℹ️ Уже есть в списке.", reply_markup=admins_menu()
        )


@router.callback_query(AdminFilter(), F.data.startswith("adm:rm:"))
async def on_adm_rm(cq: CallbackQuery):
    raw = cq.data.split(":", 2)[2]
    if raw.isdigit() and bot_settings.remove_admin(int(raw)):
        audit("admin_removed", user_id=raw)
        await cq.message.edit_text(
            _lex("admins_menu_intro"),
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
        await cq.answer(_lex("single_device_only"), show_alert=True)
        return
    favs = devices.get_favorites(target)
    await cq.message.edit_text(
        _lex_html("favorites_page_intro", device=target_label(target)),
        reply_markup=_favorites_kb(target, favs),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.startswith("fav:toggle:"))
async def on_fav_toggle(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target or target == "all":
        await cq.answer(_lex("single_device_only"), show_alert=True)
        return
    action = cq.data.split(":", 2)[2]
    added = devices.toggle_favorite(target, action)
    favs = devices.get_favorites(target)
    await cq.message.edit_text(
        _lex_html("favorites_page_intro", device=target_label(target)),
        reply_markup=_favorites_kb(target, favs),
    )
    await cq.answer("Добавлено ⭐" if added else "Убрано")


# ---------- История команд ----------

@router.callback_query(AdminFilter(), F.data == "hist:0")
async def on_history(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target or target == "all":
        await cq.answer(_lex("single_device_only"), show_alert=True)
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
                text=_limit_button_label(f"↻ {ACTION_LABELS.get(action, action)}"),
                callback_data=f"rep:{i}",
                style="primary",
            )
    kb.button(text=_nav("back_device"), callback_data="back:device", style="primary")
    kb.adjust(1)
    await cq.message.answer(text, reply_markup=kb.as_markup())
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.startswith("rep:"))
async def on_repeat(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target or target == "all":
        await cq.answer(_lex("target_required"), show_alert=True)
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
        await cq.message.answer(_lex("mqtt_disconnected"))
        await cq.answer()


# ---------- Подтверждения опасных действий ----------

CONFIRM_PROMPTS = {
    "shell": ("💻 Выполнить терминал на устройстве?", "shell:ok"),
    "stop": ("⏹ Остановить клиента на устройстве?", "stop:ok"),
    "stop_all": ("⏹ Остановить клиенты на ВСЕХ устройствах?", "stopall:ok"),
    "guardian_stop": ("⏹ Остановить рабочий агент? Guardian останется доступен для запуска.", "guardianstop:ok"),
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
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    text, yes_cb = CONFIRM_PROMPTS[kind]
    await cq.message.answer(
        f"⚠️ {text}",
        reply_markup=confirm_kb(yes_cb, yes_text=_lex("confirm_yes_button")),
    )
    await cq.answer()


@router.callback_query(OwnerFilter(), F.data == "shell:ok")
async def on_shell_ok(cq: CallbackQuery, state: FSMContext):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    if target == "all":
        await cq.answer(_lex("single_device_only"), show_alert=True)
        return
    await state.set_state(Form.wait_shell)
    await state.update_data(command_target=target)
    await _replace_callback_message(
        cq,
        _lex("shell_prompt"),
        reply_markup=back_to_device_kb(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "openapp:ok")
async def on_openapp_ok(cq: CallbackQuery, state: FSMContext):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    if target == "all":
        await cq.answer(_lex("single_device_only"), show_alert=True)
        return
    await _start_authorized_input(state, Form.wait_open_app, cq.data, target, command_target=target)
    await _replace_callback_message(
        cq,
        _lex("open_app_prompt"),
        reply_markup=back_to_device_kb(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "stop:ok")
async def on_stop_ok(cq: CallbackQuery):
    await _guardian_command(cq, "stop")


@router.callback_query(AdminFilter(), F.data == "stopall:ok")
async def on_stopall_ok(cq: CallbackQuery):
    if transport.publish_command("all", "guardian", action="guardian", command="stop"):
        await _replace_callback_message(
            cq,
            "⏹ Команда остановки рабочих агентов отправлена всем. Guardian останется доступен.",
            reply_markup=back_to_device_kb(),
        )
    else:
        await _replace_callback_message(cq, _lex("mqtt_disconnected"), reply_markup=back_to_device_kb())
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "guardianstop:ok")
async def on_guardian_stop_ok(cq: CallbackQuery):
    await _guardian_command(cq, "stop")


@router.callback_query(AdminFilter(), F.data == "sleep:ok")
async def on_sleep_ok(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    if publish("power", _target=target, action="sleep"):
        await _replace_callback_message(
            cq,
            _lex_html("power_action_result", text=f"😴 Сон отправлен: {target_label(target)}"),
            reply_markup=back_to_device_kb(),
        )
    else:
        await _replace_callback_message(
            cq, _lex("mqtt_disconnected"), reply_markup=back_to_device_kb()
        )
    await cq.answer()


# =====================================================================
#  Файлы и папки (волна новых команд)
# =====================================================================

def _require_target(cq: CallbackQuery) -> str | None:
    target = SESSION.get("target")
    if not target or target == "all":
        return None
    return target


def _device_action_still_allowed(user_id: int, callback: str, target: str) -> bool:
    """Recheck the action and target immediately before publishing to MQTT."""
    if not target or target == "all" or devices.get(target) is None:
        return False
    return access_store.can_use_callback(user_id, callback, ADMIN_ID, target)


async def _callback_result_access_or_report(cq: CallbackQuery, target: str) -> bool:
    """Recheck the exact callback grant after awaits before dispatching or returning data."""
    user_id = int(cq.from_user.id) if cq.from_user else 0
    callback = str(cq.data or "")
    if _simple_command_still_allowed(user_id, callback, target):
        return True
    await _replace_callback_message(
        cq,
        _lex("action_result_hidden_after_revoke"),
        reply_markup=back_to_device_kb(),
    )
    return False


def _pending_device_input_still_allowed(
    message: Message,
    form_data: dict,
    requested_target: str | None = None,
) -> bool:
    """Revalidate a prompted device action after awaits and before its side effect.

    User forms must carry the callback and device captured when the prompt was
    opened; neither the mutable current selection nor stale FSM state is an
    authorization source. Owner forms keep their existing full-access path.
    """
    if not message.from_user:
        return False
    user_id = int(message.from_user.id)
    callback = str(form_data.get("authorization_callback") or "")
    bound_target = str(form_data.get("authorization_target") or "")
    if get_user_role(user_id) == Role.OWNER:
        bound_target = bound_target or str(
            requested_target
            or form_data.get("command_target")
            or form_data.get("target")
            or SESSION.get("target")
            or ""
        )
    elif not callback or not bound_target:
        return False
    if requested_target and str(requested_target) != bound_target:
        return False
    return _device_action_still_allowed(user_id, callback, bound_target)


async def _reject_revoked_device_input(message: Message) -> None:
    await _replace_user_card(
        message,
        _lex("input_access_revoked"),
        reply_markup=back_to_device_kb(),
    )


async def _report_revoked_after_publish(message: Message) -> None:
    """Hide a response after revocation without implying the command was cancelled."""
    await _replace_user_card(
        message,
        _lex("action_result_hidden_after_revoke"),
        reply_markup=back_to_device_kb(),
    )


def _safe_transfer_filename(value: object) -> str:
    """Keep Telegram/agent filenames as a single portable Downloads entry."""
    raw = str(value or "file.bin").replace("\\", "/")
    name = raw.rsplit("/", 1)[-1]
    name = re.sub(r'[<>:"|?*\x00-\x1f\x7f]', "_", name).strip().rstrip(" .")
    if name in {"", ".", ".."}:
        name = "file.bin"

    stem = name.split(".", 1)[0].upper()
    if stem in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(r"(?:COM|LPT)[1-9]", stem):
        name = f"_{name}"

    # POSIX filesystems cap a single path component at 255 UTF-8 bytes. Keep
    # the same bound on every client and never split a multibyte character.
    name = name.encode("utf-8")[:255].decode("utf-8", errors="ignore").rstrip(" .")
    return name or "file.bin"


@router.callback_query(AdminFilter(), F.data == "files:menu")
async def on_files_menu(cq: CallbackQuery):
    if not _require_target(cq):
        await cq.answer(_lex("device_required"), show_alert=True)
        return
    await cq.message.edit_text(
        _lex_html("files_menu_title", device=target_label(SESSION["target"])),
        reply_markup=files_menu(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "files:list")
async def on_files_list(cq: CallbackQuery, state: FSMContext):
    target = _require_target(cq)
    if not target:
        await cq.answer(_lex("device_required"), show_alert=True)
        return
    await _start_authorized_input(state, Form.wait_path, cq.data, target, path_mode="list", target=target)
    await _replace_callback_message(
        cq,
        _lex("files_prompt_list"),
        reply_markup=back_to_device_kb(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "files:find")
async def on_files_find(cq: CallbackQuery, state: FSMContext):
    target = _require_target(cq)
    if not target:
        await cq.answer(_lex("device_required"), show_alert=True)
        return
    await _start_authorized_input(state, Form.wait_find, cq.data, target, target=target)
    await _replace_callback_message(
        cq,
        _lex("files_prompt_find"),
        reply_markup=back_to_device_kb(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "files:get")
async def on_files_get(cq: CallbackQuery, state: FSMContext):
    target = _require_target(cq)
    if not target:
        await cq.answer(_lex("device_required"), show_alert=True)
        return
    await _start_authorized_input(state, Form.wait_path, cq.data, target, path_mode="get", target=target)
    await _replace_callback_message(
        cq,
        _lex("files_prompt_get"),
        reply_markup=back_to_device_kb(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "files:put")
async def on_files_put(cq: CallbackQuery, state: FSMContext):
    target = _require_target(cq)
    if not target:
        await cq.answer(_lex("device_required"), show_alert=True)
        return
    await _start_authorized_input(state, Form.wait_path, cq.data, target, path_mode="put", target=target)
    await _replace_callback_message(
        cq,
        _lex("files_prompt_put"),
        reply_markup=back_to_device_kb(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "files:del")
async def on_files_del(cq: CallbackQuery, state: FSMContext):
    target = _require_target(cq)
    if not target:
        await cq.answer(_lex("device_required"), show_alert=True)
        return
    await _start_authorized_input(state, Form.wait_path, cq.data, target, path_mode="del", target=target)
    await _replace_callback_message(
        cq,
        _lex("files_prompt_delete"),
        reply_markup=back_to_device_kb(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "files:open")
async def on_files_open(cq: CallbackQuery, state: FSMContext):
    target = _require_target(cq)
    if not target:
        await cq.answer(_lex("device_required"), show_alert=True)
        return
    await _start_authorized_input(state, Form.wait_path, cq.data, target, path_mode="open", target=target)
    await _replace_callback_message(
        cq,
        _lex("files_prompt_open"),
        reply_markup=back_to_device_kb(),
    )
    await cq.answer()


@router.message(AdminFilter(), Form.wait_path)
async def on_path_input(message: Message, state: FSMContext):
    data = await state.get_data()
    mode = data.get("path_mode", "list")
    await state.clear()
    target = data.get("target") or SESSION.get("target")
    if not target or target == "all":
        await _replace_user_card(message, _lex("target_required"), reply_markup=back_to_device_kb())
        return

    if mode == "put":
        doc = message.document
        if not doc:
            await _replace_user_card(message, _lex("files_document_required"), reply_markup=back_to_device_kb())
            return
        buf = await bot.download(doc)
        chunk = buf.read()
        name = _safe_transfer_filename(doc.file_name)
        dest = f"~/Downloads/{name}"
        if len(chunk) > 30 * 1024 * 1024:
            await _replace_user_card(message, _lex("files_size_limit", limit="30"), reply_markup=back_to_device_kb())
            return
        # Telegram's download is an external await: the role, grant, or device
        # may have changed while it was in flight. Do not publish stale input.
        if not _pending_device_input_still_allowed(message, data, target):
            await _reject_revoked_device_input(message)
            return
        sent, command_id = publish_tracked(
            "file_put",
            _target=target,
            name=name,
            path=dest,
            b64=base64.b64encode(chunk).decode("ascii"),
        )
        if not sent:
            await _replace_user_card(message, _lex("mqtt_disconnected"), reply_markup=back_to_device_kb())
            return
        result = await fun_text_collector.wait_for(target, "file_put", timeout=25.0, command_id=command_id)
        if not _pending_device_input_still_allowed(message, data, target):
            await _report_revoked_after_publish(message)
            return
        if result and result.get("ok"):
            await _replace_user_card(
                message,
                _lex_html("files_upload_saved", path=result.get("path") or dest),
                reply_markup=back_to_device_kb(),
            )
        else:
            error = (result or {}).get("error") or _lex("device_no_response")
            await _replace_user_card(
                message,
                _lex_html("files_upload_failed", error=error),
                reply_markup=back_to_device_kb(),
            )
        return

    path = (message.text or "").strip()
    if not path:
        await _replace_user_card(message, _lex("files_empty_path"), reply_markup=back_to_device_kb())
        return

    if mode == "list":
        if not _pending_device_input_still_allowed(message, data, target):
            await _reject_revoked_device_input(message)
            return
        sent, command_id = publish_tracked("dir_list", _target=target, path=path)
        if not sent:
            await _replace_user_card(message, _lex("mqtt_disconnected"), reply_markup=back_to_device_kb())
            return
        result = await fun_text_collector.wait_for(target, "dir_list", timeout=15.0, command_id=command_id)
        if not _pending_device_input_still_allowed(message, data, target):
            await _report_revoked_after_publish(message)
            return
        text = (result or {}).get("text") or _lex("device_no_response")
        await _replace_user_card(
            message,
            _lex_html("files_list_result", text=str(text)[:3500]),
            reply_markup=back_to_device_kb(),
        )
    elif mode == "get":
        if not _pending_device_input_still_allowed(message, data, target):
            await _reject_revoked_device_input(message)
            return
        sent, command_id = publish_tracked("file_get", _target=target, path=path)
        if not sent:
            await _replace_user_card(message, _lex("mqtt_disconnected"), reply_markup=back_to_device_kb())
            return
        result = await file_collector.wait_for(target, "file_get", timeout=30.0, command_id=command_id)
        # File content is sensitive; if authorization changed while waiting,
        # do not deliver the returned document to Telegram.
        if not _pending_device_input_still_allowed(message, data, target):
            await _report_revoked_after_publish(message)
            return
        if not result:
            await _replace_user_card(message, _lex("files_download_timeout"), reply_markup=back_to_device_kb())
            return
        if not result.get("ok", False):
            await _replace_user_card(
                message,
                _lex_html("files_download_failed", error=result.get("error") or ""),
                reply_markup=back_to_device_kb(),
            )
            return
        encoded = result.get("data") or result.get("b64") or ""
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            await _replace_user_card(message, _lex("files_download_corrupt"), reply_markup=back_to_device_kb())
            return
        await _replace_user_card_with_document(
            message,
            BufferedInputFile(
                data,
                filename=_safe_transfer_filename(result.get("filename") or result.get("name")),
            ),
            caption=_lex_html("files_download_caption", device=target_label(target)),
            reply_markup=back_to_device_kb(),
        )
    elif mode == "del":
        if not _pending_device_input_still_allowed(message, data, target):
            await _reject_revoked_device_input(message)
            return
        sent, command_id = publish_tracked("file_del", _target=target, path=path)
        if not sent:
            await _replace_user_card(message, _lex("mqtt_disconnected"), reply_markup=back_to_device_kb())
            return
        result = await fun_text_collector.wait_for(target, "file_del", timeout=15.0, command_id=command_id)
        if not _pending_device_input_still_allowed(message, data, target):
            await _report_revoked_after_publish(message)
            return
        if result and result.get("ok"):
            await _replace_user_card(
                message,
                _lex_html("files_delete_done", path=path),
                reply_markup=back_to_device_kb(),
            )
        else:
            await _replace_user_card(
                message,
                _lex_html("files_delete_failed", error=(result or {}).get("error") or _lex("device_no_response")),
                reply_markup=back_to_device_kb(),
            )
    elif mode == "open":
        if not _pending_device_input_still_allowed(message, data, target):
            await _reject_revoked_device_input(message)
            return
        sent, command_id = publish_tracked("path_open", _target=target, path=path)
        if not sent:
            await _replace_user_card(message, _lex("mqtt_disconnected"), reply_markup=back_to_device_kb())
            return
        result = await fun_text_collector.wait_for(target, "path_open", timeout=10.0, command_id=command_id)
        if not _pending_device_input_still_allowed(message, data, target):
            await _report_revoked_after_publish(message)
            return
        if result and result.get("ok"):
            await _replace_user_card(
                message,
                _lex_html("files_open_done", path=path),
                reply_markup=back_to_device_kb(),
            )
        else:
            await _replace_user_card(
                message,
                _lex_html("files_open_failed", error=(result or {}).get("error") or _lex("device_no_response")),
                reply_markup=back_to_device_kb(),
            )


@router.message(AdminFilter(), Form.wait_find)
async def on_find_input(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    target = data.get("target") or SESSION.get("target")
    pattern = (message.text or "").strip()
    if not target or target == "all":
        await _replace_user_card(message, _lex("device_required"), reply_markup=back_to_device_kb())
        return
    if not pattern:
        await _replace_user_card(message, _lex("files_find_empty"), reply_markup=back_to_device_kb())
        return
    if not _pending_device_input_still_allowed(message, data, target):
        await _reject_revoked_device_input(message)
        return
    sent, command_id = publish_tracked("find_file", _target=target, pattern=pattern, root="~")
    if not sent:
        await _replace_user_card(message, _lex("mqtt_disconnected"), reply_markup=back_to_device_kb())
        return
    result = await fun_text_collector.wait_for(target, "find_file", timeout=20.0, command_id=command_id)
    if not _pending_device_input_still_allowed(message, data, target):
        await _report_revoked_after_publish(message)
        return
    text = (result or {}).get("text")
    if not text:
        await _replace_user_card(message, _lex("files_find_no_response"), reply_markup=back_to_device_kb())
        return
    await _replace_user_card(
        message,
        _lex_html("files_find_result", text=str(text)[:3500]),
        reply_markup=back_to_device_kb(),
    )


# =====================================================================
#  Утилиты и удаленный ввод (Fun / Tools)
# =====================================================================

@router.callback_query(AdminFilter(), F.data.in_({"fun:extip", "cmd:extip"}))
async def on_fun_extip(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    await cq.answer("🌍 Запрашиваю внешний IP...")
    fun_text_collector.reset()
    if not publish("ext_ip"):
        await _replace_callback_message(cq, _lex("mqtt_disconnected"), reply_markup=back_to_device_kb())
        return
    result = await fun_text_collector.wait(10.0)
    text = (result or {}).get("text") or _lex("device_no_response")
    await _replace_callback_message(cq, f"🌍 <b>Внешний IP ({target_label(target)}):</b>\n<code>{html.escape(str(text))}</code>", reply_markup=back_to_device_kb())


@router.callback_query(AdminFilter(), F.data.in_({"fun:screenoff", "cmd:screenoff"}))
async def on_fun_screenoff(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    if publish("screen_off"):
        await cq.answer("🧯 Экран выключен")
        await _replace_callback_message(cq, f"🧯 Экран успешно погашен: <b>{target_label(target)}</b>", reply_markup=back_to_device_kb())
    else:
        await cq.answer(_lex("send_failed"))
        await _replace_callback_message(cq, _lex("send_failed"), reply_markup=back_to_device_kb())


@router.callback_query(AdminFilter(), F.data.in_({"fun:screensaver", "cmd:saveron"}))
async def on_fun_screensaver(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    if publish("screensaver_on"):
        await cq.answer("💤 Заставка запущена")
        await _replace_callback_message(cq, f"💤 Заставка экрана включена: <b>{target_label(target)}</b>", reply_markup=back_to_device_kb())
    else:
        await cq.answer(_lex("send_failed"))
        await _replace_callback_message(cq, _lex("send_failed"), reply_markup=back_to_device_kb())


@router.callback_query(AdminFilter(), F.data.in_({"fun:type", "cmd:typetxt"}))
async def on_fun_type(cq: CallbackQuery, state: FSMContext):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    if target == "all":
        await cq.answer(_lex("single_device_only"), show_alert=True)
        return
    await _start_authorized_input(state, Form.wait_fun_text, cq.data, target, command_target=target)
    await _replace_callback_message(cq, "⌨️ Введите текст для набора на выбранном устройстве:", reply_markup=back_to_device_kb())
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.in_({"fun:hotkey", "cmd:hotkey"}))
async def on_fun_hotkey(cq: CallbackQuery, state: FSMContext):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    if target == "all":
        await cq.answer(_lex("single_device_only"), show_alert=True)
        return
    await _start_authorized_input(state, Form.wait_fun_hotkey, cq.data, target, command_target=target)
    await _replace_callback_message(cq, "⌘ Введите сочетание клавиш через плюс (например: <code>ctrl+c</code>, <code>cmd+space</code>, <code>alt+tab</code>):", reply_markup=back_to_device_kb())
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.in_({"fun:wallpaper", "cmd:wallpaper"}))
async def on_fun_wallpaper(cq: CallbackQuery, state: FSMContext):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    if target == "all":
        await cq.answer(_lex("single_device_only"), show_alert=True)
        return
    await _start_authorized_input(state, Form.wait_fun_wallpaper, cq.data, target, command_target=target)
    await _replace_callback_message(cq, "🖼 Отправьте прямую ссылку на картинку (.jpg или .png) для установки на рабочий стол:", reply_markup=back_to_device_kb())
    await cq.answer()


async def _run_text_prank_input(
    message: Message,
    state: FSMContext,
    *,
    action: str,
    emoji: str,
    label_key: str,
    timeout: float,
    **publish_kwargs,
):
    """Finish a prompted prank by editing the owner's existing bot card."""
    form_data = await state.get_data()
    await state.clear()
    target = form_data.get("target")
    text = (message.text or "").strip()
    if action not in _PROMPTED_TEXT_ACTIONS:
        log.error("Blocked unapproved prompted-text action: %s", action)
        await _replace_user_card(
            message, _lex("send_failed"), reply_markup=back_to_device_kb()
        )
        return
    if not target:
        await _replace_user_card(
            message, _lex("target_required"), reply_markup=back_to_device_kb()
        )
        return
    if not text:
        await _replace_user_card(
            message, _lex("empty_text"), reply_markup=back_to_device_kb()
        )
        return
    if not _pending_device_input_still_allowed(message, form_data, target):
        await _reject_revoked_device_input(message)
        return

    label = _simple_command_label(action, _lex(label_key), {})
    sent, command_id = publish_tracked(
        action,
        _target=target,
        text=text,
        **publish_kwargs,
    )
    if not sent:
        await _replace_user_card(
            message, _lex("mqtt_disconnected"), reply_markup=back_to_device_kb()
        )
        return

    await _replace_user_card(
        message,
        _lex_html(
            "command_waiting",
            emoji=emoji,
            label=label,
            device=target_label(target),
        ),
        reply_markup=back_to_device_kb(),
    )
    result = await fun_text_collector.wait_for(
        target, action, timeout=timeout, command_id=command_id
    )
    if result is None:
        result_text = _lex("device_timeout", seconds=str(int(timeout)))
    else:
        result_text = result.get("text") or result.get("error")
        if not result_text:
            result_text = (
                _lex("device_command_completed")
                if result.get("ok")
                else _lex("device_no_response")
            )
    await _replace_user_card(
        message,
        _lex_html(
            "command_result",
            emoji=emoji,
            label=label,
            device=target_label(target),
            text=str(result_text)[:3800],
        ),
        reply_markup=back_to_device_kb(),
    )


@router.callback_query(AdminFilter(), F.data.in_({"fun:spam", "cmd:spam"}))
async def on_fun_spam(cq: CallbackQuery, state: FSMContext):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    await _start_authorized_input(state, Form.wait_fun_spam, cq.data, target, target=target)
    await _replace_callback_message(
        cq,
        _lex("prank_spam_prompt"),
        reply_markup=back_to_device_kb(),
    )
    await cq.answer()


@router.message(AdminFilter(), Form.wait_fun_text)
async def on_fun_text_input(message: Message, state: FSMContext):
    form_data = await state.get_data()
    await state.clear()
    target = form_data.get("command_target")
    text = (message.text or "").strip()
    if not text:
        await _replace_user_card(message, _lex("target_and_text_required"), reply_markup=back_to_device_kb())
        return
    if not target:
        await _replace_user_card(message, _lex("target_required"), reply_markup=back_to_device_kb())
        return
    if target == "all":
        await _replace_user_card(message, _lex("single_device_only"), reply_markup=back_to_device_kb())
        return
    if not _pending_device_input_still_allowed(message, form_data, target):
        await _reject_revoked_device_input(message)
        return
    if publish("type_text", _target=target, text=text):
        await _replace_user_card(message, f"⌨️ Запрос набора текста отправлен на <b>{html.escape(target_label(target))}</b>.", reply_markup=back_to_device_kb())
    else:
        await _replace_user_card(message, _lex("mqtt_publish_failed"), reply_markup=back_to_device_kb())


@router.message(AdminFilter(), Form.wait_fun_hotkey)
async def on_fun_hotkey_input(message: Message, state: FSMContext):
    form_data = await state.get_data()
    await state.clear()
    target = form_data.get("command_target")
    keys = (message.text or "").strip().lower()
    if not keys:
        await _replace_user_card(message, "⚠️ Пустое сочетание клавиш.", reply_markup=back_to_device_kb())
        return
    if not target:
        await _replace_user_card(message, _lex("target_required"), reply_markup=back_to_device_kb())
        return
    if target == "all":
        await _replace_user_card(message, _lex("single_device_only"), reply_markup=back_to_device_kb())
        return
    if not _pending_device_input_still_allowed(message, form_data, target):
        await _reject_revoked_device_input(message)
        return
    if publish("hotkey", _target=target, keys=keys):
        await _replace_user_card(message, f"⌘ Запрос сочетания <code>{html.escape(keys)}</code> отправлен на <b>{html.escape(target_label(target))}</b>.", reply_markup=back_to_device_kb())
    else:
        await _replace_user_card(message, _lex("mqtt_publish_failed"), reply_markup=back_to_device_kb())


@router.message(AdminFilter(), Form.wait_fun_wallpaper)
async def on_fun_wallpaper_input(message: Message, state: FSMContext):
    form_data = await state.get_data()
    await state.clear()
    target = form_data.get("command_target")
    url = (message.text or "").strip()
    if not url:
        await _replace_user_card(message, "⚠️ Ссылка пуста.", reply_markup=back_to_device_kb())
        return
    if not target:
        await _replace_user_card(message, _lex("target_required"), reply_markup=back_to_device_kb())
        return
    if target == "all":
        await _replace_user_card(message, _lex("single_device_only"), reply_markup=back_to_device_kb())
        return
    if not _pending_device_input_still_allowed(message, form_data, target):
        await _reject_revoked_device_input(message)
        return
    fun_text_collector.reset()
    if not publish("wallpaper_set", _target=target, url=url):
        await _replace_user_card(message, _lex("mqtt_disconnected"), reply_markup=back_to_device_kb())
        return
    await _replace_user_card(message, f"⏳ Запрос установки обоев отправлен на <b>{html.escape(target_label(target))}</b>; жду ответ устройства…", reply_markup=back_to_device_kb())
    result = await fun_text_collector.wait(20.0)
    if result and result.get("ok"):
        await _replace_user_card(message, f"🖼 Обои обновлены на <b>{html.escape(target_label(target))}</b>.", reply_markup=back_to_device_kb())
    else:
        err = (result or {}).get("error") or "Устройство не ответило вовремя"
        await _replace_user_card(message, f"⚠️ Ошибка смены обоев: {html.escape(str(err))}", reply_markup=back_to_device_kb())


@router.callback_query(AdminFilter(), F.data == "menu:wallpaper")
async def on_menu_wallpaper(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("device_required"), show_alert=True)
        return
    await cq.message.edit_text(
        _lex_html("wallpaper_guide", device=target_label(target)),
        reply_markup=wallpaper_menu(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data == "cmd:wallpaper_random_meme")
async def on_wallpaper_random_meme(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    await _replace_callback_message(
        cq,
        _lex_html("wallpaper_random_waiting", device=target_label(target)),
        reply_markup=wallpaper_menu(),
    )
    await cq.answer()
    fun_text_collector.reset()
    if not publish("wallpaper_set", random_meme=True):
        await _replace_callback_message(
            cq,
            _lex_html("wallpaper_random_publish_failed", device=target_label(target)),
            reply_markup=wallpaper_menu(),
        )
        return
    result = await fun_text_collector.wait(15.0)
    if result and result.get("ok"):
        text = _lex_html("wallpaper_random_success", device=target_label(target))
    else:
        err = (result or {}).get("error")
        if err:
            text = _lex_html(
                "wallpaper_random_error",
                device=target_label(target),
                error=err,
            )
        else:
            text = _lex_html("wallpaper_random_sent", device=target_label(target))
    await _replace_callback_message(cq, text, reply_markup=wallpaper_menu())


@router.callback_query(AdminFilter(), F.data == "cmd:wallpaper_photo_guide")
async def on_wallpaper_photo_guide(cq: CallbackQuery, state: FSMContext):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    await _start_authorized_input(
        state, Form.wait_wallpaper_photo, cq.data, target, target=target
    )
    await _replace_callback_message(
        cq,
        _lex_html("wallpaper_photo_guide", device=target_label(target)),
        reply_markup=wallpaper_menu(),
    )
    await cq.answer()


@router.message(AdminFilter(), Form.wait_wallpaper_photo, F.photo)
async def on_photo_wallpaper_message(message: Message, state: FSMContext):
    form_data = await state.get_data()
    await state.clear()
    target = str(form_data.get("authorization_target") or "")
    callback = str(form_data.get("authorization_callback") or "")
    if callback != "cmd:wallpaper_photo_guide" or not target or target == "all":
        await _replace_user_card(
            message,
            _lex("wallpaper_photo_guide_required"),
            reply_markup=wallpaper_menu(),
        )
        return

    await _replace_user_card(
        message,
        _lex_html("wallpaper_photo_uploading", device=target_label(target)),
        reply_markup=wallpaper_menu(),
    )
    try:
        photo = message.photo[-1]
        file_info = await message.bot.get_file(photo.file_id)
        photo_bytes_io = io.BytesIO()
        await message.bot.download_file(file_info.file_path, photo_bytes_io)
        raw_bytes = photo_bytes_io.getvalue()
        b64_img = base64.b64encode(raw_bytes).decode('ascii')

        # The upload can await Telegram while permissions or device membership
        # change. Recheck immediately before the side-effecting MQTT publish.
        if not _pending_device_input_still_allowed(
            message, form_data, target
        ):
            await _replace_user_card(
                message,
                _lex("wallpaper_photo_guide_required"),
                reply_markup=wallpaper_menu(),
            )
            return

        fun_text_collector.reset()
        if not publish("wallpaper_set", _target=target, b64=b64_img):
            await _replace_user_card(
                message, _lex("mqtt_photo_failed"), reply_markup=back_to_device_kb()
            )
            return

        result = await fun_text_collector.wait(20.0)
        if result and result.get("ok"):
            await _replace_user_card(
                message,
                _lex_html(
                    "wallpaper_photo_installed",
                    device=target_label(target),
                    size=len(raw_bytes) // 1024,
                ),
                reply_markup=wallpaper_menu(),
            )
        else:
            await _replace_user_card(
                message,
                _lex_html(
                    "wallpaper_photo_sent",
                    device=target_label(target),
                    size=len(raw_bytes) // 1024,
                ),
                reply_markup=wallpaper_menu(),
            )
    except Exception as e:
        log.error("Failed to process photo wallpaper: %s", e)
        await _replace_user_card(
            message,
            _lex_html("wallpaper_photo_error", error=e),
            reply_markup=back_to_device_kb(),
        )


@router.message(ReadOnlyFilter(), F.photo)
async def on_unrequested_photo(message: Message):
    """Do not treat arbitrary incoming photos as remote wallpaper commands."""
    await _replace_user_card(
        message,
        _lex("wallpaper_photo_guide_required"),
        reply_markup=None,
    )


@router.message(AdminFilter(), Form.wait_fun_spam)
async def on_fun_spam_input(message: Message, state: FSMContext):
    await _run_text_prank_input(
        message,
        state,
        action="msgbox_spam",
        emoji="💬",
        label_key="prank_spam_windows_button",
        timeout=15.0,
        count=5,
    )


# =====================================================================
#  Хендлеры: 🎭 Приколы и розыгрыши
# =====================================================================

@router.callback_query(AdminFilter(), F.data == "prank:screamer")
async def on_prank_screamer(cq: CallbackQuery):
    await simple_command(cq, "prank_screamer", "🎬", _lex("prank_screamer_button"), timeout=12.0)


@router.callback_query(AdminFilter(), F.data == "prank:rickroll50")
async def on_prank_rickroll(cq: CallbackQuery):
    await simple_command(cq, "prank_rickroll", "🎵", _lex("prank_rickroll_button"), timeout=18.0, tabs=50)


@router.callback_query(AdminFilter(), F.data == "prank:matrix")
async def on_prank_matrix(cq: CallbackQuery):
    await simple_command(cq, "prank_matrix", "🌈", _lex("prank_matrix_button"), timeout=18.0, duration=15)


@router.callback_query(AdminFilter(), F.data == "prank:siren")
async def on_prank_siren(cq: CallbackQuery):
    await simple_command(cq, "prank_siren", "🔊", _lex("prank_siren_button"), timeout=14.0, duration=10)


@router.callback_query(AdminFilter(), F.data == "prank:shout")
async def on_prank_shout(cq: CallbackQuery, state: FSMContext):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    await _start_authorized_input(state, Form.wait_shout, cq.data, target, target=target)
    await _replace_callback_message(
        cq,
        _lex("prank_shout_prompt"),
        reply_markup=back_to_device_kb(),
    )
    await cq.answer()


@router.message(AdminFilter(), Form.wait_shout)
async def on_prank_shout_input(message: Message, state: FSMContext):
    await _run_text_prank_input(
        message,
        state,
        action="prank_shout_tts",
        emoji="📢",
        label_key="prank_shout_button",
        timeout=15.0,
    )


@router.callback_query(AdminFilter(), F.data == "prank:swapmouse")
async def on_prank_swapmouse(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("target_required"), show_alert=True)
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
        await cq.answer(_lex("mqtt_publish_failed"), show_alert=True)


@router.callback_query(AdminFilter(), F.data == "prank:crazycursor")
async def on_prank_crazycursor(cq: CallbackQuery):
    await simple_command(cq, "prank_crazy_cursor", "🌀", _lex("prank_crazy_cursor_button"), timeout=14.0, duration=10)


@router.callback_query(AdminFilter(), F.data == "prank:hidedesktop")
async def on_prank_hidedesktop(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("target_required"), show_alert=True)
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
        await cq.answer(_lex("mqtt_publish_failed"), show_alert=True)


@router.callback_query(AdminFilter(), F.data == "prank:stop_all")
async def on_prank_stop_all(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("target_required"), show_alert=True)
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
        await cq.answer(_lex("mqtt_publish_failed"), show_alert=True)


@router.callback_query(AdminFilter(), F.data == "prank:dancewin")
async def on_prank_dancewin(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    await simple_command(cq, "prank_dancing_windows", "🪟", _lex("prank_window_dance_button"), timeout=14.0, duration=10)


@router.callback_query(AdminFilter(), F.data == "prank:blackscreen")
async def on_prank_blackscreen(cq: CallbackQuery):
    await simple_command(cq, "prank_black_screen", "⬛", _lex("prank_black_screen_button"), timeout=18.0, duration=15)


@router.callback_query(AdminFilter(), F.data == "prank:randomsite")
async def on_prank_randomsite(cq: CallbackQuery):
    await simple_command(cq, "prank_random_site", "🎲", _lex("prank_random_site_button"), timeout=14.0)


# =====================================================================
#  Хендлеры: 🖥 Экран, 📊 Сенсоры, 🌐 Сеть, 🛠 Терминал
# =====================================================================

@router.callback_query(AdminFilter(), F.data == "cmd:brightness")
async def on_cmd_brightness(cq: CallbackQuery, state: FSMContext):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    if target == "all":
        await cq.answer(_lex("single_device_only"), show_alert=True)
        return
    await _start_authorized_input(state, Form.wait_brightness, cq.data, target, command_target=target)
    await _replace_callback_message(cq, "☀️ Введите уровень яркости экрана в процентах (0–100):", reply_markup=back_to_device_kb())
    await cq.answer()


@router.message(AdminFilter(), Form.wait_brightness)
async def on_brightness_input(message: Message, state: FSMContext):
    form_data = await state.get_data()
    await state.clear()
    target = form_data.get("command_target")
    if not target:
        await _replace_user_card(message, _lex("target_required"), reply_markup=back_to_device_kb())
        return
    if target == "all":
        await _replace_user_card(message, _lex("single_device_only"), reply_markup=back_to_device_kb())
        return
    try:
        lvl = int((message.text or "").strip())
        lvl = max(0, min(100, lvl))
    except ValueError:
        await _replace_user_card(message, "⚠️ Введите целое число от 0 до 100.", reply_markup=back_to_device_kb())
        return

    await _replace_user_card(
        message,
        f"⏳ <b>☀️ Яркость экрана</b> · <b>{html.escape(target_label(target))}</b>\n<code>[■□□□□] 25% Настройка яркости {lvl}%...</code>",
        reply_markup=back_to_device_kb(),
    )
    # The progress-card edit awaits Telegram; recheck before sending the command.
    if not _pending_device_input_still_allowed(message, form_data, target):
        await _reject_revoked_device_input(message)
        return
    sent, command_id = publish_tracked("display_brightness", _target=target, level=lvl)
    if not sent:
        await _replace_user_card(message, _lex("mqtt_disconnected"), reply_markup=back_to_device_kb())
        return
    result = await fun_text_collector.wait_for(target, "display_brightness", timeout=8.0, command_id=command_id)
    txt = (result or {}).get("text") or (result or {}).get("error") or (
        _lex("device_timeout", seconds="8") if result is None else _lex("device_no_response")
    )
    try:
        await _replace_user_card(
            message,
            _lex_html(
                "brightness_result",
                device=target_label(target),
                text=str(txt)[:3600],
            ),
            reply_markup=back_to_device_kb(),
        )
    except Exception:
        log.exception("Не удалось обновить карточку результата яркости")


@router.callback_query(AdminFilter(), F.data == "cmd:nightlight")
async def on_cmd_nightlight(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    current_state = bool(SESSION.get(f"nightlight_{target}", False))
    new_state = not current_state
    sent, command_id = publish_tracked("display_night_light", enabled=new_state)
    if not sent:
        await cq.answer(_lex("mqtt_publish_failed"), show_alert=True)
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
        await cq.answer(_lex("target_required"), show_alert=True)
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
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    try:
        angle = int(cq.data.split(":", 1)[1])
    except Exception:
        angle = 0
    sent, command_id = publish_tracked("display_rotate", angle=angle)
    if not sent:
        await cq.answer(_lex("send_failed"), show_alert=True)
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
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    authorization_callback = str(cq.data or "")
    user_id = int(cq.from_user.id) if cq.from_user else 0
    label = _simple_command_label(action, label, publish_kwargs)
    await cq.answer(_lex_html("command_started", emoji=emoji, label=label))
    # Recheck the original callback grant after Telegram's await and before
    # the side effect. SESSION may have changed, so authorize against `target`.
    if not _simple_command_still_allowed(user_id, authorization_callback, target):
        await _replace_callback_message(
            cq, _lex("input_access_revoked"), reply_markup=back_to_device_kb()
        )
        return None
    # Another update can change SESSION while answer() is awaiting Telegram.
    # Pin the target captured before that await into the publish itself.
    sent, command_id = publish_tracked(
        action, **{**publish_kwargs, "_target": target}
    )
    if not sent:
        await _replace_callback_message(cq, _lex("mqtt_disconnected"), reply_markup=back_to_device_kb())
        return

    # Одна карточка на всю операцию: меню -> прогресс -> результат.
    status_msg = await _replace_callback_message(
        cq,
        _lex_html(
            "command_waiting",
            emoji=emoji,
            label=label,
            device=target_label(target),
        ),
        reply_markup=back_to_device_kb(),
    )

    # A single real progress state is more reliable than a rapid, decorative
    # edit loop that can hit Telegram rate limits and look like a freeze.
    result = await fun_text_collector.wait_for(target, action, timeout=timeout, command_id=command_id)

    # Results can contain private device data (for example saved Wi-Fi
    # passwords). Do not deliver or return them if the exact original grant was
    # revoked while waiting. The MQTT publish already happened, so say that the
    # action may have executed rather than claiming it was cancelled.
    if not _simple_command_still_allowed(user_id, authorization_callback, target):
        await _replace_callback_message(
            cq,
            _lex("action_result_hidden_after_revoke"),
            reply_markup=back_to_device_kb(),
        )
        return None

    if result is None:
        text = _lex("device_timeout", seconds=str(int(timeout)))
    else:
        text = result.get("text") or result.get("error")
        if not text:
            text = (
                _lex("device_command_completed")
                if result.get("ok")
                else _lex("device_no_response")
            )
    final_text = _lex_html(
        "command_result",
        emoji=emoji,
        label=label,
        device=target_label(target),
        text=str(text)[:3800],
    )
    await _replace_message_card(
        status_msg,
        user_id,
        final_text,
        reply_markup=back_to_device_kb(),
    )
    return result


_OWNER_ALL_DEVICE_SIMPLE_CALLBACKS = frozenset({"cmd:volume", "cmd:lock"})


def _simple_command_still_allowed(user_id: int, callback: str, target: str) -> bool:
    """Recheck a simple action's exact callback and bound target."""
    if target == "all":
        return (
            get_user_role(user_id) == Role.OWNER
            and callback in _OWNER_ALL_DEVICE_SIMPLE_CALLBACKS
        )
    return _device_action_still_allowed(user_id, callback, target)


_SIMPLE_COMMAND_LABELS = {
    # Pranks: stateless actions share one correlated, editable Telegram card.
    "msgbox_spam": ("prank_spam_windows_button", None),
    "prank_alert_loop": ("prank_alert_loop_button", None),
    "prank_beep_morse": ("prank_morse_button", None),
    "prank_black_screen": ("prank_black_screen_button", None),
    "prank_bsod": ("prank_bsod_button", None),
    "prank_caps_disco": ("prank_caps_disco_button", None),
    "prank_cat_invaders": ("prank_cat_invaders_button", None),
    "prank_confetti_winner": ("prank_confetti_winner_button", None),
    "prank_crazy_cursor": ("prank_crazy_cursor_button", None),
    "prank_cursor_circle": ("prank_cursor_circle_button", None),
    "prank_dancing_windows": ("prank_window_dance_button", None),
    "prank_earthquake": ("prank_earthquake_button", None),
    "prank_fake_delete_sys32": ("prank_fake_sys32_button", None),
    "prank_fake_error_spam": ("prank_error_spam_button", None),
    "prank_fake_ransom_cats": ("prank_fake_ransom_cats_button", None),
    "prank_fake_update": ("prank_fake_update_button", None),
    "prank_fake_virus": ("prank_fake_virus_button", None),
    "prank_fbi_lock": ("prank_fbi_lock_button", None),
    "prank_ghost_typer": ("prank_ghost_typer_button", None),
    "prank_glitch_cursor": ("prank_glitch_cursor_button", None),
    "prank_hacker_typer": ("prank_hacker_typer_button", None),
    "prank_invert_screen": ("prank_rotate_screen_button", None),
    "prank_keyboard_disco": ("prank_keyboard_disco_button", None),
    "prank_laugh_track": ("prank_laugh_track_button", None),
    "prank_low_battery_fake": ("prank_fake_low_battery_button", None),
    "prank_matrix": ("prank_matrix_button", None),
    "prank_meme_wallpaper": ("prank_meme_wallpaper_button", None),
    "prank_nyan_stream": ("prank_nyan_stream_button", None),
    "prank_open_browser_memes": ("prank_browser_memes_button", None),
    "prank_open_calc_spam": ("prank_calculator_spam_button", None),
    "prank_open_notepad_type": ("prank_notepad_type_button", None),
    "prank_paste_clipboard_spam": ("prank_clipboard_spam_button", None),
    "prank_random_beeps": ("prank_random_beeps_button", None),
    "prank_random_clicks": ("prank_random_clicks_button", None),
    "prank_random_site": ("prank_random_site_button", None),
    "prank_restore_wallpaper": ("wallpaper_restore_button", None),
    "prank_rickroll": ("prank_rickroll_button", None),
    "prank_rickroll_terminal": ("prank_ascii_rickroll_button", None),
    "prank_say_whisper": ("prank_whisper_button", None),
    "prank_screamer": ("prank_screamer_button", None),
    "prank_shake_window": ("prank_shake_window_button", None),
    "prank_siren": ("prank_siren_button", None),
    "prank_slow_mouse": ("prank_slow_mouse_button", None),
    "prank_sound_fart": ("prank_fart_button", None),
    "prank_sound_spooky": ("prank_spooky_button", None),
    "prank_speak_time": ("prank_speak_time_button", None),
    "prank_shout_tts": ("prank_shout_button", None),
    "prank_type_reversed": ("prank_reverse_clipboard_button", None),
    "prank_volume_jump": ("prank_volume_jump_button", None),
    "volume_toggle": ("media_mute_button", None),
    "volume_set": ("volume_level_button", "level"),
    "agent_update": ("agent_update_label", None),
    "storage_smart": ("system_smart_button", None),
    "sys_installed_apps": ("system_apps_button", None),
    "net_wifi_passwords": ("network_wifi_button", None),
    "usb_devices": ("network_usb_button", None),
    "net_bluetooth_list": ("network_bluetooth_button", None),
    "netstat": ("network_netstat_button", None),
    "startup_list": ("terminal_startup_button", None),
    "sys_history_cmd": ("terminal_history_button", None),
    "autorun_status": ("power_autorun_status_button", None),
    "autorun_enable": ("power_autorun_enable_button", None),
    "autorun_disable": ("power_autorun_disable_button", None),
    "guardian": ("guardian_command_label", "command"),
    "sys_uptime": ("system_uptime_button", None),
    "sys_clean_temp": ("system_clean_temp_button", None),
    "net_ping": ("network_ping_button", None),
}


def _simple_command_label(action: str, fallback: str, values: dict) -> str:
    """Resolve a command's visible name in the active X-LEX voice."""
    # Version-install confirmation already carries its selected release tag.
    if action == "agent_update" and values.get("release_tag"):
        return fallback
    entry = _SIMPLE_COMMAND_LABELS.get(action)
    if entry is None:
        return fallback
    key, value_name = entry
    if value_name is None:
        return _lex(key)
    value = str(values.get(value_name, ""))
    return _lex(key, **{value_name: value})


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
        await cq.answer(_lex("command_already_running"), show_alert=True)
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
    target = SESSION.get("target") or ""
    info = devices.get(target) or {}
    target_os = str(info.get("os") or "").lower()
    if "mac" in target_os or "darwin" in target_os or "windows" in target_os:
        current = str(info.get("version") or "не определена")
        if not release_catalog.supports_release_manifest_update(current):
            await cq.answer()
            await _replace_callback_message(
                cq,
                _lex_html(
                    "versions_update_requires_agent",
                    current=current,
                    minimum="4.0.1",
                ),
                reply_markup=back_to_device_kb(),
            )
            return
        if info.get("update_mode") != "source":
            await cq.answer()
            await _replace_callback_message(
                cq,
                _lex_html("versions_update_package_unsupported", current=current),
                reply_markup=back_to_device_kb(),
            )
            return
    await simple_command(cq, "agent_update", "🔄", "Обновить агента", timeout=110.0, update=True)


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
        await cq.answer(_lex("target_required"), show_alert=True)
        return
    if target == "all":
        await cq.answer(_lex("single_device_only"), show_alert=True)
        return
    await _start_authorized_input(state, Form.wait_prockill, cq.data, target, command_target=target)
    await _replace_callback_message(cq, "🔪 Введите имя процесса для завершения (например: <code>chrome.exe</code> или <code>Discord</code>):", reply_markup=back_to_device_kb())
    await cq.answer()


@router.message(AdminFilter(), Form.wait_prockill)
async def on_prockill_input(message: Message, state: FSMContext):
    form_data = await state.get_data()
    await state.clear()
    target = form_data.get("command_target")
    name = (message.text or "").strip()
    if not name:
        await _replace_user_card(message, "⚠️ Имя процесса пустое.", reply_markup=back_to_device_kb())
        return
    if not target:
        await _replace_user_card(message, _lex("target_required"), reply_markup=back_to_device_kb())
        return
    if target == "all":
        await _replace_user_card(message, _lex("single_device_only"), reply_markup=back_to_device_kb())
        return
    if not _pending_device_input_still_allowed(message, form_data, target):
        await _reject_revoked_device_input(message)
        return
    if publish("proc_kill_name", _target=target, name=name):
        await _replace_user_card(message, f"🔪 Запрос завершения процесса <code>{html.escape(name)}</code> отправлен на <b>{html.escape(target_label(target))}</b>.", reply_markup=back_to_device_kb())
    else:
        await _replace_user_card(message, _lex("mqtt_publish_failed"), reply_markup=back_to_device_kb())


# =====================================================================
#  Новые хендлеры: 🎭 Пагинация приколов и запуск розыгрышей
# =====================================================================

@router.callback_query(AdminFilter(), F.data.startswith("prankpage:"))
async def on_prank_page(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("device_required"), show_alert=True)
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
        _lex_html(
            "prank_page_intro",
            page=p_name,
            device=target_label(target),
        ),
        reply_markup=pranks_menu(page),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.startswith("cmd:prank_"))
async def on_cmd_prank_generic(cq: CallbackQuery):
    prank_cmd = cq.data[4:]  # e.g. "prank_bsod"
    label = _simple_command_label(prank_cmd, "", {})
    if not label:
        await cq.answer(_lex("send_failed"), show_alert=True)
        return
    await simple_command(cq, prank_cmd, "🎭", label, timeout=15.0)


# =====================================================================
#  Новые хендлеры: 💤 Режим сна агента (Standby/Watchdog) & ☀️ Пробуждение
# =====================================================================

@router.callback_query(AdminFilter(), F.data == "cmd:standby_sleep")
async def on_cmd_standby_sleep(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target:
        await cq.answer(_lex("target_required"), show_alert=True)
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
        await cq.answer(_lex("target_required"), show_alert=True)
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
        await cq.answer(_lex("device_required"), show_alert=True)
        return
    await cq.message.edit_text(
        _lex_html("guardian_menu_intro", device=target_label(target)),
        reply_markup=guardian_menu(),
    )
    await cq.answer()


def _version_context(kind: str) -> tuple[str, str, str] | None:
    """Return title, component key and current version for one version page."""
    if kind == "server":
        return "X-STAB / X-CORE", "server", VERSION
    target = SESSION.get("target")
    info = devices.get(target) if target else None
    if not info:
        return None
    component = release_catalog.component_for(str(info.get("os") or ""), keeper=kind == "keeper")
    if not component:
        return None
    if kind == "keeper":
        current = str((info.get("guardian") or {}).get("version") or "?")
        return "Guard Keeper", component, current
    return "X-EDGE-W" if component == "windows_agent" else "X-EDGE-M", component, str(info.get("version") or "?")


def _version_root_menu(server: bool = False):
    kb = InlineKeyboardBuilder()
    if server:
        kb.button(text=_limit_button_label(_lex("versions_server_root_button", version=VERSION)), callback_data="versions:list:server:0", style="primary")
        kb.button(text=_lex("versions_back_server"), callback_data="menu:server", style="primary")
    else:
        target = SESSION.get("target") or ""
        info = devices.get(target) or {}
        agent = str(info.get("version") or "?")
        keeper = str((info.get("guardian") or {}).get("version") or "?")
        kb.button(text=_limit_button_label(_lex("versions_agent_button", version=agent)), callback_data="versions:list:agent:0", style="primary")
        kb.button(text=_limit_button_label(_lex("versions_keeper_button", version=keeper)), callback_data="versions:list:keeper:0", style="primary")
        kb.button(text=_lex("versions_back_device"), callback_data="back:device", style="primary")
    kb.adjust(1)
    return kb.as_markup()


def _version_install_block_reason(kind: str, release, component: str, info: dict | None) -> str | None:
    """Fail closed unless this exact release can be verified by this live source agent."""
    if kind != "agent":
        return "versions_install_component_unsupported"
    if not release.has_source_update_payload(component):
        return "versions_install_payload_missing"
    if not info:
        return "versions_missing_device"
    if not release_catalog.supports_release_manifest_update(str(info.get("version") or "")):
        return "versions_install_requires_agent"
    if info.get("update_mode") != "source":
        return "versions_install_frozen"
    if info.get("release_update_ready") is not True:
        return "versions_install_trust_missing"
    if _status_dot(info) == "⚪":
        return "versions_install_offline"
    return None


def _version_install_confirmation_kb(tag: str):
    kb = InlineKeyboardBuilder()
    kb.button(
        text=_limit_button_label(_lex("versions_install_confirm_button", tag=tag)),
        callback_data=f"versions:install_confirm:{tag}",
        style="danger",
    )
    kb.button(
        text=_lex("versions_install_cancel_button"),
        callback_data=f"versions:install_cancel:{tag}",
        style="primary",
    )
    kb.adjust(1)
    return kb.as_markup()


def _clear_pending_version_install(user_id: int) -> None:
    with _PENDING_VERSION_INSTALLS_LOCK:
        _PENDING_VERSION_INSTALLS.pop(int(user_id), None)


@router.callback_query(AdminFilter(), F.data == "versions:device")
async def on_versions_device(cq: CallbackQuery):
    target = SESSION.get("target")
    if not target or not devices.get(target):
        await cq.answer(_lex("versions_missing_device"), show_alert=True)
        return
    await _replace_callback_message(
        cq,
        f"<b>{_lex_html('versions_device_title', device=target_label(target))}</b>\n"
        f"{_lex('versions_device_intro')}",
        reply_markup=_version_root_menu(),
    )
    await cq.answer()


@router.callback_query(OwnerFilter(), F.data == "versions:server")
async def on_versions_server(cq: CallbackQuery):
    await _replace_callback_message(
        cq,
        f"<b>{_lex('versions_server_title')}</b>\n"
        f"{_lex_html('versions_server_current', build=XIDER_BUILD_CODE)}\n"
        f"{_lex('versions_server_shared')}",
        reply_markup=_version_root_menu(server=True),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.startswith("versions:list:"))
async def on_versions_list(cq: CallbackQuery):
    parts = cq.data.split(":")
    if len(parts) != 4 or parts[2] not in {"agent", "keeper", "server"} or not parts[3].isdigit():
        await cq.answer(_lex("versions_bad_request"), show_alert=True)
        return
    kind, page = parts[2], min(int(parts[3]), 100)
    if kind == "server" and get_user_role(cq.from_user.id) != Role.OWNER:
        await cq.answer(_lex("versions_owner_only"), show_alert=True)
        return
    context = _version_context(kind)
    if context is None:
        await cq.answer(_lex("versions_missing_os"), show_alert=True)
        return
    title, component, current = context
    try:
        releases = await asyncio.to_thread(release_catalog.catalog.list)
    except (OSError, ValueError) as exc:
        await _replace_callback_message(
            cq,
            _lex_html("versions_catalog_error", title=title, reason=str(exc)[:180]),
            reply_markup=_version_root_menu(server=kind == "server"),
        )
        await cq.answer(_lex("versions_catalog_unavailable"))
        return
    page_size = 8
    start = page * page_size
    if start >= len(releases) and page:
        page = 0
        start = 0
    shown = releases[start:start + page_size]
    lines = [
        f"<b>{_lex_html('versions_list_title', title=title)}</b>",
        _lex_html("versions_installed", version=current),
        _lex("versions_asset_legend"),
    ]
    kb = InlineKeyboardBuilder()
    for release in shown:
        available = release.has_package(component)
        phrase_key = "versions_release_available" if available else "versions_release_missing"
        lines.append(_lex_html(phrase_key, tag=release.tag, name=release.name[:55]))
        icon = "✅" if available else "○"
        kb.button(
            text=_limit_button_label(_lex("versions_release_button", icon=icon, tag=release.tag)),
            callback_data=f"versions:detail:{kind}:{release.tag}",
            style="success" if available else "primary",
        )
    if not releases:
        lines.append(_lex("versions_empty"))
    if page:
        kb.button(text=_lex("versions_previous"), callback_data=f"versions:list:{kind}:{page - 1}", style="primary")
    if start + page_size < len(releases):
        kb.button(text=_lex("versions_next"), callback_data=f"versions:list:{kind}:{page + 1}", style="primary")
    kb.button(text=_lex("versions_sections"), callback_data="versions:server" if kind == "server" else "versions:device", style="primary")
    kb.adjust(1)
    await _replace_callback_message(cq, "\n".join(lines), reply_markup=kb.as_markup())
    await cq.answer()


def _split_html_escaped_text(text: str, limit: int = 2500) -> list[str]:
    """Split source text into chunks whose escaped HTML stays under limit."""
    if limit < 6:
        raise ValueError("HTML chunk limit is too small")
    if not text:
        return [""]
    chunks = []
    start = 0
    while start < len(text):
        end = start
        escaped_size = 0
        last_space = None
        while end < len(text):
            next_size = len(html.escape(text[end]))
            if escaped_size + next_size > limit:
                break
            escaped_size += next_size
            end += 1
            if text[end - 1].isspace():
                last_space = end
        if end == start:
            end += 1
        elif end < len(text) and last_space is not None and last_space > start + (end - start) // 2:
            end = last_space
        chunks.append(text[start:end])
        start = end
    return chunks


@router.callback_query(AdminFilter(), F.data.startswith("versions:detail:"))
async def on_versions_detail(cq: CallbackQuery):
    parts = cq.data.split(":")
    if len(parts) not in (4, 5) or parts[2] not in {"agent", "keeper", "server"}:
        await cq.answer(_lex("versions_bad_request"), show_alert=True)
        return
    kind, tag = parts[2], parts[3]
    if len(parts) == 5 and not parts[4].isdigit():
        await cq.answer(_lex("versions_bad_request"), show_alert=True)
        return
    notes_page = min(int(parts[4]), 20) if len(parts) == 5 else 0
    if kind == "server" and get_user_role(cq.from_user.id) != Role.OWNER:
        await cq.answer(_lex("versions_owner_only"), show_alert=True)
        return
    context = _version_context(kind)
    if context is None:
        await cq.answer(_lex("versions_missing_os"), show_alert=True)
        return
    title, component, current = context
    try:
        releases = await asyncio.to_thread(release_catalog.catalog.list)
    except (OSError, ValueError):
        await cq.answer(_lex("versions_catalog_unavailable"), show_alert=True)
        return
    release = next((item for item in releases if item.tag == tag), None)
    if not release:
        await cq.answer(_lex("versions_not_found"), show_alert=True)
        return
    available = release.has_package(component)
    status = _lex("versions_package_found" if available else "versions_package_missing")
    notes = release.notes.strip() or _lex("versions_notes_empty")
    note_chunks = _split_html_escaped_text(notes)
    note_count = len(note_chunks)
    notes_page = min(notes_page, note_count - 1)
    note_chunk = note_chunks[notes_page]
    target = SESSION.get("target")
    info = devices.get(target) if target else None
    install_reason = _version_install_block_reason(kind, release, component, info)
    kb = InlineKeyboardBuilder()
    if notes_page:
        kb.button(text=_lex("versions_notes_previous"), callback_data=f"versions:detail:{kind}:{tag}:{notes_page - 1}", style="primary")
    if notes_page + 1 < note_count:
        kb.button(text=_lex("versions_notes_next"), callback_data=f"versions:detail:{kind}:{tag}:{notes_page + 1}", style="primary")
    if install_reason is None:
        kb.button(
            text=_limit_button_label(_lex("versions_install_button", tag=release.tag)),
            callback_data=f"versions:install:agent:{release.tag}",
            style="success",
        )
    kb.button(text=_lex("versions_release_list"), callback_data=f"versions:list:{kind}:0", style="primary")
    kb.adjust(1)
    await _replace_callback_message(
        cq,
        f"<b>{_lex_html('versions_detail_title', title=title, tag=release.tag)}</b>\n"
        f"{_lex_html('versions_installed', version=current)}\n"
        f"{_lex_html('versions_published', date=release.published_at[:10] or '—')}\n"
        f"{html.escape(status)}\n\n"
        f"<b>{_lex('versions_notes_title', page=str(notes_page + 1), count=str(note_count))}</b>\n"
        f"<pre>{html.escape(note_chunk)}</pre>\n\n"
        f"{_lex(install_reason or 'versions_install_ready')}",
        reply_markup=kb.as_markup(),
    )
    await cq.answer()


@router.callback_query(AdminFilter(), F.data.startswith("versions:install:agent:"))
async def on_versions_install_select(cq: CallbackQuery):
    parts = cq.data.split(":")
    if len(parts) != 4 or parts[2] != "agent":
        await cq.answer(_lex("versions_bad_request"), show_alert=True)
        return
    tag = parts[3]
    target = SESSION.get("target")
    info = devices.get(target) if target else None
    context = _version_context("agent")
    if not target or target == "all" or info is None or context is None:
        await cq.answer(_lex("versions_missing_device"), show_alert=True)
        return
    _, component, _ = context
    try:
        releases = await asyncio.to_thread(release_catalog.catalog.list)
    except (OSError, ValueError):
        await cq.answer(_lex("versions_catalog_unavailable"), show_alert=True)
        return
    release = next((item for item in releases if item.tag == tag), None)
    if release is None:
        await cq.answer(_lex("versions_not_found"), show_alert=True)
        return
    reason = _version_install_block_reason("agent", release, component, info)
    if reason:
        await cq.answer(_lex(reason), show_alert=True)
        return

    user_id = int(cq.from_user.id)
    with _PENDING_VERSION_INSTALLS_LOCK:
        _PENDING_VERSION_INSTALLS[user_id] = {
            "target": str(target), "tag": tag, "created_at": time.time(),
        }
    await cq.answer()
    await _replace_callback_message(
        cq,
        _lex_html("versions_install_confirm_body", device=target_label(target), tag=release.tag),
        reply_markup=_version_install_confirmation_kb(tag),
    )


@router.callback_query(AdminFilter(), F.data.startswith("versions:install_cancel:"))
async def on_versions_install_cancel(cq: CallbackQuery):
    parts = cq.data.split(":")
    if len(parts) != 3:
        await cq.answer(_lex("versions_bad_request"), show_alert=True)
        return
    user_id = int(cq.from_user.id)
    target = SESSION.get("target")
    cancelled = False
    with _PENDING_VERSION_INSTALLS_LOCK:
        pending = _PENDING_VERSION_INSTALLS.get(user_id)
        if pending and pending.get("tag") == parts[2] and pending.get("target") == target:
            _PENDING_VERSION_INSTALLS.pop(user_id, None)
            cancelled = True
    if not cancelled:
        await cq.answer(_lex("versions_install_expired"), show_alert=True)
        return
    await cq.answer()
    await _replace_callback_message(
        cq,
        _lex("versions_install_cancelled"),
        reply_markup=_version_root_menu(),
    )


@router.callback_query(AdminFilter(), F.data.startswith("versions:install_confirm:"))
async def on_versions_install_confirm(cq: CallbackQuery):
    parts = cq.data.split(":")
    if len(parts) != 3:
        await cq.answer(_lex("versions_bad_request"), show_alert=True)
        return
    tag = parts[2]
    user_id = int(cq.from_user.id)
    now = time.time()
    with _PENDING_VERSION_INSTALLS_LOCK:
        pending = _PENDING_VERSION_INSTALLS.get(user_id)
        if not pending or pending.get("tag") != tag:
            pending = None
        elif now - float(pending.get("created_at") or 0) > 180:
            _PENDING_VERSION_INSTALLS.pop(user_id, None)
            pending = None
    if pending is None:
        await cq.answer(_lex("versions_install_expired"), show_alert=True)
        return

    target = SESSION.get("target")
    if target != pending.get("target"):
        with _PENDING_VERSION_INSTALLS_LOCK:
            _PENDING_VERSION_INSTALLS.pop(user_id, None)
        await cq.answer(_lex("versions_install_target_changed"), show_alert=True)
        return
    info = devices.get(target) if target else None
    context = _version_context("agent")
    if not info or not context:
        _clear_pending_version_install(user_id)
        await cq.answer(_lex("versions_missing_device"), show_alert=True)
        return
    _, component, _ = context
    try:
        releases = await asyncio.to_thread(release_catalog.catalog.list)
    except (OSError, ValueError):
        _clear_pending_version_install(user_id)
        await cq.answer(_lex("versions_catalog_unavailable"), show_alert=True)
        return
    release = next((item for item in releases if item.tag == tag), None)
    if release is None:
        _clear_pending_version_install(user_id)
        await cq.answer(_lex("versions_not_found"), show_alert=True)
        return
    reason = _version_install_block_reason("agent", release, component, info)
    if reason:
        _clear_pending_version_install(user_id)
        await cq.answer(_lex(reason), show_alert=True)
        return

    with _PENDING_VERSION_INSTALLS_LOCK:
        _PENDING_VERSION_INSTALLS.pop(user_id, None)
    await simple_command(
        cq,
        "agent_update",
        "🔄",
        _lex("versions_install_action", tag=tag),
        timeout=110.0,
        update=True,
        release_tag=tag,
    )


async def _guardian_command(cq: CallbackQuery, command: str, *, enabled: bool | None = None):
    # `type=guardian` routes the packet; `action=guardian` is the explicit
    # marker consumed by the macOS and Windows Guardians.
    kwargs = {"action": "guardian", "command": command}
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
    await simple_command(cq, "geo_location", "⚠️", _lex("geo_beta"), timeout=16.0)


@router.message(F.from_user.id != ADMIN_ID)
async def on_denied(message: Message):
    """Fallback must stay last so authorized FSM handlers get first refusal."""
    if get_user_role(message.from_user.id) == Role.BLOCKED:
        text = "Доступ для этого аккаунта заблокирован."
    else:
        text = "Используй /start, чтобы открыть доступные разделы."
    await _replace_user_card(message, text, reply_markup=None)


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
