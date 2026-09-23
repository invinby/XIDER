# XGENT — XGENT-MCS

Клиент XGENT для macOS (MacBook). Консольный агент без скрытых функций:
работа логируется в файл, статус виден в Telegram-боте («Статус» и список
устройств).

## Установка и запуск

Требуется Python 3.10+.

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python xgent_mcs.py
```

После запуска агент подключается к брокеру и появится в списке устройств бота.

## Сборка приложения и автозапуск

1. Соберите однофайловое приложение (в venv должен быть установлен PyInstaller):

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name XGENT xgent_mcs.py
```

Бинарь появится в `dist/XGENT`.

2. Создайте файл `~/Library/LaunchAgents/com.xgent.client.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.xgent.client</string>
    <key>ProgramArguments</key>
    <array>
        <string>/полный/путь/к/dist/XGENT</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
</dict>
</plist>
```

3. Загрузите агент:

```bash
launchctl load ~/Library/LaunchAgents/com.xgent.client.plist
```

⚠️ Из-за `KeepAlive` клиент автоматически перезапустится после команды
«Остановить клиент» из Telegram. Полная остановка до следующего входа:

```bash
launchctl unload ~/Library/LaunchAgents/com.xgent.client.plist
```

## Настройка

- Идентификатор устройства создаётся автоматически при первом запуске
  в `~/.xgent/config.json` и в дальнейшем не меняется.
- Лог клиента: `~/.xgent/xgent.log` (ротация, 3 файла по 500 КБ).
- Секреты и параметры брокера — в `.env` (скопируйте из `.env.example`):
  `SHARED_KEY` должен совпадать с TG-BOT-SERVER и XGENT-WDS;
  `MQTT_TLS`, `MQTT_USERNAME`, `MQTT_PASSWORD` — опционально, для своего брокера.

## Команды из Telegram

| Команда | Действие на MacBook |
|---|---|
| Открыть ссылку | открывает URL в браузере по умолчанию |
| Показать текст | системное уведомление macOS |
| Озвучить текст | озвучка через `say`; `beep` — системный звук |
| Статус | мгновенный ответ со статусом «online» |
| Остановить клиент | завершение работы клиента |
