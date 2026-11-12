"""Agent update and uninstall command implementations."""

from __future__ import annotations

from types import FunctionType, MethodType


def bind_handler_methods(client, runtime_globals):
    bound = {}
    for name, handler in globals().items():
        if name not in {"_do_agent_update", "_do_uninstall_agent"} or not isinstance(handler, FunctionType):
            continue
        runtime_handler = FunctionType(
            handler.__code__, runtime_globals, handler.__name__, handler.__defaults__, handler.__closure__
        )
        runtime_handler.__kwdefaults__ = handler.__kwdefaults__
        runtime_handler.__annotations__ = handler.__annotations__
        bound[name] = MethodType(runtime_handler, client)
    return bound


def remove_scheduled_task(task_name, *, stop_running, run_without_console, run, scheduled_task_exists):
    try:
        if stop_running:
            run_without_console(
                run, ["schtasks.exe", "/End", "/TN", task_name],
                capture_output=True, text=True, check=False,
            )
        delete = run_without_console(
            run, ["schtasks.exe", "/Delete", "/TN", task_name, "/F"],
            capture_output=True, text=True, check=False,
        )
    except OSError as exc:
        return False, str(exc)

    detail = (delete.stdout or delete.stderr or "").strip()
    if delete.returncode == 0:
        return True, detail
    # Task Scheduler output is localized; query state instead of matching text.
    if not scheduled_task_exists(task_name):
        return True, detail
    return False, detail or f"Scheduled Task is still registered: {task_name}"


