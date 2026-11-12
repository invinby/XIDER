"""Basic host telemetry command handlers."""

from __future__ import annotations


def installed_apps(payload: dict, *, device_id, publish_response, run_without_console,
                   check_output, devnull) -> None:
    ps = (
        "$paths = @( "
        "'HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*', "
        "'HKLM:\\Software\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*', "
        "'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*' "
        "); $names = @(); "
        "foreach ($p in $paths) { $names += (Get-ItemProperty $p -ErrorAction SilentlyContinue | "
        "Where-Object { $_.DisplayName } | Select-Object -ExpandProperty DisplayName) }; "
        "$names | Sort-Object -Unique"
    )
    try:
        out = run_without_console(
            check_output, ["powershell", "-NoProfile", "-Command", ps],
            text=True, stderr=devnull, timeout=25,
        )
        items = sorted({line.strip() for line in out.splitlines() if line.strip()})
        total = len(items)
        shown = items[:40]
        text = f"📦 Установленные программы ({total}):\n" + "\n".join(f"• {item}" for item in shown)
        if total > len(shown):
            text += f"\n… и ещё {total - len(shown)}"
        ok = True
    except Exception as exc:
        text, ok = f"⚠️ Ошибка получения софта: {exc}", False
    publish_response("sys_installed_apps", {
        "type": "sys_installed_apps", "device_id": device_id, "ok": ok, "text": text,
    })


def history_cmd(payload: dict, *, device_id, publish_response, clamp, os_module, open_file) -> None:
    lines = clamp(payload.get("lines"), 1, 200, 30)
    appdata = os_module.environ.get("APPDATA", "")
    hist_path = os_module.path.join(appdata, r"Microsoft\Windows\PowerShell\PSReadLine\ConsoleHost_history.txt")
    try:
        if os_module.path.exists(hist_path):
            with open_file(hist_path, "r", encoding="utf-8", errors="ignore") as file:
                history = [line.strip() for line in file if line.strip()][-lines:]
            text = "🕘 История PowerShell (последние):\n" + "\n".join(f"• {item}" for item in history)
            ok = True
        else:
            text, ok = "История PowerShell не найдена.", True
    except Exception as exc:
        text, ok = f"⚠️ Ошибка чтения истории: {exc}", False
    publish_response("sys_history_cmd", {
        "type": "sys_history_cmd", "device_id": device_id, "ok": ok, "text": text[:3800],
    })


def kill_by_name(name: str, *, os_module, psutil_module) -> dict:
    """Stop matching user-visible processes and wait for each stopped PID to exit."""
    low = (name or "").strip().lower()
    if not low:
        return {"ok": False, "state": "invalid", "error": "empty name", "count": 0, "killed": [],
                "text": "Не указано имя процесса; ничего не завершено."}
    killed, errors = [], 0
    matched = 0
    me = os_module.getpid()
    for proc in psutil_module.process_iter(["pid", "name"]):
        try:
            if proc.info["pid"] == me:
                continue
            pname = (proc.info.get("name") or "").lower()
            if low in pname:
                matched += 1
                proc.kill()
                proc.wait(timeout=3)
                killed.append(proc.info["pid"])
        except (psutil_module.NoSuchProcess, psutil_module.AccessDenied,
                psutil_module.ZombieProcess, psutil_module.TimeoutExpired):
            errors += 1
    if not matched:
        return {"ok": False, "state": "not_found", "count": 0, "killed": [], "errors": 0,
                "text": "Совпадений не найдено; ничего не завершено."}
    if errors:
        state = "partial" if killed else "failed"
        return {"ok": False, "state": state, "count": len(killed), "killed": killed[:20], "errors": errors,
                "text": f"Завершено и проверено: {len(killed)}; не подтверждено: {errors}. Результат частичный."}
    return {"ok": True, "state": "completed", "count": len(killed), "killed": killed[:20], "errors": 0,
            "text": f"Завершено и подтверждено: {len(killed)} процесс(ов)."}


