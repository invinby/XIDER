# X-Guard Keeper

X-Guard Keeper — прозрачный supervisor агента. Он не маскируется, не собирает
данные и не заменяет штатные механизмы автозапуска. Его задача — перезапустить
агент после аварийного выхода, писать причину в журнал и замедлять повторные
попытки при быстром падении.

Пример для macOS/Linux:

```bash
python3 ops/guard_keeper.py --log XGENT-MCS/guard-keeper.log -- \
  python3 XGENT-MCS/xgent_mcs.py
```

Пример для Windows PowerShell:

```powershell
py -3 ops\guard_keeper.py --log XGENT-WDS\guard-keeper.log -- py -3 XGENT-WDS\xgent_wds.py
```

Остановка — `Ctrl+C` или штатный сигнал процесса. Установка в LaunchAgent или
Task Scheduler должна быть отдельным явно подтверждаемым шагом.