def _do_agent_update(self, payload: dict) -> None:
        """Install a signed source release transactionally; frozen bundles fail closed."""
        if payload.get("update") and _BUNDLED_RUNTIME:
            self._publish_response("output", {
                "type": "agent_update",
                "device_id": DEVICE_ID,
                "ok": False,
                "state": "unsupported_package",
                "operation_id": _frozen_update_operation_id(payload, getattr(getattr(self, "_command_context", None), "cmd_id", None)),
                "update_state": "failed",
                "text": (
                    "⚠️ Эта сборка Windows EXE пока не поддерживает транзакционное "
                    "обновление. Ничего не менял; файлы пакета не тронуты."
                ),
            })
            return

        if payload.get("update"):
            operation_id = update_operation_id(payload, getattr(getattr(self, "_command_context", None), "cmd_id", None))
            if not isinstance(payload.get("manifest_digest"), str) or not re.fullmatch(r"[0-9a-f]{64}", payload["manifest_digest"]):
                self._publish_response("output", {"type": "agent_update", "device_id": DEVICE_ID,
                    "ok": False, "state": "manifest_pin_required", "update_state": "failed",
                    "operation_id": operation_id, "text": "Обновление не начато: требуется точный manifest digest."})
                return
            self._publish_update_progress(operation_id, "queued")
            script_dir = Path(__file__).resolve().parent
            files = (
                "xgent_wds.py",
                "xider_guardian_wds.py",
                "config.py",
                "crypto.py",
                "xgencrypto.py",
                "release_signature.py",
                "update_package.py",
                "command_module_loader.py", "command_signing.py", "command_replay.py", "shell_execution.py", "location_adapter.py", "camera_devices.py", "camera_series.py", "microphone_devices.py", "microphone_level.py", "audio_output_control.py", "media_temp.py", "media_history.py", "media_request_control.py", "display_contact_sheet.py", "window_capture.py", "dns_diagnostics.py", "dns_settings.py", "dns_comparison.py", "dnssec_check.py", "gateway_probe.py", "route_trace.py", "path_mtu.py", "captive_portal.py", "ipv6_connectivity.py", "tls_certificate.py", "service_probe.py", "latency_series.py", "packet_loss_history.py", "jitter_measurement.py", "network_throughput.py", "network_link_speed.py", "route_lookup.py", "endpoint_batch.py", "wifi_quality.py", "wifi_channel.py", "wifi_connection_history.py", "proxy_status.py", "vpn_route.py", "network_traffic.py", "network_traffic_schedule.py", "interface_status_history.py",
            )
            backup_dir = CONFIG_DIR / "agent-backups" / (
                f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns()}"
            )
            installed = False
            try:
                with tempfile.TemporaryDirectory(prefix="xgent-wds-update-") as tmp:
                    archive = Path(tmp) / "XIDER-source.zip"
                    manifest = Path(tmp) / "release-manifest.json"
                    self._publish_update_progress(operation_id, "downloading")
                    release_tag = download_verified_source_archive(
                        manifest,
                        archive,
                        release_tag=payload.get("release_tag"),
                        expected_manifest_digest=payload.get("manifest_digest"),
                    )
                    self._publish_update_progress(operation_id, "verifying")
                    source = extract_agent_files(
                        archive,
                        Path(tmp) / "agent-stage",
                        files,
                        component_dir="XGENT-WDS",
                    )
                    for name in files:
                        candidate = source / name
                        ast.parse(candidate.read_text(encoding="utf-8"), filename=str(candidate), mode="exec")
                    self._publish_update_progress(operation_id, "installing")
                    backup_dir = install_agent_files(
                        source,
                        script_dir,
                        backup_dir,
                        files,
                        transaction_path=CONFIG_DIR / UPDATE_STATE_NAME,
                        operation_id=operation_id, release_tag=release_tag,
                        manifest_digest=payload.get("manifest_digest"),
                    )
                    installed = True

                self._publish_response("output", {
                    "type": "agent_update",
                    "device_id": DEVICE_ID,
                    "ok": True,
                    "state": "restarting",
                    "release_tag": release_tag,
                    "health_check": "pending",
                    "operation_id": operation_id, "update_state": "reconnecting",
                    "text": (
                        f"📦 Подписанный релиз {release_tag} проверен; исходники Windows-агента "
                        f"установлены. Копия прежних файлов: {backup_dir.name}. Перезапускаюсь; "
                        "подтверждение MQTT-здоровья ещё ожидается."
                    ),
                })

                def _reboot_agent() -> None:
                    time.sleep(1.5)
                    try:
                        os.execv(
                            sys.executable,
                            [sys.executable, str(script_dir / "xgent_wds.py"), *sys.argv[1:]],
                        )
                    except Exception as restart_error:
                        log.exception("Не удалось перезапустить Windows-агент после обновления")
                        rollback_state = "rolled_back"
                        rollback_detail = ""
                        self._publish_update_progress(operation_id, "rolling_back")
                        try:
                            rollback_unhealthy_agent(
                                CONFIG_DIR / UPDATE_STATE_NAME,
                                script_dir,
                            )
                        except Exception as rollback_error:
                            rollback_state = "failed"
                            rollback_detail = f" Откат неполон: {rollback_error}"
                            log.exception("Не удалось вернуть прежние файлы после ошибки перезапуска")
                        self._publish_response("output", {
                            "type": "agent_update",
                            "device_id": DEVICE_ID,
                            "ok": False,
                            "state": rollback_state, "update_state": rollback_state,
                            "operation_id": operation_id,
                            **({"id": payload["id"]} if payload.get("id") else {}),
                            "text": f"❌ Перезапуск не удался: {restart_error}.{rollback_detail}",
                        })

                threading.Thread(target=_reboot_agent, name="xgent-wds-update-restart", daemon=True).start()
            except Exception as exc:
                log.exception("Windows agent self-update failed")
                outcome = "failed"
                rollback_detail = ""
                if installed:
                    self._publish_update_progress(operation_id, "rolling_back")
                    try:
                        rollback_unhealthy_agent(CONFIG_DIR / UPDATE_STATE_NAME, script_dir)
                        outcome = "rolled_back"
                    except Exception as rollback_error:
                        rollback_detail = f" Автоматический откат неполон: {rollback_error}"
                receipt = read_agent_update_result(CONFIG_DIR / UPDATE_STATE_NAME)
                if receipt and receipt.get("operation_id") == operation_id and receipt.get("update_state") in {"rolled_back", "failed"}:
                    outcome = receipt["update_state"]
                self._publish_response("output", {
                    "type": "agent_update",
                    "device_id": DEVICE_ID,
                    "ok": False,
                    "state": "failed",
                    "operation_id": operation_id, "update_state": outcome,
                    "text": (
                        f"❌ Обновление не подтверждено: {exc}.{rollback_detail} "
                        "Перед новой попыткой проверь журнал агента и резервную копию."
                    ),
                })
            return

        restart = payload.get("restart", False)
        pid = os.getpid()
        exe_path = sys.executable
        text = (
            f"🔄 <b>Агент XGENT v{VERSION}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• Платформа: Windows ({platform.system()} {platform.release()})\n"
            f"• Процесс: <code>{html.escape(exe_path)}</code>\n"
            f"• PID процесса: <code>{pid}</code>\n"
            f"• Набор команд: <b>{len(SUPPORTED_COMMANDS)} функций</b> (полная синхронизация)\n"
            f"• Статус: 🟢 В сети, готов к приёму команд"
        )
        if restart:
            text += "\n\n⚠️ <i>Инициирован перезапуск процесса агента...</i>"
            def _reboot_agent():
                time.sleep(1.5)
                _run_without_console(subprocess.Popen, [sys.executable] + sys.argv)
                os._exit(0)
            threading.Thread(target=_reboot_agent, daemon=True).start()
        self._publish_response("output", {"type": "agent_update", "device_id": DEVICE_ID, "ok": True, "text": text})

