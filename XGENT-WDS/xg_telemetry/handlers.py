"""System inventory, health telemetry and diagnostics command adapters."""

from __future__ import annotations

from types import FunctionType, MethodType


def bind_handler_methods(client, runtime_globals):
    bound = {}
    method_names = {'_do_pending_restart', '_do_os_support', '_do_startup_inventory', '_do_geo_location', '_do_driver_inventory', '_do_sysinfo_interval', '_do_memory_history', '_do_cpu_history', '_do_permission_settings', '_do_startup_duration', '_do_service_probe', '_do_processes', '_do_startup_list', '_do_battery', '_do_sysinfo', '_do_smart_trend', '_do_capabilities', '_do_sys_uptime', '_do_energy_usage', '_do_sys_history_cmd', '_do_services', '_do_installed_app_inventory', '_do_env_get', '_do_service_inventory', '_do_battery_health', '_do_volume_topology', '_do_sys_clean_temp', '_do_thermal_snapshot', '_do_disks', '_do_sleep_wake_history', '_do_clock_drift', '_do_memory_pressure_history', '_do_battery_cycles', '_do_sys_installed_apps', '_do_crash_summary', '_do_hardware_inventory', '_do_volume_history', '_sysinfo_schedule_loop'}
    for name in method_names:
        handler = globals()[name]
        runtime_handler = FunctionType(
            handler.__code__, runtime_globals, handler.__name__, handler.__defaults__, handler.__closure__
        )
        runtime_handler.__kwdefaults__ = handler.__kwdefaults__
        runtime_handler.__annotations__ = handler.__annotations__
        bound[name] = MethodType(runtime_handler, client)
    return bound


def _do_sysinfo_interval(self, payload: dict) -> None:
        request_id = payload.get("id")
        state = {"state": "unknown", "interval_seconds": 0, "schedule_id": None, "next_sample_at": None}
        try:
            state = save_schedule(
                CONFIG_DIR / "sysinfo_schedule.json",
                payload.get("interval_seconds"), payload.get("schedule_id"),
            )
            state = {**state, "state": "supported"}
            self._sysinfo_schedule_wake.set()
        except (OSError, TypeError, ValueError):
            log.warning("Rejected or could not persist D01-F26 schedule", exc_info=True)
        self._publish_response("sysinfo_schedule", {
            "type": "sysinfo_schedule_status", "schema": "xider.sysinfo-schedule.v1",
            "feature_id": "D01-F26", "device_id": DEVICE_ID,
            "request_id": request_id if isinstance(request_id, str) and 1 <= len(request_id) <= 128 else None,
            **state,
        })

