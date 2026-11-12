"""Agent bootstrap, trusted command gate and runtime entry point."""

from __future__ import annotations

from types import FunctionType


def bind_runtime_functions(runtime_globals):
    """Bind runtime functions to the legacy agent module namespace."""
    function_names = {'_shell_approval_runtime', '_command_approval_source_root', '_command_approval_protocol_enabled', '_reserve_shell_approval', 'main', 'acquire_instance_lock', '_load_command_approval_component', '_claim_bootstrap_lock', '_load_trusted_command_module_loader', '_same_absolute_path', 'setup_logging', '_shell_runner_diagnostic'}
    bound = {}
    for name in function_names:
        function = globals()[name]
        runtime_function = FunctionType(
            function.__code__, runtime_globals, function.__name__, function.__defaults__, function.__closure__
        )
        runtime_function.__kwdefaults__ = function.__kwdefaults__
        runtime_function.__annotations__ = function.__annotations__
        bound[name] = runtime_function
    return bound


def _claim_bootstrap_lock() -> bool:
    """Hold the shared per-user lock before loading replaceable agent modules."""
    global _bootstrap_lock_file
    try:
        lock_path = Path.home() / ".xgent" / "agent.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock_path, "a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        try:
            handle.close()
        except (NameError, OSError):
            pass
        return False
    _bootstrap_lock_file = handle
    return True