def _do_uninstall_agent(self, payload: dict) -> None:
        """Полное удаление агента с ПК: автозапуск, конфиги, логи и завершение процесса."""
        log.warning("Получена команда полного удаления агента с ПК!")
        try:
            set_guardian_desired_running(False)
        except Exception as exc:
            log.exception("Could not persist Guardian stop intent before uninstall")
            self._publish_response("output", {
                "type": "uninstall_agent", "device_id": DEVICE_ID, "ok": False,
                "text": f"❌ Удаление отменено: не удалось сохранить состояние Guardian: {exc}",
            })
            return

        # Unregister the worker task without ending its current instance: this
        # callback must stay alive long enough to publish the correlated ACK.
        worker_removed, worker_detail = _remove_scheduled_task(
            WINDOWS_TASK_NAME, stop_running=False,
        )
        if not worker_removed:
            try:
                set_guardian_desired_running(True)
            except Exception:
                log.exception("Could not restore Guardian intent after task removal failure")
            self._publish_response("output", {
                "type": "uninstall_agent", "device_id": DEVICE_ID, "ok": False,
                "text": f"❌ Удаление отменено: задача агента не удалена ({worker_detail}). Настройки сохранены.",
            })
            return

        # The Guardian is a different process, so stop it before unregistering
        # its task. If that cannot be confirmed, preserve the worker state and
        # report failure instead of pretending the uninstall completed.
        guardian_removed, guardian_detail = _remove_scheduled_task(
            WINDOWS_GUARDIAN_TASK_NAME, stop_running=True,
        )
        if not guardian_removed:
            try:
                set_guardian_desired_running(True)
            except Exception:
                log.exception("Could not restore Guardian intent after Guardian task removal failure")
            self._publish_response("output", {
                "type": "uninstall_agent", "device_id": DEVICE_ID, "ok": False,
                "text": f"❌ Удаление не завершено: Guardian не остановлен ({guardian_detail}). Состояние агента сохранено.",
            })
            return
        cleanup_errors = []
        cleanup_notes = []
        try:
            import winreg
            run_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_path, 0, winreg.KEY_SET_VALUE) as key:
                for value_name in ("XGENT", "XGentAgent"):
                    try:
                        winreg.DeleteValue(key, value_name)
                    except FileNotFoundError:
                        pass
        except FileNotFoundError:
            pass
        except Exception as exc:
            cleanup_errors.append(f"Run registry: {exc}")

        install_root = resolve_agent_install_root(__file__, sys.executable)
        consent_targets = []
        if install_root is not None:
            consent_targets.extend((
                install_root / "XGENT-WDS.exe",
                install_root / "XIDER-Guard-Keeper.exe",
                install_root / "venv" / "Scripts" / "python.exe",
                install_root / "venv" / "Scripts" / "pythonw.exe",
            ))
        try:
            import winreg
            consent_base = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore"
            for capability in ("webcam", "microphone", "location"):
                for executable in consent_targets:
                    if not executable.is_file():
                        continue
                    consent_name = str(executable.resolve()).replace(":", "#").replace("\\", "#")
                    consent_path = consent_base + "\\" + capability + "\\NonPackaged\\" + consent_name
                    try:
                        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, consent_path)
                    except FileNotFoundError:
                        pass
                    except OSError as exc:
                        cleanup_errors.append(f"ConsentStore/{capability}: {exc}")
        except Exception as exc:
            cleanup_errors.append(f"ConsentStore: {exc}")

        try:
            startup_dir = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
            for shortcut in startup_dir.glob("*XGENT*"):
                shortcut.unlink(missing_ok=True)
        except Exception:
            pass

        file_result = remove_agent_install_tree(install_root)
        if file_result["state"] == "scheduled":
            cleanup_notes.append(f"Файлы агента удалятся при следующей перезагрузке ({file_result['scheduled']} элементов).")
        elif file_result["state"] not in {"removed"}:
            cleanup_errors.append(f"Файлы агента не удалены: {file_result['error']}")

        try:
            if CONFIG_DIR.exists():
                shutil.rmtree(CONFIG_DIR)
            if CONFIG_DIR.exists():
                cleanup_errors.append("каталог настроек остался после удаления")
        except OSError as exc:
            cleanup_errors.append(f"каталог настроек не удалён: {exc}")

        ok = not cleanup_errors
        cleanup_summary = ""
        if cleanup_notes:
            cleanup_summary += "\n• " + "\n• ".join(cleanup_notes)
        if cleanup_errors:
            log.error("Agent uninstall completed partially: %s", "; ".join(cleanup_errors))
            cleanup_summary += "\n• Не всё удалось удалить: " + "; ".join(cleanup_errors)

        text = (
            ("🛑 <b>Агент XGENT удалён с компьютера.</b>\n" if ok else "⚠️ <b>Удаление XGENT завершено частично.</b>\n")
            + "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "• Автозагрузка Windows очищена\n"
            "• Локальные настройки и логи обработаны\n"
            "• Процесс агента завершает работу."
            f"{cleanup_summary}"
        )
        self._publish_response("output", {"type": "uninstall_agent", "device_id": DEVICE_ID, "ok": ok, "text": text})

        def _bye():
            time.sleep(1.5)
            os._exit(0)
        threading.Thread(target=_bye, daemon=True).start()