def proc_kill_name(payload: dict, *, publish_response, device_id, kill_by_name, logger) -> None:
    result = kill_by_name(payload.get("name") or "")
    logger.info("proc_kill_name: %s", result)
    publish_response("proc_kill_name", {"type": "proc_kill_name", "device_id": device_id, **result})


def kill_process(payload: dict, *, publish_response, device_id, psutil_module, logger) -> None:
    pid = int(payload.get("pid", 0) or 0)
    proc = psutil_module.Process(pid)
    name = proc.name()
    logger.info("kill_process: pid=%s (%s)", pid, name)
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except psutil_module.TimeoutExpired:
        proc.kill()
    publish_response("kill_process", {
        "type": "kill_process", "device_id": device_id,
        "pid": pid, "name": name, "killed": True,
    })


def sysinfo(
    payload: dict, *, publish_response, device_id: str, platform: str, psutil_module,
    format_battery, primary_mac, regional_settings, socket_module, time_module, logger,
) -> None:
    cpu = psutil_module.cpu_percent(interval=1)
    mem = psutil_module.virtual_memory()
    disk = psutil_module.disk_usage("C:\\")
    boot = psutil_module.boot_time()
    uptime_sec = int(time_module.time() - boot)
    hours, remainder = divmod(uptime_sec, 3600)
    minutes, _ = divmod(remainder, 60)
    uptime_str = f"{hours}ч {minutes}мин"
    battery = format_battery(psutil_module.sensors_battery())

    publish_response("sysinfo", {
        "type": "sysinfo",
        "device_id": device_id,
        "hostname": socket_module.gethostname(),
        "mac": primary_mac(),
        "os": platform,
        "cpu_percent": round(cpu, 1),
        "ram_used_gb": round(mem.used / (1024 ** 3), 1),
        "ram_total_gb": round(mem.total / (1024 ** 3), 1),
        "ram_percent": round(mem.percent, 1),
        "disk_used_gb": round(disk.used / (1024 ** 3), 1),
        "disk_total_gb": round(disk.total / (1024 ** 3), 1),
        "disk_percent": round(disk.percent, 1),
        "boot_time_sec": int(boot),
        "uptime": uptime_str,
        "battery": battery,
        "regional_settings": regional_settings(payload.get("id")),
    })
    logger.info("Sysinfo отправлен")


def processes(payload: dict, *, publish_response, device_id: str, psutil_module, format_processes, logger) -> None:
    procs = list(psutil_module.process_iter(["pid", "name", "memory_percent"]))
    top_n = int(payload.get("top_n", 5) or 5)
    top_n = max(1, min(top_n, 25))
    result = format_processes(procs, top_n=top_n)
    publish_response("processes", {
        "type": "processes", "device_id": device_id,
        "lines": result["lines"], "top": result["top"],
    })
    logger.info("Список процессов отправлен")


def battery(payload: dict, *, publish_response, device_id: str, psutil_module, format_battery, logger) -> None:
    battery_data = format_battery(psutil_module.sensors_battery())
    publish_response("battery", {"type": "battery", "device_id": device_id, **battery_data})
    logger.info("Состояние батареи отправлено")


def network(payload: dict, *, publish_response, device_id: str, psutil_module, format_network, logger) -> None:
    result = format_network(
        psutil_module.net_if_addrs(),
        psutil_module.net_if_stats(),
        psutil_module.net_io_counters(pernic=True),
    )
    publish_response("network", {
        "type": "network", "device_id": device_id,
        "interfaces": result["interfaces"], "totals": result["totals"],
    })
    logger.info("Сетевая сводка отправлена")


def services(payload: dict, *, publish_response, device_id: str, psutil_module, format_services, logger) -> None:
    result = format_services(psutil_module.win_service_iter())
    publish_response("services", {
        "type": "services", "device_id": device_id,
        "total": result["total"], "running": result["running"],
        "stopped": result["stopped"], "failed_auto_start": result["failed_auto_start"],
    })
    logger.info("Сводка служб отправлена")


