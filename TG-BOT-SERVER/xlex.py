"""X-LEX: named, testable copy for the six X-STAB interface voices.

Commands, permissions and confirmation requirements never depend on a voice.
Only presentation changes.  Runtime data must be HTML-escaped by the caller.
"""

from __future__ import annotations

from string import Formatter


STYLES = (
    "xtech",
    "xperson",
    "xpikmi",
    "xtarped",
    "xcore",
    "xadam",
)
LEGACY_STYLES = {
    "technical": "xtech",
    "custom": "xperson",
    "conversational": "xtarped",
    "xtexbo": "xtech",
    "xplain": "xtarped",
    "xnoir": "xcore",
}
LEGACY_STYLE_KEYS = {
    "xtech": ("xtexbo",),
    "xtarped": ("xplain",),
    "xcore": ("xnoir",),
}
STYLE_NAMES = {
    "xtech": "X-TECH · технический",
    "xperson": "X-PERSON · авторский",
    "xpikmi": "X-PIKMI · мягкий",
    "xtarped": "X-TARPED · разговорный",
    "xcore": "X-CORE · системный",
    "xadam": "X-ADAM · TRUE ADAM",
}


COPY = {
    "start_owner": {
        "xtech": "Контрольный интерфейс активен. Доступ владельца подтверждён.",
        "xperson": "Пульт на месте. Посмотрим, что сегодня решило выкинуть фокус, и приведём это в порядок.",
        "xpikmi": "Всё на месте. Давай спокойно посмотрим, как чувствуют себя устройства.",
        "xtarped": "Пульт жив, паника отменяется. Выбирай устройство — посмотрим, что там опять устроило драму.",
        "xcore": "Канал открыт. Устройства на связи.",
        "xadam": "Система готова. Выбери цель и действуй.",
    },
    "start_user": {
        "xtech": "Активны только назначенные владельцем права на устройства и команды.",
        "xperson": "Доступ есть. Лишние кнопки закрыты — так и должно быть, это не баг.",
        "xpikmi": "Тебе доступны только выданные устройства и действия. Всё под контролем.",
        "xtarped": "Доступ есть только к выданным устройствам и кнопкам. Остальное закрыто — это не баг, а владелец предусмотрительно нажал тормоз.",
        "xcore": "Доступ ограничен назначенными правами.",
        "xadam": "Работай в пределах выданных полномочий.",
    },
    "start_guest": {
        "xtech": "Режим чтения: команды управления заблокированы до выдачи прав.",
        "xperson": "Смотреть можно. Управлять пока нельзя — хозяин ещё не выдал права.",
        "xpikmi": "Пока открыт только просмотр. Для управления понадобится разрешение владельца.",
        "xtarped": "Пока ты зритель: разделы смотреть можно, команды нажимать нельзя. Права выдаёт владелец, не сила убеждения.",
        "xcore": "Доступ: просмотр.",
        "xadam": "Режим наблюдения. Команды недоступны.",
    },
    "blocked": {
        "xtech": "Доступ отклонён: аккаунт заблокирован владельцем.",
        "xperson": "Доступ закрыт. Тут без сюрпризов: аккаунт заблокирован владельцем.",
        "xpikmi": "Доступ к боту закрыт владельцем.",
        "xtarped": "Доступ закрыт владельцем. Без загадок и драматичной музыки: решение уже принято.",
        "xcore": "Доступ закрыт.",
        "xadam": "Аккаунт заблокирован. Доступа нет.",
    },
    "main_title": {
        "xtech": "Главный интерфейс управления",
        "xperson": "Главный пульт. Всё важное здесь, без хождения по кругу.",
        "xpikmi": "Главное меню. Выбирай, куда заглянем сначала.",
        "xtarped": "Главное меню · пульт, а не квест-комната",
        "xcore": "Главный пульт",
        "xadam": "Командный центр",
    },
    "devices_button": {
        "xtech": "Устройства · реестр",
        "xperson": "Устройства · кто тут живой?",
        "xpikmi": "Мои устройства",
        "xtarped": "Устройства",
        "xcore": "Устройства",
        "xadam": "Устройства",
    },
    "my_devices_button": {
        "xtech": "Назначенные устройства",
        "xperson": "Мои устройства · только разрешённые",
        "xpikmi": "Мои устройства ♡",
        "xtarped": "Мои устройства",
        "xcore": "Мои узлы",
        "xadam": "Доступные устройства",
    },
    "guest_devices_button": {
        "xtech": "Реестр устройств · просмотр",
        "xperson": "Устройства · посмотреть можно",
        "xpikmi": "Устройства · просто посмотреть ♡",
        "xtarped": "Устройства · просмотр",
        "xcore": "Узлы · просмотр",
        "xadam": "Устройства · наблюдение",
    },
    "server_overview_button": {
        "xtech": "Сервер · обзор состояния",
        "xperson": "Серверная · посмотреть, как живёт",
        "xpikmi": "Серверная · как там дела? ♡",
        "xtarped": "Серверная · обзор",
        "xcore": "Сервер · состояние",
        "xadam": "Сервер · сводка",
    },
    "server_button": {
        "xtech": "Сервер · подсистема",
        "xperson": "Серверная · сердце этой штуки",
        "xpikmi": "Серверная",
        "xtarped": "Серверная",
        "xcore": "Сервер",
        "xadam": "Сервер",
    },
    "about_button": {
        "xtech": "Сведения об архитектуре",
        "xperson": "О системе · что мы вообще собрали",
        "xpikmi": "О системе",
        "xtarped": "О XIDER",
        "xcore": "Досье",
        "xadam": "Сведения",
    },
    "role_label": {
        "xtech": "Уровень доступа: {role}",
        "xperson": "Твои права: {role}",
        "xpikmi": "Твоя роль: {role} ♡",
        "xtarped": "Роль: {role}",
        "xcore": "Доступ: {role}",
        "xadam": "Полномочия: {role}",
    },
    "online_label": {
        "xtech": "Активных устройств: {online}/{total}",
        "xperson": "На связи: {online}/{total} · остальные пока молчат",
        "xpikmi": "На связи: {online}/{total} ♡",
        "xtarped": "Устройств онлайн: {online}/{total}",
        "xcore": "Активно: {online}/{total}",
        "xadam": "На связи: {online}/{total}",
    },
    "versions_button": {
        "xtech": "Реестр версий · агент {agent} / Keeper {keeper}",
        "xperson": "Версии · агент {agent}, Keeper {keeper}",
        "xpikmi": "Версии · агент {agent} / Keeper {keeper}",
        "xtarped": "Версии · агент {agent} / Keeper {keeper}",
        "xcore": "Версии · {agent} / {keeper}",
        "xadam": "Версии · {agent} / {keeper}",
    },
    "version_warning": {
        "xtech": "Установленная версия {current} старше рекомендуемой {latest}; поведение отдельных команд может отличаться.",
        "xperson": "Здесь версия {current}, а уже есть {latest}. Если что-то чудит — сначала обновим, потом будем ругать железо.",
        "xpikmi": "Установлена версия {current}; доступна {latest}. Некоторые действия могут работать иначе.",
        "xtarped": "Агент {current} отстал от версии {latest}. Может, всё ещё работает, но сюрпризы в комплект не входят.",
        "xcore": "Старая версия {current}. Доступна {latest}.",
        "xadam": "Версия {current} устарела. Доступна {latest}.",
    },
    "geo_beta": {
        "xtech": "Геопозиция по публичному IP · оценка, точность не гарантируется",
        "xperson": "Локация по IP · примерно, не GPS и не магия",
        "xpikmi": "Примерная локация по IP",
        "xtarped": "Локация по IP · неточная",
        "xcore": "Локация IP · приблизительно",
        "xadam": "Локация по IP · оценка",
    },
    "danger_confirm": {
        "xtech": "Подтвердите действие «{action}». Оно изменит состояние устройства.",
        "xperson": "Подтверди «{action}». Это реально изменит состояние устройства, не просто красивая кнопка.",
        "xpikmi": "Подтверди «{action}»: действие изменит состояние устройства.",
        "xtarped": "Подтверди «{action}». Это настоящая команда, не декоративная кнопка для красоты.",
        "xcore": "«{action}» изменит состояние устройства. Подтвердить?",
        "xadam": "Подтверди «{action}». Команда будет выполнена.",
    },
}