def acquire_instance_lock() -> bool:
    """X-LOCK: only one interactive Windows agent per user session.

    The OS releases this byte-range lock if the process exits unexpectedly.
    All installations use the same user config directory, so an old desktop
    copy and a new LocalAppData copy cannot both own the tray once updated.
    """
    global _instance_lock_file
    if _bootstrap_lock_file is not None:
        _instance_lock_file = _bootstrap_lock_file
        return True
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = CONFIG_DIR / "agent.lock"
    handle = open(lock_path, "a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return False
    _instance_lock_file = handle
    return True


def _same_absolute_path(left, right) -> bool:
    return os.path.normcase(os.path.abspath(os.fspath(left))) == os.path.normcase(os.path.abspath(os.fspath(right)))


def _load_trusted_command_module_loader():
    """Bootstrap the one shared loader without importing it by a shadowable name."""
    global _COMMAND_MODULE_LOADER
    if _COMMAND_MODULE_LOADER is not None:
        return _COMMAND_MODULE_LOADER
    module_name = "command_module_loader"
    try:
        if _NUITKA_RUNTIME:
            import command_module_loader as module
            runtime_info = globals().get("__compiled__")
            loader_info = getattr(module, "__compiled__", None)
            runtime_dir = getattr(runtime_info, "containing_dir", None)
            loader_dir = getattr(loader_info, "containing_dir", None)
            if (
                loader_info is None
                or not isinstance(runtime_dir, (str, os.PathLike))
                or not isinstance(loader_dir, (str, os.PathLike))
                or not _same_absolute_path(runtime_dir, loader_dir)
            ):
                raise ImportError("Shared command loader is not compiled into the active Nuitka bundle")
            _COMMAND_MODULE_LOADER = module
            return module
        if _BUNDLED_RUNTIME:
            runtime = sys.modules.get("pyimod02_importers")
            finder_type = getattr(runtime, "PyiFrozenFinder", None)
            loader_type = getattr(runtime, "PyiFrozenLoader", None)
            archive = getattr(runtime, "pyz_archive", None)
            meipass = getattr(sys, "_MEIPASS", None)
            if (
                not isinstance(finder_type, type)
                or finder_type.__name__ != "PyiFrozenFinder"
                or finder_type.__module__ != "pyimod02_importers"
                or not isinstance(loader_type, type)
                or loader_type.__name__ != "PyiFrozenLoader"
                or loader_type.__module__ != "pyimod02_importers"
                or archive is None
                or not isinstance(getattr(archive, "toc", None), dict)
                or not isinstance(meipass, (str, os.PathLike))
                or not os.fspath(meipass)
            ):
                raise ImportError("Trusted PyInstaller loader is unavailable")
            finder = finder_type(os.fspath(meipass))
            spec = finder.find_spec(module_name)
            loader = getattr(spec, "loader", None)
            expected_origin = Path(meipass) / f"{module_name}.py"
            if (
                type(finder) is not finder_type
                or getattr(finder, "_pyz_archive", None) is not archive
                or not _same_absolute_path(getattr(finder, "_path", ""), meipass)
                or getattr(spec, "name", None) != module_name
                or type(loader) is not loader_type
                or getattr(loader, "name", None) != module_name
                or getattr(loader, "_pyz_archive", None) is not archive
                or getattr(loader, "_pyz_entry_name", None) != module_name
                or getattr(loader, "_is_package", None) is not False
                or module_name not in archive.toc
                or not getattr(loader, "path", None)
                or not getattr(spec, "origin", None)
                or not _same_absolute_path(loader.path, expected_origin)
                or not _same_absolute_path(spec.origin, expected_origin)
                or getattr(spec, "submodule_search_locations", None) is not None
            ):
                raise ImportError("Shared command loader is not bundled in the active PYZ")
        else:
            root = _command_approval_source_root().resolve(strict=True)
            expected = root / f"{module_name}.py"
            if expected.is_symlink() or not expected.is_file() or not _same_absolute_path(expected.resolve(strict=True), expected):
                raise ImportError("Shared command loader is missing or outside XGENT-MCS")
            spec = importlib.util.spec_from_file_location(module_name, expected)
            if (
                spec is None
                or spec.name != module_name
                or type(spec.loader) is not importlib.machinery.SourceFileLoader
                or not spec.origin
                or not _same_absolute_path(spec.origin, expected)
                or not _same_absolute_path(spec.loader.get_filename(module_name), expected)
            ):
                raise ImportError("Shared command loader has an unexpected source spec")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _COMMAND_MODULE_LOADER = module
        return module
    except ImportError:
        raise
    except Exception as exc:
        raise ImportError("Shared command loader could not be trusted") from exc


def _load_command_approval_component(module_name: str):
    """Load only the canonical MCS verifier/replay helpers; no crypto copy here."""
    return _load_trusted_command_module_loader().load_component(
        module_name,
        source_root=_command_approval_source_root(),
        frozen=_BUNDLED_RUNTIME,
    )


def _command_approval_source_root():
    """Use the transaction-owned helper set, or the initial checkout's shared set."""
    windows = Path(__file__).resolve().parent
    candidate = windows / "command_module_loader.py"
    if candidate.exists() or candidate.is_symlink():
        return windows
    return windows.parent / "XGENT-MCS"


def _shell_approval_runtime(client):
    """Load the canonical MCS verifier/replay helper lazily; no crypto copy here."""
    try:
        signing = _load_command_approval_component("command_signing")
        replay = _load_command_approval_component("command_replay")
        _load_command_approval_component("shell_execution")
        ledger = getattr(client, "_command_replay_ledger", None)
        if ledger is None:
            ledger = replay.CommandReplayLedger()
            client._command_replay_ledger = ledger
        return signing, ledger
    except Exception:
        return None, None


def _command_approval_protocol_enabled(client) -> bool:
    """Advertise schema 1 only when its public keyset and replay store are usable."""
    signing, ledger = _shell_approval_runtime(client)
    if signing is None or ledger is None:
        return False
    try:
        return signing.trusted_command_keys_ready() and ledger.ensure_available(now=int(time.time()))
    except Exception:
        return False


def _shell_runner_diagnostic(client) -> dict:
    """Report runner prerequisites from local metadata only; do not execute shell commands."""
    protocol_state = "ready" if _command_approval_protocol_enabled(client) else "unavailable"
    try:
        execution = _load_command_approval_component("shell_execution")
        executor_state = "available" if callable(getattr(execution, "execute_shell", None)) else "unavailable"
    except Exception:
        execution = None
        executor_state = "unavailable"
    if PLATFORM == "macOS":
        runner_path = "/bin/sh"
    else:
        system_root = os.environ.get("SystemRoot") or r"C:\Windows"
        expected = str(Path(system_root) / "System32" / "cmd.exe")
        configured = os.environ.get("COMSPEC")
        runner_path = expected if not configured or os.path.normcase(os.path.abspath(configured)) == os.path.normcase(os.path.abspath(expected)) else ""
    if execution is None or not callable(getattr(execution, "inspect_shell_runner", None)):
        runner = {"runner_state": "unknown", "permission_state": "unknown"}
    else:
        runner = execution.inspect_shell_runner(runner_path)
    return {
        "schema": 1,
        "protocol_state": protocol_state,
        "executor_state": executor_state,
        "runner_state": runner["runner_state"],
        "permission_state": runner["permission_state"],
    }


def _reserve_shell_approval(client, payload: dict) -> bool:
    """Verify exact command bytes and persist the signed request ID before execution."""
    if type(payload) is not dict:
        return False
    command = payload.get("command")
    envelope = payload.get("approval")
    if not isinstance(command, str) or type(envelope) is not dict:
        return False
    signing, ledger = _shell_approval_runtime(client)
    if signing is None or ledger is None:
        return False
    try:
        now = int(time.time())
        verified = signing.verify_approved_command(
            envelope,
            command=command,
            expected_device_id=DEVICE_ID,
            now=now,
        )
        if verified is None:
            return False
        return ledger.reserve(verified["request_id"], verified["expires_at"], now=now)
    except Exception:
        return False


def setup_logging() -> None:
    r"""Лог в %USERPROFILE%\.xgent\xgent.log (с ротацией) + консоль при наличии."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    handlers = [
        RotatingFileHandler(
            CONFIG_DIR / "xgent.log",
            maxBytes=500_000,
            backupCount=3,
            encoding="utf-8",
        )
    ]
    if "--console" in sys.argv:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def main() -> None:
    setup_logging()
    if not acquire_instance_lock():
        log.info("X-LOCK: другой экземпляр Windows-агента уже запущен; второй не стартует")
        return
    client = XgentClient()
    if EARLY_UPDATE_STATE == "pending":
        def _rollback_if_unhealthy() -> None:
            if client._update_health.wait(120):
                return
            with client._subscription_lock:
                if client._update_health.is_set() or not client._running.is_set():
                    return
                state_path = CONFIG_DIR / UPDATE_STATE_NAME
                if not state_path.exists():
                    log.error("Нет журнала обновления и нет MQTT health подтверждения; откат невозможен")
                    return
                try:
                    rollback_unhealthy_agent(state_path, Path(__file__).resolve().parent)
                except Exception:
                    log.exception("Критическая ошибка: откат обновления Windows-агента не завершён")
                    return
                log.error("Новая версия не подключилась к MQTT за 120 секунд; возвращаю предыдущие файлы")
                os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])

        threading.Thread(
            target=_rollback_if_unhealthy,
            name="xgent-wds-update-health",
            daemon=True,
        ).start()
    if "--console" in sys.argv or "--no-tray" in sys.argv:
        log.info("Запуск XGENT в консольном режиме")
        client.start()
        try:
            while client.is_running:
                time.sleep(1)
        except KeyboardInterrupt:
            client.request_stop()
        finally:
            client.stop()
        return

    try:
        import pystray
        from pystray import Menu, MenuItem
    except ImportError:
        log.warning("pystray недоступен — запуск без значка в трее")
        client.start()
        try:
            while client.is_running:
                time.sleep(1)
        except KeyboardInterrupt:
            client.request_stop()
        finally:
            client.stop()
        return

    icon = pystray.Icon(
        "xgent-wds",
        create_image(),
        "XGENT",
        menu=Menu(
            MenuItem("🟢 XGENT: онлайн", None, enabled=False),
            Menu.SEPARATOR,
            MenuItem("Остановить XGENT", lambda *_: client.request_stop()),
        ),
    )
    client.on_stop_requested = icon.stop
    client.start()
    icon.run()
    client.stop()
