# XGENT — XGENT-WDS

Клиент XGENT для Windows (Asus). Прозрачная версия: в системном трее
всегда виден значок, через него же можно остановить клиент. Скрытых
функций нет.

## Installation / Установка и запуск

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

2. Устанавливайте автозапуск только через `install_agent.ps1` ниже. Старые
   ручные варианты с задачей `XGENT` и ключами `XGENT`/`XGentAgent` в реестре
   устарели и могут запускать несколько экземпляров. Современный установщик
   использует одну видимую задачу `XIDER Agent` и очищает эти известные старые
   записи, не затрагивая `.env`.

The Windows agent is visible in the tray. Windows Guardian is a separate,
visible Scheduled Task and is the only automatic worker-recovery mechanism.
An intentional stop from the tray is recorded as owner intent, so Guardian
does not immediately relaunch the agent; automatic logon startup remains
independently configured.

Install both tasks from an elevated PowerShell prompt:

```powershell
Set-Location C:\path\to\XIDER\XGENT-WDS
PowerShell -NoProfile -ExecutionPolicy Bypass -File .\install_agent.ps1
```

Guardian commands from Telegram work the same way as on macOS. Manual task
commands, if needed:

```powershell
PowerShell -NoProfile -ExecutionPolicy Bypass -File .\install_guardian.ps1
.\stop_guardian.bat
```

⚠️ Если остановить агент вручную или через Guardian, он остаётся остановленным
в текущем сеансе. Если автозапуск включён, задача агента может снова запуститься
при следующем входе в Windows.

## Настройка

- Идентификатор устройства создаётся автоматически при первом запуске
  в `%USERPROFILE%\.xgent\config.json` и в дальнейшем не меняется.
- Лог клиента: `%USERPROFILE%\.xgent\xgent.log` (ротация, 3 файла по 500 КБ).
- Секреты и параметры брокера — в `.env` (скопируйте из `.env.example`):
  `SHARED_KEY` должен совпадать с TG-BOT-SERVER и XGENT-MCS;
  `MQTT_TLS=true`, `MQTT_USERNAME` и `MQTT_PASSWORD` обязательны. Агент
  отклоняет plaintext MQTT и соединение без broker credentials. Настройте
  ACL на приватном брокере только для топиков XIDER. В текущей схеме bootstrap
  передаёт агенту общую broker-пару; отдельные роли ACL ещё не реализованы и
  не проверяются клиентом.

## Подписанное обновление агента

В исходной Python-установке начиная с версии **4.0.1** бот может запросить
стабильный GitHub Release. Агент проверяет подпись Ed25519 и размер/SHA-256
архива, выбирает только allowlist-файлы Windows-агента, сохраняет прежние файлы
в `%USERPROFILE%\.xgent\agent-backups`, а после перезапуска ждёт MQTT-соединение.
Если запуск прерван или агент не подключился за 120 секунд, транзакция пытается
вернуть предыдущие файлы. Это проверка доступности MQTT, не полный health-check
всех функций и не A/B-обновление.

Это не обновляет Guard Keeper и не заменяет отдельные файлы `.env`, venv или
установщик. Однофайловая Windows EXE-сборка пока отказывает без изменения файлов;
версии ниже 4.0.1 сначала нужно обновить установщиком. Обновление возможно
только из опубликованного релиза, подписанного ключом издателя, которому агент
доверяет. Локальный исходный кандидат закрепляет ключ издателя, но сам ключ и
подписанный релиз ещё не опубликованы; до публикации обновление заканчивается
безопасным отказом.

## Команды из Telegram

| Команда | Действие на Asus |
|---|---|
| Открыть ссылку | открывает URL в браузере по умолчанию |
| Показать текст | окно сообщения на экране |
| Озвучить текст | озвучка через pyttsx3; `beep` — системный сигнал |
| Статус | мгновенный ответ со статусом «online» |
| Остановить клиент | завершение работы клиента |

## English summary

`XGENT-WDS` is the visible Windows endpoint agent. It uses the same signed
MQTT protocol as macOS and keeps secrets in the sidecar `.env`. Telegram
controls are restricted by the bot's owner/admin roles. Do not describe this
agent as hidden: its tray icon and local startup entry are intentional.
