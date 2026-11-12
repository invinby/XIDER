from types import SimpleNamespace

from xg_commands import system_info


class _Logger:
    def __init__(self):
        self.messages = []

    def info(self, message, *args, **kwargs):
        self.messages.append(message)


def test_processes_command_keeps_top_n_bounds_and_legacy_response_shape():
    published = []
    requested = []
    process_rows = [object(), object()]
    psutil = SimpleNamespace(process_iter=lambda fields: requested.append(fields) or process_rows)

    system_info.processes(
        {"top_n": 99},
        publish_response=lambda topic, response: published.append((topic, response)),
        device_id="device-4",
        psutil_module=psutil,
        format_processes=lambda rows, *, top_n: {"lines": [str(len(rows))], "top": top_n},
        logger=_Logger(),
    )

    assert requested == [["pid", "name", "memory_percent"]]
    assert published == [("processes", {"type": "processes", "device_id": "device-4", "lines": ["2"], "top": 25})]


def test_sys_uptime_keeps_legacy_response_and_local_boot_time():
    published = []
    clock = SimpleNamespace(
        time=lambda: 200_000,
        localtime=lambda timestamp: ("local", timestamp),
        strftime=lambda fmt, value: f"{fmt}:{value[0]}:{value[1]}",
    )
    system_info.sys_uptime(
        {}, publish_response=lambda topic, response: published.append((topic, response)),
        device_id="device-4", psutil_module=SimpleNamespace(boot_time=lambda: 100_000),
        time_module=clock,
    )

    assert published == [("output", {
        "type": "sys_uptime", "device_id": "device-4", "ok": True,
        "text": "⏱ Время работы системы (Uptime):\n"
                "• Аптайм: 1 дн. 3 ч. 46 мин. 40 сек.\n"
                "• Время загрузки: %d.%m.%Y %H:%M:%S:local:100000",
    })]


def test_sys_uptime_reports_snapshot_errors_in_legacy_shape():
    published = []
    system_info.sys_uptime(
        {}, publish_response=lambda topic, response: published.append((topic, response)),
        device_id="device-4", psutil_module=SimpleNamespace(boot_time=lambda: (_ for _ in ()).throw(OSError("offline"))),
        time_module=SimpleNamespace(),
    )

    assert published == [("output", {
        "type": "sys_uptime", "device_id": "device-4", "ok": False,
        "text": "⚠️ Ошибка получения аптайма: offline",
    })]


def test_battery_network_and_services_commands_preserve_response_shapes():
    published = []
    logger = _Logger()
    psutil = SimpleNamespace(
        sensors_battery=lambda: "battery-reading",
        net_if_addrs=lambda: "addresses",
        net_if_stats=lambda: "stats",
        net_io_counters=lambda **kwargs: kwargs,
        win_service_iter=lambda: "services",
    )
    publish = lambda topic, response: published.append((topic, response))

    system_info.battery(
        {}, publish_response=publish, device_id="device-4", psutil_module=psutil,
        format_battery=lambda value: {"present": value}, logger=logger,
    )
    system_info.network(
        {}, publish_response=publish, device_id="device-4", psutil_module=psutil,
        format_network=lambda *args: {"interfaces": [args[0]], "totals": {"pernic": args[2]}}, logger=logger,
    )
    system_info.services(
        {}, publish_response=publish, device_id="device-4", psutil_module=psutil,
        format_services=lambda value: {"total": 1, "running": 1, "stopped": 0, "failed_auto_start": 0},
        logger=logger,
    )

    assert published == [
        ("battery", {"type": "battery", "device_id": "device-4", "present": "battery-reading"}),
        ("network", {"type": "network", "device_id": "device-4", "interfaces": ["addresses"], "totals": {"pernic": {"pernic": True}}}),
        ("services", {"type": "services", "device_id": "device-4", "total": 1, "running": 1, "stopped": 0, "failed_auto_start": 0}),
    ]
    assert logger.messages == ["Состояние батареи отправлено", "Сетевая сводка отправлена", "Сводка служб отправлена"]


def test_usb_and_startup_commands_keep_read_only_commands_and_response_shapes():
    published = []
    calls = []

    def runner(function, command, **kwargs):
        calls.append((command, kwargs))
        return "Friendly USB device\n" if command[0] == "powershell" else "RunEntry\n"

    publish = lambda topic, response: published.append((topic, response))
    system_info.usb_devices({}, publish_response=publish, device_id="device-4", run_without_console=runner,
                            check_output=object(), devnull=-3)
    system_info.startup_list({}, publish_response=publish, device_id="device-4", run_without_console=runner,
                             check_output=object(), devnull=-3, parse_reg_query=lambda output: [output.strip()])

    assert calls[0][0] == ["powershell", "-NoProfile", "-Command",
                            "Get-PnpDevice -PresentOnly -Class USB | Select-Object -First 25 -ExpandProperty FriendlyName"]
    assert calls[1][0][0:2] == ["reg", "query"] and calls[2][0][0:2] == ["reg", "query"]
    assert [topic for topic, _response in published] == ["usb_devices", "startup_list"]
    assert published[0][1]["items"] == ["Friendly USB device"] and published[0][1]["ok"] is True
    assert published[1][1]["items"] == ["RunEntry", "RunEntry"] and published[1][1]["ok"] is True


def test_env_get_preserves_filtering_and_caps_display_text():
    published = []
    requested = []

    def collect_env(names):
        requested.append(names)
        return {"ZED": "last", "ALPHA": "first"}

    system_info.env_get(
        {"name": "ALPHA"}, publish_response=lambda topic, response: published.append((topic, response)),
        device_id="device-4", collect_env=collect_env,
    )

    assert requested == [["ALPHA"]]
    assert published[0] == ("env_get", {
        "type": "env_get", "device_id": "device-4", "ok": True,
        "text": "ALPHA=first\nZED=last", "env": {"ZED": "last", "ALPHA": "first"},
    })


def test_netstat_command_stays_read_only_and_bounds_response_lines():
    published = []
    calls = []

    def runner(function, command, **kwargs):
        calls.append((command, kwargs))
        return "\n".join(f"row-{index}" for index in range(45))

    system_info.netstat(
        {}, publish_response=lambda topic, response: published.append((topic, response)),
        device_id="device-4", run_without_console=runner, check_output=object(), devnull=-3,
    )

    assert calls[0] == (["netstat", "-an", "-p", "tcp"], {"text": True, "stderr": -3, "timeout": 15})
    assert published[0][0] == "netstat" and published[0][1]["ok"] is True
    assert published[0][1]["text"].endswith("row-39") and "row-40" not in published[0][1]["text"]