def _sysinfo_schedule_loop(self) -> None:
        path = CONFIG_DIR / "sysinfo_schedule.json"
        while self._running.is_set():
            state = load_schedule(path)
            interval, next_at = state["interval_seconds"], state["next_sample_at"]
            if interval == 0 or not isinstance(next_at, int):
                self._sysinfo_schedule_wake.wait(30)
                self._sysinfo_schedule_wake.clear()
                continue
            delay = next_at - int(time.time())
            if delay > 0:
                self._sysinfo_schedule_wake.wait(min(delay, 30))
                self._sysinfo_schedule_wake.clear()
                continue
            schedule_id = state["schedule_id"]
            try:
                cpu = psutil.cpu_percent(interval=1)
                memory = psutil.virtual_memory()
                disk = psutil.disk_usage("C:\\")
                values = (cpu, memory.percent, disk.percent)
                if any(isinstance(value, bool) or not isinstance(value, (int, float))
                       or not math.isfinite(value) or not 0 <= value <= 100 for value in values):
                    raise ValueError("invalid scheduled metric")
                current = load_schedule(path)
                if current["schedule_id"] != schedule_id or current["interval_seconds"] != interval:
                    continue
                updated = save_schedule(path, interval, schedule_id)
                thermal = _read_thermal_snapshot()
                temperatures = [
                    row.get("temperature_c") for row in thermal.get("sensors", [])
                    if isinstance(row, dict)
                    and isinstance(row.get("temperature_c"), (int, float))
                    and not isinstance(row.get("temperature_c"), bool)
                    and math.isfinite(row["temperature_c"])
                    and -40 <= row["temperature_c"] <= 150
                ] if isinstance(thermal, dict) and thermal.get("source") == _THERMAL_SOURCE else []
                thermal_state = thermal.get("state") if isinstance(thermal, dict) else "unknown"
                if temperatures:
                    thermal_state = "supported"
                elif thermal_state == "supported":
                    thermal_state = "unknown"
                self._publish_response("sysinfo_schedule", {
                    "type": "sysinfo_summary_sample", "schema": "xider.sysinfo-summary.v1",
                    "feature_id": "D01-F26", "device_id": DEVICE_ID, "schedule_id": schedule_id,
                    "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                    "next_sample_at": updated["next_sample_at"],
                    "summary": {"platform": "Windows", "cpu_percent": round(float(cpu), 1),
                                "ram_percent": round(float(memory.percent), 1),
                                "disk_percent": round(float(disk.percent), 1)},
                    "temperature": {
                        "state": thermal_state if thermal_state in {"supported", "unsupported", "unknown"} else "unknown",
                        "source": _THERMAL_SOURCE,
                        "max_celsius": round(max(temperatures), 1) if temperatures else None,
                    },
                })
            except Exception:
                log.warning("D01-F26 scheduled sample unavailable", exc_info=True)
                current = load_schedule(path)
                if current["schedule_id"] == schedule_id and current["interval_seconds"] == interval:
                    save_schedule(path, interval, schedule_id)

def _do_geo_location(self, payload: dict) -> None:
        """One explicit request; IP estimation requires the exact boolean opt-in."""
        from location_adapter import read_system_location, read_ip_estimate, location_text, compare_location_results
        return _permission_commands.geo_location(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            read_system_location=read_system_location, read_ip_estimate=read_ip_estimate,
            location_text=location_text, compare_location_results=compare_location_results,
            open_settings=os.startfile,
        )

def _do_cpu_history(self, payload: dict) -> None:
        """Sample local CPU use for one bounded, owner-selected window."""
        return _performance_telemetry_commands.cpu_history(
            payload, publish_response=self._publish_response, device_id=DEVICE_ID,
            psutil_module=psutil, math_module=math, datetime_module=datetime, logger=log,
        )

def _do_memory_history(self, payload: dict) -> None:
        """Sample local RAM and swap use for one bounded, owner-selected window."""
        return _performance_telemetry_commands.memory_history(
            payload, publish_response=self._publish_response, device_id=DEVICE_ID,
            psutil_module=psutil, math_module=math, time_module=time,
            datetime_module=datetime, logger=log,
        )

def _do_memory_pressure_history(self, payload: dict) -> None:
        """Sample Windows memory-resource threshold notifications for a bounded window."""
        return _performance_telemetry_commands.memory_pressure_history(
            payload, publish_response=self._publish_response, device_id=DEVICE_ID,
            time_module=time, datetime_module=datetime, logger=log,
            source=_MEMORY_PRESSURE_SOURCE, read_sample=_read_memory_pressure_sample,
            normalize_sample=_memory_pressure_history_sample,
        )

def _do_thermal_snapshot(self, payload: dict) -> None:
        return _power_telemetry_commands.thermal_snapshot(
            payload, publish_response=self._publish_response, device_id=DEVICE_ID,
            read_snapshot=_read_thermal_snapshot, datetime_module=datetime,
        )

def _do_battery_health(self, payload: dict) -> None:
        return _power_telemetry_commands.battery_health(
            payload, publish_response=self._publish_response, device_id=DEVICE_ID,
            read_snapshot=_read_battery_health_snapshot, datetime_module=datetime,
        )

