"""MQTT client orchestration and command dispatch."""

from __future__ import annotations

from types import FunctionType


class XgentClient:
    """MQTT-клиент: подписка на команды, heartbeat и выполнение действий."""

    def __init__(self, on_stop_requested=None) -> None:
        self.on_stop_requested = on_stop_requested
        self._running = threading.Event()
        self._running.set()
        self._update_health = threading.Event()
        self._mqtt_connected = False
        self._mqtt_session_id = uuid.uuid4().hex
        self._subscription_lock = threading.RLock()
        self._pending_command_subacks = set()
        self._command_subscriptions_failed = False
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"xgent-wds-{DEVICE_ID}",
            protocol=mqtt.MQTTv311,
            transport=MQTT_TRANSPORT,
        )
        if MQTT_TRANSPORT == "websockets":
            self._client.ws_set_options(path=MQTT_WS_PATH)
        # Экспоненциальный бэкофф переподключения (1с -> 120с).
        self._client.reconnect_delay_set(min_delay=1, max_delay=120)
        self._client.on_connect = self._on_connect
        self._client.on_connect_fail = self._on_connect_fail
        self._client.on_disconnect = self._on_disconnect
        self._client.on_subscribe = self._on_subscribe
        self._client.on_message = self._on_message
        self._heartbeat = threading.Thread(
            target=self._heartbeat_loop, name="xgent-heartbeat", daemon=True
        )
        self._sysinfo_schedule_wake = threading.Event()
        self._sysinfo_schedule = threading.Thread(
            target=self._sysinfo_schedule_loop, name="xgent-sysinfo-schedule", daemon=True,
        )
        self._network_traffic_schedule_wake = threading.Event()
        self._network_traffic_schedule = threading.Thread(
            target=self._network_traffic_schedule_loop, name="xgent-network-traffic-schedule", daemon=True,
        )
        self._interface_status_history_wake = threading.Event()
        self._interface_status_history_thread = threading.Thread(
            target=self._interface_status_history_loop, name="xgent-interface-status-history", daemon=True,
        )
        self._standby = False
        self._command_context = threading.local()
        self._active_prank_cancels: dict[str, object] = {}
        self._active_prank_cancels_lock = threading.RLock()
        self._open_url_operations: dict[str, dict] = {}
        self._open_url_operations_lock = threading.Lock()
        for method_name, handler in _prank_commands.bind_handler_methods(self, globals()).items():
            setattr(self, method_name, handler)
        for method_name, handler in _lifecycle.bind_handler_methods(self, globals()).items():
            setattr(self, method_name, handler)
        self._handlers = build_command_handlers(self)
        register_command_handlers(self._handlers)
        assert set(self._handlers) == set(SUPPORTED_COMMANDS)


    def __getattr__(self, name: str):
        if name.startswith("_do_prank_"):
            handler = _prank_commands.bind_handler_methods(self, globals()).get(name)
            if handler is not None:
                setattr(self, name, handler)
                return handler
        if name in {"_do_agent_update", "_do_uninstall_agent"}:
            handler = _lifecycle.bind_handler_methods(self, globals()).get(name)
            if handler is not None:
                setattr(self, name, handler)
                return handler
        for command_module in (_network_commands, _browser_commands):
            handler = command_module.bind_handler_methods(self, globals()).get(name)
            if handler is not None:
                setattr(self, name, handler)
                return handler
        handler = _transport.bind_handler_methods(self, globals()).get(name)
        if handler is not None:
            setattr(self, name, handler)
            return handler
        handler = _telemetry_handlers.bind_handler_methods(self, globals()).get(name)
        if handler is not None:
            setattr(self, name, handler)
            return handler
        handler = _device_interaction.bind_handler_methods(self, globals()).get(name)
        if handler is not None:
            setattr(self, name, handler)
            return handler
        raise AttributeError(f"{type(self).__name__!s} has no attribute {name!r}")


    @property
    def is_running(self) -> bool:
        return self._running.is_set()





    # ---------- колбэки paho-mqtt ----------



    def _restart_existing_guardian_after_update(self) -> None:
        """Reload the updated Guardian script only when its owner task exists."""
        with self._subscription_lock:
            if not self._running.is_set() or not self._update_health.is_set():
                return
            try:
                if not _scheduled_task_exists(WINDOWS_GUARDIAN_TASK_NAME):
                    log.info("Windows Guardian не зарегистрирован; обновление не запускает его автоматически.")
                    return
                # /End may report that an idle task was not running. /Run is the
                # authoritative result: it starts the already registered task
                # with its existing owner, action, and permissions.
                _run_without_console(subprocess.run,
                    ["schtasks.exe", "/End", "/TN", WINDOWS_GUARDIAN_TASK_NAME],
                    capture_output=True, text=True, check=False, timeout=10,
                )
                time.sleep(0.5)
                started = _run_without_console(subprocess.run,
                    ["schtasks.exe", "/Run", "/TN", WINDOWS_GUARDIAN_TASK_NAME],
                    capture_output=True, text=True, check=False, timeout=10,
                )
                if started.returncode != 0:
                    raise RuntimeError((started.stderr or started.stdout or "schtasks /Run failed").strip())
                log.info("Зарегистрированный Windows Guardian перезапущен после подтверждения обновления агента.")
            except Exception as exc:
                log.exception("Агент обновлён и здоров, но Windows Guardian не перезапущен")
                try:
                    self._publish_response("output", {
                        "type": "agent_update", "device_id": DEVICE_ID, "ok": True,
                        "state": "guardian_restart_failed", "health_check": "passed",
                        "guardian_restart": "failed",
                        "text": (
                            "⚠️ Обновление агента подтверждено, MQTT работает. "
                            f"Перезапуск Guardian не выполнен: {exc}. Он может продолжать работу со старым кодом."
                        ),
                    })
                except Exception:
                    log.exception("Не удалось отправить предупреждение о перезапуске Windows Guardian")




    # ---------- маршрутизация команд ----------






    # ---------- heartbeat и статус ----------











    # ---------- действия ----------








    def _do_status_request(self, payload: dict) -> None:
        self._publish_status()












































    def _do_agent_files(self, payload: dict) -> None:
        """Return allowlisted component metadata only; never return file contents."""
        return _filesystem_commands.agent_files(
            payload, device_id=DEVICE_ID, agent_version=VERSION,
            collect_inventory=agent_file_inventory.collect_agent_file_inventory,
            schema=agent_file_inventory.SCHEMA,
            publish_response=self._publish_response, logger=log,
        )

    def _do_agent_file_get(self, payload: dict) -> None:
        """Return bounded contents for a fixed, readable text-file ID only."""
        return _filesystem_commands.agent_file_get(
            payload, device_id=DEVICE_ID,
            read_allowlisted_text_file=agent_file_inventory.read_allowlisted_text_file,
            response_schema=agent_file_inventory.TEXT_FILE_SCHEMA,
            fullmatch=re.fullmatch, publish_response=self._publish_response,
        )

    def _do_agent_logs(self, payload: dict) -> None:
        """Return a bounded, redacted tail of the local agent log on owner request."""
        return _diagnostic_commands.read_agent_logs(
            config_dir=CONFIG_DIR, device_id=DEVICE_ID, publish_response=self._publish_response,
        )




    def _do_kill_process(self, payload: dict) -> None:
        """Завершить процесс по PID (terminate, затем kill)."""
        return _system_info_commands.kill_process(
            payload, publish_response=self._publish_response, device_id=DEVICE_ID,
            psutil_module=psutil, logger=log,
        )


    # ---------------------------------------------------------------
    #  Новые команды волны 1: файлы, система, приколы
    # ---------------------------------------------------------------

    def _do_dir_list(self, payload: dict) -> None:
        path = (payload.get("path") or "~").strip()
        result = _list_directory(path)
        self._publish_response("dir_list", {"type": "dir_list", "device_id": DEVICE_ID, **result})
        log.info("dir_list: %s (найдено %s)", result.get("path"), result.get("total"))

    def _do_file_get(self, payload: dict) -> None:
        return _filesystem_commands.file_get(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            getsize=os.path.getsize, open_file=open, basename=os.path.basename,
            base64_module=base64, logger=log,
        )

    def _do_file_put(self, payload: dict) -> None:
        return _filesystem_commands.file_put(
            payload, device_id=DEVICE_ID, decode_payload=_decode_file_put_payload,
            write_atomic=_write_file_put_atomic, basename=os.path.basename,
            expanduser=os.path.expanduser, expandvars=os.path.expandvars,
            join=os.path.join, publish_response=self._publish_response, logger=log,
        )

    def _do_file_del(self, payload: dict) -> None:
        return _filesystem_commands.file_del(
            payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            expanduser=os.path.expanduser, expandvars=os.path.expandvars,
            remove=os.remove,
        )

    def _do_find_file(self, payload: dict) -> None:
        return _filesystem_commands.find_file(
            payload, device_id=DEVICE_ID, find_matches=_find_matches,
            publish_response=self._publish_response,
        )





    def _get_master_volume(self):
        """Универсальное получение интерфейса IAudioEndpointVolume (поддержка pycaw всех версий)."""
        from comtypes import CLSCTX_ALL
        from ctypes import cast, POINTER
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        speakers = AudioUtilities.GetSpeakers()
        if hasattr(speakers, "EndpointVolume"):
            return speakers.EndpointVolume
        dev = getattr(speakers, "_dev", speakers)
        interface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return cast(interface, POINTER(IAudioEndpointVolume))










    def _do_shell(self, payload: dict) -> None:
        """Выполнить shell-команду и вернуть вывод (лимит 20 сек)."""
        return _maintenance_commands.shell_command(
            self, payload, device_id=DEVICE_ID, publish_response=self._publish_response,
            reserve_approval=_reserve_shell_approval,
            load_component=_load_command_approval_component, logger=log,
        )

    def _do_shell_cancel(self, payload: dict) -> None:
        """Stop only a currently running, previously approved shell request."""
        return _maintenance_commands.shell_cancel(
            payload, device_id=DEVICE_ID,
            load_component=_load_command_approval_component,
            publish_response=self._publish_response,
        )


    def _do_proc_kill_name(self, payload: dict) -> None:
        return _system_info_commands.proc_kill_name(
            payload, publish_response=self._publish_response, device_id=DEVICE_ID,
            kill_by_name=lambda name: _system_info_commands.kill_by_name(
                name, os_module=os, psutil_module=psutil,
            ),
            logger=log,
        )


    def _do_usb_devices(self, payload: dict) -> None:
        _system_info_commands.usb_devices(
            payload, publish_response=self._publish_response, device_id=DEVICE_ID,
            run_without_console=_run_without_console, check_output=subprocess.check_output,
            devnull=subprocess.DEVNULL,
        )






    def _do_msgbox_spam(self, payload: dict) -> None:
        return _prank_commands.msgbox_spam(
            payload, device_id=DEVICE_ID,
            show_message_box=ctypes.windll.user32.MessageBoxW,
            threading_module=threading, publish_response=self._publish_response,
        )







    def _do_stop(self, payload: dict) -> None:
        log.info("Получена команда остановки")
        self.request_stop()




















    # ---------- Режим ожидания (Watchdog) и автозапуск ----------






    # ---------- Системные утилиты и диагностика ----------




























    # ---------- Расширенная матрица приколов (40 приколов) ----------























    def _restore_saved_prank_wallpaper(self) -> tuple[bool, str]:
        """Restore the saved wallpaper without publishing a second command result."""
        return _prank_commands.restore_wallpaper(
            config_dir=CONFIG_DIR, ctypes_module=ctypes, os_module=os, logger=log,
        )























    def _register_prank_cancel(self, scene_id: str, callback) -> None:
        if not isinstance(scene_id, str) or not scene_id or not callable(callback):
            raise ValueError("prank cancellation registration is invalid")
        with self._active_prank_cancels_lock:
            self._active_prank_cancels[scene_id] = callback

    def _finish_prank_cancel(self, scene_id: str) -> None:
        with self._active_prank_cancels_lock:
            self._active_prank_cancels.pop(scene_id, None)

    def _cancel_active_pranks(self) -> list[str]:
        with self._active_prank_cancels_lock:
            entries = list(self._active_prank_cancels.items())
            self._active_prank_cancels.clear()
        cancelled = []
        for scene_id, callback in entries:
            try:
                callback()
                cancelled.append(scene_id)
            except Exception:
                log.exception("Не удалось отменить прикол %s", scene_id)
        return cancelled








    def _publish_update_progress(self, operation_id: str, state: str) -> None:
        self._publish_response("update_progress", {
            "type": "agent_update_progress", "device_id": DEVICE_ID,
            "operation_id": operation_id, "update_state": state,
        })


def bind_runtime_class(runtime_globals):
    """Bind client methods to the legacy module namespace for compatibility."""
    client_type = XgentClient
    client_type.__module__ = runtime_globals.get("__name__", client_type.__module__)
    for name, value in tuple(vars(client_type).items()):
        if isinstance(value, FunctionType):
            rebound = FunctionType(
                value.__code__, runtime_globals, value.__name__, value.__defaults__, value.__closure__
            )
            rebound.__kwdefaults__ = value.__kwdefaults__
            rebound.__annotations__ = value.__annotations__
            setattr(client_type, name, rebound)
        elif isinstance(value, property) and value.fget is not None:
            getter = value.fget
            rebound = FunctionType(
                getter.__code__, runtime_globals, getter.__name__, getter.__defaults__, getter.__closure__
            )
            rebound.__kwdefaults__ = getter.__kwdefaults__
            rebound.__annotations__ = getter.__annotations__
            setattr(client_type, name, property(rebound, value.fset, value.fdel, value.__doc__))
    return client_type