NAV_KEYS = (
    "all_devices", "events", "admin", "media", "screen", "input", "system",
    "network", "files", "terminal", "power", "pranks", "device_settings",
)
NAV = {
    "xtech": {
        "all_devices": "Глобальный реестр устройств",
        "events": "События и конфигурация",
        "admin": "Управление доступом",
        "media": "Медиазахват и вывод",
        "screen": "Дисплей и видеорежимы",
        "input": "Устройства ввода",
        "system": "Системная телеметрия",
        "network": "Сетевые интерфейсы",
        "files": "Файловые операции",
        "terminal": "Процессы и исполнение",
        "power": "Питание и защита",
        "pranks": "Визуальные и звуковые сценарии",
        "device_settings": "Конфигурация устройства",
    },
    "xperson": {
        "all_devices": "Все устройства · полный состав",
        "events": "События · кто опять чудит",
        "admin": "Админка · порядок наведём",
        "media": "Камера, звук и прочее",
        "screen": "Экран · картинка и её капризы",
        "input": "Клавиши и мышь",
        "system": "Система · что у неё внутри",
        "network": "Сеть · где связь потерялась",
        "files": "Файлы · без квестов",
        "terminal": "Процессы и терминал",
        "power": "Питание · тут без случайных тыков",
        "pranks": "Приколы · с кнопкой остановки",
        "device_settings": "Настройки этой машины",
    },
    "xpikmi": {
        "all_devices": "Все устройства",
        "events": "События и настройки",
        "admin": "Управление доступом",
        "media": "Фото и звук",
        "screen": "Экран и изображение",
        "input": "Мышь и клавиатура",
        "system": "Как чувствует себя система",
        "network": "Сеть и подключения",
        "files": "Файлы",
        "terminal": "Программы и процессы",
        "power": "Питание и защита",
        "pranks": "Розыгрыши",
        "device_settings": "Настройки устройства",
    },
    "xtarped": {
        "all_devices": "Все устройства",
        "events": "События и настройки",
        "admin": "Администрирование",
        "media": "Камера и звук",
        "screen": "Экран",
        "input": "Мышь и клавиатура",
        "system": "Состояние системы",
        "network": "Сеть",
        "files": "Файлы",
        "terminal": "Процессы и терминал",
        "power": "Питание и защита",
        "pranks": "Розыгрыши",
        "device_settings": "Настройки устройства",
    },
    "xcore": {
        "all_devices": "Все узлы",
        "events": "События",
        "admin": "Доступ",
        "media": "Медиа",
        "screen": "Экран",
        "input": "Ввод",
        "system": "Система",
        "network": "Сеть",
        "files": "Файлы",
        "terminal": "Терминал",
        "power": "Питание и защита",
        "pranks": "Сценарии",
        "device_settings": "Параметры",
    },
    "xadam": {
        "all_devices": "Все устройства",
        "events": "События и параметры",
        "admin": "Права доступа",
        "media": "Медиасистема",
        "screen": "Дисплей",
        "input": "Управление вводом",
        "system": "Диагностика системы",
        "network": "Сетевой контур",
        "files": "Файловая система",
        "terminal": "Процессы и терминал",
        "power": "Питание и защита",
        "pranks": "Сценарии розыгрыша",
        "device_settings": "Параметры устройства",
    },
}


def normalize_style(style: str | None) -> str:
    value = str(style or "").lower()
    value = LEGACY_STYLES.get(value, value)
    return value if value in STYLES else "xtech"


def render(key: str, style: str | None = None, **values: str) -> str:
    """Render a named phrase; the caller escapes inserted HTML values."""
    variants = COPY[key]
    return variants[normalize_style(style)].format(**values)


def nav(key: str, style: str | None = None) -> str:
    return NAV[normalize_style(style)][key]


def validate() -> None:
    """Fail CI for a missing voice or mismatched placeholders."""
    formatter = Formatter()
    if set(NAV) != set(STYLES):
        raise ValueError("navigation styles are incomplete")
    for style, labels in NAV.items():
        if set(labels) != set(NAV_KEYS):
            raise ValueError(f"{style}: navigation labels are incomplete")
    for key, variants in COPY.items():
        if set(variants) != set(STYLES):
            raise ValueError(f"{key}: expected all six text styles")
        fields = [
            {name for _, name, _, _ in formatter.parse(text) if name}
            for text in variants.values()
        ]
        if len(set(map(frozenset, fields))) != 1:
            raise ValueError(f"{key}: placeholder mismatch")


validate()