def _do_battery_cycles(self, payload: dict) -> None:
        return _power_telemetry_commands.battery_cycles(
            payload, publish_response=self._publish_response, device_id=DEVICE_ID,
            read_snapshot=_read_battery_cycle_snapshot, datetime_module=datetime,
        )

def _do_energy_usage(self, payload: dict) -> None:
        return _power_telemetry_commands.energy_usage(
            payload, publish_response=self._publish_response, device_id=DEVICE_ID,
            read_snapshot=_read_energy_usage_snapshot, datetime_module=datetime,
        )

def _do_sleep_wake_history(self, payload: dict) -> None:
        return _system_event_commands.sleep_wake_history(
            payload, publish_response=self._publish_response, device_id=DEVICE_ID,
            read_snapshot=_read_sleep_wake_history_snapshot, datetime_module=datetime,
        )

def _do_startup_duration(self, payload: dict) -> None:
        return _system_event_commands.startup_duration(
            payload, publish_response=self._publish_response, device_id=DEVICE_ID,
            read_snapshot=_read_startup_duration_snapshot, datetime_module=datetime,
        )

def _do_pending_restart(self, payload: dict) -> None:
        return _system_event_commands.pending_restart(
            payload, publish_response=self._publish_response, device_id=DEVICE_ID,
            read_snapshot=_read_pending_restart_snapshot, datetime_module=datetime,
        )

def _do_os_support(self, payload: dict) -> None:
        return _inventory_telemetry_commands.os_support(
            payload, publish_response=self._publish_response, device_id=DEVICE_ID,
            read_inventory_snapshot=read_inventory_snapshot, snapshot_reader=_read_os_support_identity,
            config_dir=CONFIG_DIR, datetime_module=datetime,
        )

def _do_hardware_inventory(self, payload: dict) -> None:
        return _inventory_telemetry_commands.hardware_inventory(
            payload, publish_response=self._publish_response, device_id=DEVICE_ID,
            read_inventory_snapshot=read_inventory_snapshot, snapshot_reader=_read_hardware_inventory_snapshot,
            config_dir=CONFIG_DIR, datetime_module=datetime,
        )

def _do_driver_inventory(self, payload: dict) -> None:
        return _inventory_telemetry_commands.driver_inventory(
            payload, publish_response=self._publish_response, device_id=DEVICE_ID,
            read_inventory_snapshot=read_inventory_snapshot, snapshot_reader=_read_driver_inventory_snapshot,
            config_dir=CONFIG_DIR, datetime_module=datetime,
        )

def _do_installed_app_inventory(self, payload: dict) -> None:
        return _inventory_telemetry_commands.simple_inventory_snapshot(
            payload, publish_response=self._publish_response, device_id=DEVICE_ID,
            read_snapshot=_read_installed_app_inventory_snapshot, datetime_module=datetime,
            topic="installed_app_inventory", response_type="installed_app_inventory_snapshot",
            schema="xider.installed-app-inventory.v1", feature_id="D01-F16",
            source="windows_uninstall_registry_projection", data_key="apps", snapshot_key="apps",
        )

def _do_startup_inventory(self, payload: dict) -> None:
        return _inventory_telemetry_commands.simple_inventory_snapshot(
            payload, publish_response=self._publish_response, device_id=DEVICE_ID,
            read_snapshot=_read_startup_inventory_snapshot, datetime_module=datetime,
            topic="startup_inventory", response_type="startup_inventory_snapshot",
            schema="xider.startup-inventory.v1", feature_id="D01-F17",
            source="windows_run_keys_and_startup_folder_names", data_key="entries", snapshot_key="entries",
        )

