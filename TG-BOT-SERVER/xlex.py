"""X-LEX: named, testable copy for the six X-STAB interface voices.

Commands, permissions and confirmation requirements never depend on a voice.
Only presentation changes.  Runtime data must be HTML-escaped by the caller.
"""

from __future__ import annotations

from string import Formatter


STYLES = (
    "xtexbo",
    "xperson",
    "xpikmi",
    "xplain",
    "xnoir",
    "xadam",
)
LEGACY_STYLES = {
    "technical": "xtexbo",
    "custom": "xperson",
    "conversational": "xplain",
}
STYLE_NAMES = {
    "xtexbo": "X-TEXBO · технический",
    "xperson": "X-PERSON · авторский",
    "xpikmi": "X-PIKMI · мягкий",
    "xplain": "X-PLAIN · понятный",
    "xnoir": "X-NOIR · лаконичный",
    "xadam": "TRUE ADAM · строгий",
}


COPY = {
    "start_owner": {
        "xtexbo": "Контрольный интерфейс активен. Доступ владельца подтверждён.",
        "xperson": "Пульт на месте. Посмотрим, что сегодня решило выкинуть фокус, и приведём это в порядок.",
        "xpikmi": "Всё на месте. Давай спокойно посмотрим, как чувствуют себя устройства.",
        "xplain": "Панель готова. Выбери устройство или открой серверную.",
        "xnoir": "Канал открыт. Устройства на связи.",
        "xadam": "Система готова. Выбери цель и действуй.",
    },
    "start_user": {
        "xtexbo": "Активны только назначенные владельцем права на устройства и команды.",
        "xperson": "Доступ есть. Лишние кнопки закрыты — так и должно быть, это не баг.",
        "xpikmi": "Тебе доступны только выданные устройства и действия. Всё под контролем.",
        "xplain": "Ты можешь пользоваться только устройствами и кнопками, к которым выдан доступ.",
        "xnoir": "Доступ ограничен назначенными правами.",
        "xadam": "Работай в пределах выданных полномочий.",
    },
    "start_guest": {
        "xtexbo": "Режим чтения: команды управления заблокированы до выдачи прав.",
        "xperson": "Смотреть можно. Управлять пока нельзя — хозяин ещё не выдал права.",
        "xpikmi": "Пока открыт только просмотр. Для управления понадобится разрешение владельца.",
        "xplain": "Ты можешь смотреть разделы, но управлять устройствами пока нельзя.",
        "xnoir": "Доступ: просмотр.",
        "xadam": "Режим наблюдения. Команды недоступны.",
    },
    "blocked": {
        "xtexbo": "Доступ отклонён: аккаунт заблокирован владельцем.",
        "xperson": "Доступ закрыт. Тут без сюрпризов: аккаунт заблокирован владельцем.",
        "xpikmi": "Доступ к боту закрыт владельцем.",
        "xplain": "Владелец заблокировал доступ для этого аккаунта.",
        "xnoir": "Доступ закрыт.",
        "xadam": "Аккаунт заблокирован. Доступа нет.",
    },
    "main_title": {
        "xtexbo": "Главный интерфейс управления",
        "xperson": "Главный пульт. Всё важное здесь, без хождения по кругу.",
        "xpikmi": "Главное меню. Выбирай, куда заглянем сначала.",
        "xplain": "Главное меню",
        "xnoir": "Главный пульт",
        "xadam": "Командный центр",
    },
    "devices_button": {
        "xtexbo": "Устройства · реестр",
        "xperson": "Устройства · кто тут живой?",
        "xpikmi": "Мои устройства",
        "xplain": "Устройства",
        "xnoir": "Устройства",
        "xadam": "Устройства",
    },
    "server_button": {
        "xtexbo": "Сервер · подсистема",
        "xperson": "Серверная · сердце этой штуки",
        "xpikmi": "Серверная",
        "xplain": "Серверная",
        "xnoir": "Сервер",
        "xadam": "Сервер",
    },
    "about_button": {
        "xtexbo": "Сведения об архитектуре",
        "xperson": "О системе · что мы вообще собрали",
        "xpikmi": "О системе",
        "xplain": "О XIDER",
        "xnoir": "Досье",
        "xadam": "Сведения",
    },
    "versions_button": {
        "xtexbo": "Реестр версий · агент {agent} / Keeper {keeper}",
        "xperson": "Версии · агент {agent}, Keeper {keeper}",
        "xpikmi": "Версии · агент {agent} / Keeper {keeper}",
        "xplain": "Версии · агент {agent} / Keeper {keeper}",
        "xnoir": "Версии · {agent} / {keeper}",
        "xadam": "Версии · {agent} / {keeper}",
    },
    "version_warning": {
        "xtexbo": "Установленная версия {current} старше рекомендуемой {latest}; поведение отдельных команд может отличаться.",
        "xperson": "Здесь версия {current}, а уже есть {latest}. Если что-то чудит — сначала обновим, потом будем ругать железо.",
        "xpikmi": "Установлена версия {current}; доступна {latest}. Некоторые действия могут работать иначе.",
        "xplain": "Агент {current} устарел. Доступна версия {latest}; некоторые функции могут работать нестабильно.",
        "xnoir": "Старая версия {current}. Доступна {latest}.",
        "xadam": "Версия {current} устарела. Доступна {latest}.",
    },
    "geo_beta": {
        "xtexbo": "Геопозиция по публичному IP · оценка, точность не гарантируется",
        "xperson": "Локация по IP · примерно, не GPS и не магия",
        "xpikmi": "Примерная локация по IP",
        "xplain": "Локация по IP · неточная",
        "xnoir": "Локация IP · приблизительно",
        "xadam": "Локация по IP · оценка",
    },
    "danger_confirm": {
        "xtexbo": "Подтвердите действие «{action}». Оно изменит состояние устройства.",
        "xperson": "Подтверди «{action}». Это реально изменит состояние устройства, не просто красивая кнопка.",
        "xpikmi": "Подтверди «{action}»: действие изменит состояние устройства.",
        "xplain": "Подтверди «{action}». После подтверждения устройство выполнит это действие.",
        "xnoir": "«{action}» изменит состояние устройства. Подтвердить?",
        "xadam": "Подтверди «{action}». Команда будет выполнена.",
    },
}


NAV_KEYS = (
    "all_devices", "events", "admin", "media", "screen", "input", "system",
    "network", "files", "terminal", "power", "pranks", "device_settings",
)
NAV = {
    "xtexbo": {
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
    "xplain": {
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
    "xnoir": {
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
    return value if value in STYLES else "xplain"


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