def sys_uptime(payload: dict, *, publish_response, device_id: str, psutil_module, time_module) -> None:
    """Publish the legacy local-time uptime response without changing its shape."""
    try:
        boot_ts = psutil_module.boot_time()
        uptime_secs = int(time_module.time() - boot_ts)
        days, rem = divmod(uptime_secs, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, secs = divmod(rem, 60)
        boot_dt = time_module.strftime("%d.%m.%Y %H:%M:%S", time_module.localtime(boot_ts))
        text = (
            f"⏱ Время работы системы (Uptime):\n"
            f"• Аптайм: {days} дн. {hours} ч. {minutes} мин. {secs} сек.\n"
            f"• Время загрузки: {boot_dt}"
        )
        ok = True
    except Exception as exc:
        text = f"⚠️ Ошибка получения аптайма: {exc}"
        ok = False
    publish_response("output", {
        "type": "sys_uptime", "device_id": device_id, "ok": ok, "text": text,
    })


def usb_devices(payload: dict, *, publish_response, device_id: str, run_without_console, check_output, devnull) -> None:
    try:
        output = run_without_console(
            check_output,
            ["powershell", "-NoProfile", "-Command",
             "Get-PnpDevice -PresentOnly -Class USB | "
             "Select-Object -First 25 -ExpandProperty FriendlyName"],
            text=True, stderr=devnull, timeout=20,
        )
        items = [line.strip() for line in output.splitlines() if line.strip()]
        text = "🔌 USB:\n" + "\n".join(f"• {item}" for item in items[:25])
        ok = True
    except Exception as exc:
        items, text, ok = [], f"⚠️ USB: {exc}", False
    publish_response("usb_devices", {"type": "usb_devices", "device_id": device_id,
                                     "ok": ok, "text": text, "items": items})


def startup_list(payload: dict, *, publish_response, device_id: str, run_without_console, check_output, devnull, parse_reg_query) -> None:
    names = []
    try:
        for hive in ("HKCU", "HKLM"):
            output = run_without_console(
                check_output,
                ["reg", "query", f"{hive}\\Software\\Microsoft\\Windows\\CurrentVersion\\Run"],
                text=True, stderr=devnull, timeout=10,
            )
            names.extend(parse_reg_query(output))
        text = "🚀 Автозагрузка:\n" + ("\n".join(f"• {name}" for name in names[:30]) or "(пусто)")
        ok = True
    except Exception as exc:
        text, ok = f"⚠️ Автозагрузка: {exc}", False
    publish_response("startup_list", {"type": "startup_list", "device_id": device_id,
                                      "ok": ok, "text": text, "items": names})


def env_get(payload: dict, *, publish_response, device_id: str, collect_env) -> None:
    raw = payload.get("names") or payload.get("name")
    names = [raw] if isinstance(raw, str) else (raw or None)
    env = collect_env(names)
    text = "\n".join(f"{key}={value}" for key, value in sorted(env.items())) or "(пусто)"
    publish_response("env_get", {"type": "env_get", "device_id": device_id,
                                 "ok": True, "text": text[:3500], "env": env})


def netstat(payload: dict, *, publish_response, device_id: str, run_without_console, check_output, devnull) -> None:
    try:
        output = run_without_console(
            check_output, ["netstat", "-an", "-p", "tcp"],
            text=True, stderr=devnull, timeout=15,
        )
        lines = [line for line in output.splitlines() if line.strip()][:40]
        text = "🕸 TCP-соединения (первые 40):\n" + "\n".join(lines)
        ok = True
    except Exception as exc:
        text, ok = f"⚠️ netstat: {exc}", False
    publish_response("netstat", {"type": "netstat", "device_id": device_id,
                                 "ok": ok, "text": text[:3500]})