def _do_service_inventory(self, payload: dict) -> None:
        return _inventory_telemetry_commands.simple_inventory_snapshot(
            payload, publish_response=self._publish_response, device_id=DEVICE_ID,
            read_snapshot=_read_service_inventory_snapshot, datetime_module=datetime,
            topic="service_inventory", response_type="service_inventory_snapshot",
            schema="xider.service-inventory.v1", feature_id="D01-F18",
            source="windows_cim_service_status", data_key="services", snapshot_key="services",
        )

def _do_crash_summary(self, payload: dict) -> None:
        return _inventory_telemetry_commands.simple_inventory_snapshot(
            payload, publish_response=self._publish_response, device_id=DEVICE_ID,
            read_snapshot=_read_crash_summary_snapshot, datetime_module=datetime,
            topic="crash_summary", response_type="crash_summary_snapshot",
            schema="xider.crash-summary.v1", feature_id="D01-F19",
            source="windows_event_log_provider_event_aggregate", data_key="events", snapshot_key="events",
        )

def _do_smart_trend(self, payload: dict) -> None:
        return _inventory_telemetry_commands.simple_inventory_snapshot(
            payload, publish_response=self._publish_response, device_id=DEVICE_ID,
            read_snapshot=_read_smart_trend_snapshot, datetime_module=datetime,
            topic="smart_trend", response_type="smart_trend_snapshot", schema="xider.smart-trend.v1",
            feature_id="D01-F20", source="", data_key="disks", snapshot_key="disks",
            source_from_snapshot=True,
        )

def _do_volume_topology(self, payload: dict) -> None:
        return _inventory_telemetry_commands.simple_inventory_snapshot(
            payload, publish_response=self._publish_response, device_id=DEVICE_ID,
            read_snapshot=_read_volume_topology_snapshot, datetime_module=datetime,
            topic="volume_topology", response_type="volume_topology_snapshot", schema="xider.volume-topology.v1",
            feature_id="D01-F21", source="", data_key="volumes", snapshot_key="volumes",
            source_from_snapshot=True,
        )

def _do_clock_drift(self, payload: dict) -> None:
        return _inventory_telemetry_commands.simple_inventory_snapshot(
            payload, publish_response=self._publish_response, device_id=DEVICE_ID,
            read_snapshot=_read_clock_drift_snapshot, datetime_module=datetime,
            topic="clock_drift", response_type="clock_drift_snapshot", schema="xider.clock-drift.v1",
            feature_id="D01-F22", source="", data_key="drift_ms", snapshot_key="drift_ms",
            source_from_snapshot=True,
            additional_data={"sample_ms": "sample_ms"},
        )

def _do_sysinfo(self, payload: dict) -> None:
        """Собрать расширенную информацию о системе и отправить в MQTT."""
        return _system_info_commands.sysinfo(
            payload, publish_response=self._publish_response, device_id=DEVICE_ID,
            platform=PLATFORM, psutil_module=psutil, format_battery=_format_battery,
            primary_mac=_primary_mac, regional_settings=_regional_settings_snapshot,
            socket_module=socket, time_module=time, logger=log,
        )

def _do_volume_history(self, payload: dict) -> None:
        """Report local filesystem usage and cautious trend from prior owner queries."""
        return _storage_telemetry_commands.volume_history(
            payload, publish_response=self._publish_response, device_id=DEVICE_ID,
            psutil_module=psutil, time_module=time, datetime_module=datetime, logger=log,
            volume_measurement=_volume_history_measurement,
            history_minimum_span_seconds=_VOLUME_HISTORY_MIN_SPAN_SECONDS,
        )

def _do_processes(self, payload: dict) -> None:
        """Топ-5 процессов по памяти (совместимо с ботом)."""
        return _system_info_commands.processes(
            payload, publish_response=self._publish_response, device_id=DEVICE_ID,
            psutil_module=psutil, format_processes=_format_processes, logger=log,
        )

def _do_battery(self, payload: dict) -> None:
        """Состояние батареи/питания (для ноутбуков)."""
        return _system_info_commands.battery(
            payload, publish_response=self._publish_response, device_id=DEVICE_ID,
            psutil_module=psutil, format_battery=_format_battery, logger=log,
        )

