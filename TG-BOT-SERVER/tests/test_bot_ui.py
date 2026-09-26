"""Тесты UI-редизайна бота: device_card и новые категорийные меню (2026-09-05)."""

import asyncio
import time

import pytest

import bot

NINE_CATEGORIES = {
    "cat:media", "cat:screen", "cat:input", "cat:system",
    "cat:network", "cat:files", "cat:terminal", "cat:power", "cat:pranks",
}


class FakeDevices:
    """Заглушка DeviceStore: без доступа к диску, только нужные методы."""

    def __init__(self, devices=None):
        self._devices = dict(devices or {})

    def all(self):
        return dict(self._devices)

    def get(self, device_id):
        return self._devices.get(device_id)

    def get_favorites(self, device_id):
        info = self._devices.get(device_id) or {}
        return list(info.get("favorites", []))


def _callback_data(markup):
    """Все callback_data из InlineKeyboardMarkup одной строкой."""
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def _setup(monkeypatch, devices):
    monkeypatch.setattr(bot, "devices", FakeDevices(devices))


def test_device_card_all_shows_online_total(monkeypatch):
    _setup(monkeypatch, {"mac1": {"name": "Mac", "os": "macOS", "last_seen": time.time()},
                         "win1": {"name": "PC", "os": "Windows", "last_seen": 0}})
    card = bot.device_card("all")
    assert "УПРАВЛЕНИЕ ВСЕМИ УСТРОЙСТВАМИ" in card
    assert "В сети" in card


def test_device_card_online_device_fields(monkeypatch):
    _setup(monkeypatch, {"mac1": {"name": "My Mac", "os": "macOS 14",
                                  "version": "1.2", "last_seen": time.time()}})
    card = bot.device_card("mac1")
    assert "My Mac" in card
    assert "macOS 14" in card
    assert "v1.2" in card
    assert "В сети" in card
    assert "ID: mac1" in card


def test_device_card_offline_and_unknown_os(monkeypatch):
    _setup(monkeypatch, {"win1": {"name": "PC", "os": "Windows 11", "last_seen": 0}})
    card = bot.device_card("win1")
    assert "Оффлайн" in card
    assert "🪟" in card  # иконка Windows
    unknown = bot.device_card("missing")
    assert "missing" in unknown  # нет записи — имя = device_id


def test_device_menu_has_nine_category_buttons(monkeypatch):
    _setup(monkeypatch, {"dev1": {"name": "Dev"}})
    cbs = _callback_data(bot.device_menu("dev1"))
    assert NINE_CATEGORIES.issubset(set(cbs))


def test_each_category_menu_builds_buttons():
    """Каждый новый категорийный рендерер возвращает клавиатуру с цветными кнопками."""
    builders = (
        bot.media_menu, bot.screen_menu, bot.input_menu, bot.system_menu,
        bot.network_menu, bot.files_menu, bot.terminal_menu, bot.power_menu_new,
        bot.pranks_menu, bot.control_menu, bot.fun_menu,
    )
    for builder in builders:
        cbs = _callback_data(builder())
        assert cbs, f"{builder.__name__} вернул пустую клавиатуру"


def test_buttons_have_colored_styles(monkeypatch):
    """Проверяет, что кнопки имеют цветные стили (style: success/primary/danger)."""
    _setup(monkeypatch, {"dev1": {"name": "Dev", "os": "macOS", "last_seen": time.time()}})
    markup = bot.device_menu("dev1")
    styles = [b.style for row in markup.inline_keyboard for b in row if b.style]
    assert "success" in styles
    assert "primary" in styles
    assert "danger" in styles


def test_pranks_menu_has_stop_all_button():
    """Каждая страница меню приколов имеет кнопку экстренной остановки 'prank:stop_all'."""
    for page in range(1, 6):
        markup = bot.pranks_menu(page=page)
        cbs = _callback_data(markup)
        assert "prank:stop_all" in cbs
        texts = [b.text for row in markup.inline_keyboard for b in row]
        assert any("СТОП" in t for t in texts)


