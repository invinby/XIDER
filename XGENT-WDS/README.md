# XGENT — XGENT-WDS

Клиент XGENT для Windows (Asus). Прозрачная версия: в системном трее
всегда виден значок, через него же можно остановить клиент. Скрытых
функций нет.

## Установка и запуск

Требуется Python 3.10+.

```powershell
py -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python xgent_wds.py
```

После запуска в трее появится значок **🟢 XGENT**.

## Сборка приложения и автозапуск

1. Соберите однофайловое приложение (в venv должен быть установлен PyInstaller):

```powershell
pip install pyinstaller
pyinstaller --onefile --windowed --name XGENT xgent_wds.py
```

Бинарь появится в `dist\XGENT.exe`.

2. Вариант А — планировщик задач (рекомендуется). Запуск при входе в систему:

```powershell
schtasks /Create /TN "XGENT" /TR "C:\полный\путь\к\XGENT.exe" /SC ONLOGON /RL LIMITED /F
```

3. Вариант Б — ключ автозагрузки в реестре:

```powershell
reg add HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v XGENT /t REG_SZ /d "C:\полный\путь\к\XGENT.exe" /f
```

⚠️ «Остановить клиент» из Telegram завершает клиент до следующего входа
в систему (автозапуск сработает при следующем входе).

## Настройка

- Идентификатор устройства создаётся автоматически при первом запуске
  в `%USERPROFILE%\.xgent\config.json` и в дальнейшем не меняется.
- Лог клиента: `%USERPROFILE%\.xgent\xgent.log` (ротация, 3 файла по 500 КБ).
- Секреты и параметры брокера — в `.env` (скопируйте из `.env.example`):
  `SHARED_KEY` должен совпадать с TG-BOT-SERVER и XGENT-MCS;
  `MQTT_TLS`, `MQTT_USERNAME`, `MQTT_PASSWORD` — опционально, для своего брокера.

## Команды из Telegram

| Команда | Действие на Asus |
|---|---|
| Открыть ссылку | открывает URL в браузере по умолчанию |
| Показать текст | окно сообщения на экране |
| Озвучить текст | озвучка через pyttsx3; `beep` — системный сигнал |
| Статус | мгновенный ответ со статусом «online» |
| Остановить клиент | завершение работы клиента |