def _do_services(self, payload: dict) -> None:
        """Сводка по службам Windows (только чтение)."""
        return _system_info_commands.services(
            payload, publish_response=self._publish_response, device_id=DEVICE_ID,
            psutil_module=psutil, format_services=_format_services, logger=log,
        )

def _do_capabilities(self, payload: dict) -> None:
        """Сообщить серверу, какие возможности поддерживает этот клиент.

        НЕ содержит секретов (SHARED_KEY намеренно исключён).
        """
        return _capability_commands.capabilities(
            publish_response=self._publish_response, device_id=DEVICE_ID,
            platform=PLATFORM, version=VERSION, hostname=socket.gethostname(),
            supported_commands=SUPPORTED_COMMANDS, detect_feature_status=_detect_feature_status,
            detect_features=_detect_features, capability_snapshot=_capability_snapshot,
            shell_runner_diagnostic=lambda: _shell_runner_diagnostic(self),
            command_approval_enabled=lambda: _command_approval_protocol_enabled(self),
            logger=log,
        )

def _do_permission_settings(self, payload: dict) -> dict:
        """Request Windows location consent locally, or open a fixed privacy page."""
        return _permission_commands.open_permission_settings(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            opener=getattr(os, "startfile", None),
        )

def _do_disks(self, payload: dict) -> None:
        """Список дисков с размерами."""
        disks = _format_disks(psutil.disk_partitions())
        self._publish_response("disks", {
            "type": "disks",
            "device_id": DEVICE_ID,
            "disks": disks,
            "lines": [
                f"{d['device']} {d['free_gb']}/{d['total_gb']} ГБ свободно ({d['percent']}%)"
                for d in disks
            ],
        })
        log.info("Список дисков отправлен")

        log.info("Список дисков отправлен")

def _do_startup_list(self, payload: dict) -> None:
        _system_info_commands.startup_list(
            payload, publish_response=self._publish_response, device_id=DEVICE_ID,
            run_without_console=_run_without_console, check_output=subprocess.check_output,
            devnull=subprocess.DEVNULL, parse_reg_query=_parse_reg_query,
        )

def _do_env_get(self, payload: dict) -> None:
        _system_info_commands.env_get(
            payload, publish_response=self._publish_response,
            device_id=DEVICE_ID, collect_env=_collect_env,
        )

def _do_sys_installed_apps(self, payload: dict) -> None:
        """Установленное ПО: Uninstall-ключи реестра 64-bit + 32-bit (WOW6432Node)."""
        return _system_info_commands.installed_apps(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            run_without_console=_run_without_console, check_output=subprocess.check_output,
            devnull=subprocess.DEVNULL,
        )

def _do_sys_history_cmd(self, payload: dict) -> None:
        """Последние команды из истории PowerShell (PSReadLine)."""
        return _system_info_commands.history_cmd(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            clamp=_clamp, os_module=os, open_file=open,
        )

def _do_sys_uptime(self, payload: dict) -> None:
        """Время непрерывной работы системы (Uptime)."""
        return _system_info_commands.sys_uptime(
            payload, publish_response=self._publish_response, device_id=DEVICE_ID,
            psutil_module=psutil, time_module=time,
        )

def _do_sys_clean_temp(self, payload: dict) -> None:
        """Remove expired regular files from XIDER's private media-temp namespace only."""
        return _maintenance_commands.clean_temp(
            payload, publish_response=self._publish_response, device_id=DEVICE_ID,
            cleanup_stale_media_temp=media_temp.cleanup_stale_media_temp,
        )

def _do_service_probe(self, payload: dict) -> None:
        _network_commands.service_probe_command(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            parse_target=tls_certificate.parse_target, probe_service=service_probe.probe_service,
            source=service_probe.SOURCE, datetime_module=datetime,
        )