def test_pranks_menu_toggle_states(monkeypatch):
    """Проверяет динамическое изменение кнопок при активных приколах (инверсия мыши, скрытые иконки)."""
    target = "pc_test_1"
    monkeypatch.setitem(bot.SESSION, f"mouse_swapped_{target}", False)
    monkeypatch.setitem(bot.SESSION, f"desktop_hidden_{target}", False)

    # Стандартный вид
    m_off_p1 = bot.pranks_menu(page=1, target=target)
    m_off_p3 = bot.pranks_menu(page=3, target=target)
    texts_p1_off = [b.text for row in m_off_p1.inline_keyboard for b in row]
    texts_p3_off = [b.text for row in m_off_p3.inline_keyboard for b in row]
    assert "🫥 Скрыть иконки" in texts_p1_off
    assert "🖱 Инверсия мыши" in texts_p3_off

    # Включаем приколы
    monkeypatch.setitem(bot.SESSION, f"mouse_swapped_{target}", True)
    monkeypatch.setitem(bot.SESSION, f"desktop_hidden_{target}", True)
    m_on_p1 = bot.pranks_menu(page=1, target=target)
    m_on_p3 = bot.pranks_menu(page=3, target=target)
    texts_p1_on = [b.text for row in m_on_p1.inline_keyboard for b in row]
    texts_p3_on = [b.text for row in m_on_p3.inline_keyboard for b in row]
    assert any("[ВКЛ] Иконки скрыты" in t for t in texts_p1_on)
    assert any("[ВКЛ] Инверсия мыши" in t for t in texts_p3_on)


def test_screen_menu_nightlight_toggle(monkeypatch):
    """Проверяет переключение текста и стиля ночного света в screen_menu."""
    target = "pc_test_2"
    monkeypatch.setitem(bot.SESSION, f"nightlight_{target}", False)
    m_off = bot.screen_menu(target=target)
    texts_off = [b.text for row in m_off.inline_keyboard for b in row]
    assert "🌙 Ночной свет" in texts_off

    monkeypatch.setitem(bot.SESSION, f"nightlight_{target}", True)
    m_on = bot.screen_menu(target=target)
    texts_on = [b.text for row in m_on.inline_keyboard for b in row]
    assert any("[ВКЛ] Ночной свет" in t for t in texts_on)


def test_response_collector_wait_for_and_race_safety():
    """Проверяет изолированное тикетное ожидание wait_for без взаимного затирания."""
    async def _async_test():
        collector = bot.ResponseCollector()
        bot.LOOP = asyncio.get_running_loop()

        # Запускаем два параллельных ожидания разных команд
        task_wifi = asyncio.create_task(collector.wait_for("dev1", "net_wifi_passwords", timeout=2.0))
        task_usb = asyncio.create_task(collector.wait_for("dev1", "usb_devices", timeout=2.0))

        await asyncio.sleep(0.01)

        # Приходит ответ от USB первым
        collector.submit("dev1", {"type": "usb_devices", "text": "USB flash drive"})
        # Затем от WiFi
        collector.submit("dev1", {"type": "net_wifi_passwords", "text": "Home_WiFi: 12345"})

        res_usb = await task_usb
        res_wifi = await task_wifi

        assert res_usb is not None and res_usb.get("text") == "USB flash drive"
        assert res_wifi is not None and res_wifi.get("text") == "Home_WiFi: 12345"

    asyncio.run(_async_test())


def test_response_collector_keeps_fast_response_for_command_id():
    """Ответ, пришедший сразу после публикации, не теряется до wait_for."""
    async def _async_test():
        collector = bot.ResponseCollector()
        bot.LOOP = asyncio.get_running_loop()
        collector.submit("dev1", {"type": "battery", "id": "cmd123", "percent": 88})
        result = await collector.wait_for("dev1", "battery", timeout=0.2, command_id="cmd123")
        assert result is not None
        assert result.get("percent") == 88

    asyncio.run(_async_test())


def test_custom_ui_mode_changes_admin_label_and_main_menu(monkeypatch):
    monkeypatch.setattr(bot.bot_settings, "get", lambda key, default=None: "custom" if key == "ui_style" else default)
    markup = bot.main_menu(bot.ADMIN_ID)
    texts = [b.text for row in markup.inline_keyboard for b in row]
    assert "🧰 Мои машинки" in texts
    assert "🌍 Весь зоопарк" in texts


def test_server_menu_contains_metrics_chart_and_safe_terminal():
    markup = bot.server_menu(bot.ADMIN_ID)
    callbacks = _callback_data(markup)
    assert {"server:specs", "server:metrics", "server:chart", "server:terminal"}.issubset(set(callbacks))


def test_device_card_shows_guardian_state(monkeypatch):
    monkeypatch.setattr(bot.devices, "get", lambda device_id: {
        "name": "MacBook-Air.local", "os": "macOS 27.0", "version": "3.3.8",
        "online": True, "last_seen": __import__("time").time(),
        "guardian": {"version": "1.0"},
        "guardian_last_seen": __import__("time").time(),
    })
    text = bot.device_card("mac-1")
    assert "Guardian" in text
    assert "v1.0" in text
