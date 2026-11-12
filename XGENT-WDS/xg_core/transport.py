"""MQTT lifecycle, dispatch and response transport for the Windows agent."""

from __future__ import annotations

from types import FunctionType, MethodType


def bind_handler_methods(client, runtime_globals):
    bound = {}
    method_names = {'_publish_ack', '_on_connect_fail', '_publish_response', '_on_connect', '_on_disconnect', '_heartbeat_loop', '_configure_mqtt_will', '_on_message', 'request_stop', '_run_threaded', 'stop', 'start', '_publish_status', '_on_subscribe', '_dispatch'}
    for name in method_names:
        handler = globals()[name]
        runtime_handler = FunctionType(
            handler.__code__, runtime_globals, handler.__name__, handler.__defaults__, handler.__closure__
        )
        runtime_handler.__kwdefaults__ = handler.__kwdefaults__
        runtime_handler.__annotations__ = handler.__annotations__
        bound[name] = MethodType(runtime_handler, client)
    return bound


def start(self) -> None:
        """Неблокирующий запуск: подключение, цикл MQTT и heartbeat."""
        # connect_async + loop_start сами повторяют попытки подключения,
        # включая первую, при недоступном брокере (с бэкоффом выше).
        if MQTT_TLS:
            self._client.tls_set()  # системные CA; без отключения проверки сертификата
        if MQTT_USERNAME:
            log.info("MQTT auth: %s (TLS=%s", MQTT_USERNAME, MQTT_TLS)
            self._client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

        self._configure_mqtt_will()

        self._client.connect_async(MQTT_BROKER, MQTT_PORT, keepalive=60)
        self._client.loop_start()
        self._heartbeat.start()
        self._sysinfo_schedule.start()
        self._network_traffic_schedule.start()
        self._interface_status_history_thread.start()
        log.info("Клиент запущен: %s (%s)", DEVICE_NAME, DEVICE_ID)

def _configure_mqtt_will(self) -> None:
        # Keep the small LWT envelope readable even when normal status payloads
        # are encrypted. A stale Will is accepted only for its active session.
        will_topic = f"{MQTT_PREFIX}/{DEVICE_ID}/status"
        will_body = {
            "type": "status", "device_id": DEVICE_ID,
            "status": "offline", "mqtt_session_id": self._mqtt_session_id,
        }
        will_payload = sign_message(will_body)
        self._client.will_set(will_topic, json.dumps(will_payload, ensure_ascii=False), qos=1, retain=False)

def stop(self) -> None:
        self._running.clear()
        self._sysinfo_schedule_wake.set()
        wake = getattr(self, "_network_traffic_schedule_wake", None)
        if wake is not None:
            wake.set()
        self._client.loop_stop()
        self._client.disconnect()
        log.info("Клиент остановлен")

def request_stop(self) -> None:
        """Остановка по команде из Telegram или из меню значка."""
        try:
            set_guardian_desired_running(False)
        except Exception:
            log.exception("Could not persist explicit stop intent for Guardian")
        self._running.clear()
        self._sysinfo_schedule_wake.set()
        wake = getattr(self, "_network_traffic_schedule_wake", None)
        if wake is not None:
            wake.set()
        if self.on_stop_requested:
            self.on_stop_requested()

def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        self._mqtt_connected = reason_code == 0
        with self._subscription_lock:
            self._pending_command_subacks.clear()
            self._command_subscriptions_failed = reason_code != 0
        if reason_code == 0:
            with self._subscription_lock:
                for topic in (f"{MQTT_PREFIX}/{DEVICE_ID}/cmd", f"{MQTT_PREFIX}/all/cmd"):
                    try:
                        result, mid = client.subscribe(topic, qos=0)
                        if result != mqtt.MQTT_ERR_SUCCESS or type(mid) is not int or mid <= 0 or mid in self._pending_command_subacks:
                            raise RuntimeError(f"subscribe rejected: rc={result}, mid={mid}")
                        self._pending_command_subacks.add(mid)
                    except Exception:
                        self._command_subscriptions_failed = True
                        log.exception("Не удалось запросить командную MQTT-подписку %s", topic)
            log.info("Подключено к %s:%s", MQTT_BROKER, MQTT_PORT)
            # Подписки восстанавливаются при каждом переподключении.
            self._publish_status()
        else:
            log.warning("Не удалось подключиться к брокеру: %s", reason_code)
            try:
                write_runtime_health("agent", False)
            except Exception:
                log.exception("Не удалось обновить локальное состояние MQTT агента")

def _on_subscribe(self, client, userdata, mid, reason_codes, properties=None):
        with self._subscription_lock:
            if mid not in self._pending_command_subacks:
                return
            self._pending_command_subacks.remove(mid)
            grants = [getattr(reason, "value", reason) for reason in (reason_codes or ())]
            if len(grants) != 1 or any(type(grant) is not int or grant not in (0, 1, 2) for grant in grants):
                self._command_subscriptions_failed = True
                return
            if self._pending_command_subacks or self._command_subscriptions_failed or not self._running.is_set():
                return
            try:
                write_runtime_health("agent", True)
                if not _BUNDLED_RUNTIME and mark_agent_update_healthy(
                    CONFIG_DIR / UPDATE_STATE_NAME, Path(__file__).resolve().parent,
                ):
                    self._update_health.set()
                    self._publish_status()
                    self._restart_existing_guardian_after_update()
            except Exception:
                log.exception("Не удалось подтвердить здоровье обновлённого Windows-агента")

def _on_connect_fail(self, client, userdata, reason_code=None):
        log.warning(
            "Попытка подключения к брокеру не удалась (код %s); "
            "автоматический повтор с бэкоффом…", reason_code
        )

def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self._mqtt_connected = False
        with self._subscription_lock:
            self._pending_command_subacks.clear()
            self._command_subscriptions_failed = True
        try:
            write_runtime_health("agent", False)
        except Exception:
            log.exception("Не удалось сбросить локальное состояние MQTT агента")
        if not self._running.is_set():
            log.info("Отключение по команде остановки")
            return
        self._mqtt_session_id = uuid.uuid4().hex
        try:
            self._configure_mqtt_will()
        except Exception:
            log.exception("Не удалось обновить MQTT Last Will для следующего подключения")
        log.warning("Отключено от брокера (код %s); переподключение…", reason_code)

def _on_message(self, client, userdata, message):
        try:
            data = json.loads(message.payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            log.warning("Некорректный JSON в топике %s", message.topic)
            return
        payload = verify_message(data)
        if payload is None:
            log.warning("Сообщение с неверной подписью в топике %s", message.topic)
            return
        if ENCRYPT_PAYLOAD or ("enc" in payload):
            inner = decrypt_payload(payload)
            if inner is None:
                log.warning("Не удалось расшифровать команду из %s", message.topic)
                return
            payload = inner
        self._dispatch(payload)

def _dispatch(self, payload: dict) -> None:
        action = payload.get("type")
        cmd_id = payload.get("id")
        if getattr(self, "_standby", False) and action not in ("wake", "status_request"):
            log.info("Команда %s отклонена: агент в режиме ожидания", action)
            self._publish_response(
                "output",
                {
                    "type": action,
                    "device_id": DEVICE_ID,
                    "ok": False,
                    "text": "💤 Агент находится в спящем режиме (Standby).\nНажмите «☀️ Пробудить агента» в меню питания, чтобы возобновить работу.",
                },
            )
            self._publish_ack(action, "error", detail="standby", cmd_id=cmd_id)
            return

        handler = self._handlers.get(action)
        if handler is None:
            log.warning("Неизвестная команда: %s", action)
            self._publish_ack(action or "?", "error", detail="unknown_command", cmd_id=cmd_id)
            return
        if action == "open_url":
            operation_state, cached_result = self._claim_open_url_operation(payload)
            if operation_state == "pending":
                self._publish_ack(action, "received", cmd_id=cmd_id)
                return
            if operation_state == "complete":
                self._publish_response("output", cached_result)
                self._publish_ack(
                    action,
                    "completed" if cached_result.get("ok") is True else "error",
                    cmd_id=cmd_id,
                )
                return
            if operation_state == "conflict":
                self._publish_response("output", {
                    "type": "open_url", "device_id": DEVICE_ID, "id": cmd_id,
                    "ok": False, "requested_count": payload.get("count"),
                    "accepted_count": 0,
                    "detail": "Request id was reused with different URL parameters",
                })
                self._publish_ack(action, "error", detail="operation_id_conflict", cmd_id=cmd_id)
                return
            if operation_state == "full":
                self._publish_response("output", {
                    "type": "open_url", "device_id": DEVICE_ID, "id": cmd_id,
                    "ok": False, "requested_count": payload.get("count", 1),
                    "accepted_count": 0,
                    "detail": "operation_cache_full",
                })
                self._publish_ack(action, "error", detail="operation_cache_full", cmd_id=cmd_id)
                return
        log.info("Выполняю команду: %s", action)
        # Подтверждение получения — часть протокола acknowledgement.
        self._publish_ack(action, "received", cmd_id=cmd_id)
        if action in MEDIA_REQUEST_ACTIONS and isinstance(cmd_id, str):
            cancel_event = MEDIA_REQUESTS.begin(
                cmd_id, action, cancellable=(action == "camera_series"),
            )
            if cancel_event is not None:
                def _tracked_media_request(request_payload):
                    try:
                        handler({**request_payload, "_media_cancel_event": cancel_event})
                    finally:
                        MEDIA_REQUESTS.finish(cmd_id)
                self._run_threaded(action, cmd_id, _tracked_media_request, payload)
                return
        self._run_threaded(action, cmd_id, handler, payload)

def _run_threaded(self, action, cmd_id, func, payload) -> None:
        """ACK описывает выполнение handler-а; эффект команды подтверждает её ответ."""

        def _worker() -> None:
            self._command_context.cmd_id = cmd_id
            try:
                func(payload)
            except Exception:
                log.exception("Команда %s завершилась ошибкой", action)
                self._publish_ack(action, "error", detail="exception", cmd_id=cmd_id)
            else:
                self._publish_ack(action, "completed", cmd_id=cmd_id)
            finally:
                try:
                    del self._command_context.cmd_id
                except AttributeError:
                    pass

        threading.Thread(target=_worker, name=f"xgent-cmd-{action}", daemon=True).start()

def _publish_status(self) -> None:
        status = "standby" if getattr(self, "_standby", False) else "online"
        payload = {
            "type": "status",
            "device_id": DEVICE_ID,
            "name": DEVICE_NAME,
            "os": PLATFORM,
            "version": VERSION,
            "update_mode": "frozen" if _BUNDLED_RUNTIME else "source",
            "release_update_ready": trusted_release_keys_ready() and not _BUNDLED_RUNTIME,
            "update_manifest_binding_protocol": 1,
            "status": status,
            "mqtt_session_id": self._mqtt_session_id,
        }
        if _command_approval_protocol_enabled(self):
            payload["command_approval_protocol"] = 1
        if not _BUNDLED_RUNTIME:
            receipt = read_agent_update_result(CONFIG_DIR / UPDATE_STATE_NAME)
            if receipt:
                payload["agent_update"] = receipt
        body = encrypt_payload(payload) if ENCRYPT_PAYLOAD else payload
        envelope = sign_message(body)
        if not self._mqtt_connected:
            return
        result = self._client.publish(
            f"{MQTT_PREFIX}/{DEVICE_ID}/status",
            json.dumps(envelope, ensure_ascii=False),
            # Status is replaceable telemetry. QoS 1 may replay an old signed
            # envelope after reconnect; the fresh status sent on connect is authoritative.
            qos=0,
        )
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            log.warning("Status not accepted by MQTT transport (rc=%s); it will retry on the next heartbeat", result.rc)

def _publish_response(self, topic_suffix: str, payload: dict) -> bool:
        """Отправить подписанный ответ в топик {prefix}/{device_id}/{suffix}."""
        action_type = payload.get("type") if isinstance(payload, dict) else None
        if (isinstance(action_type, str) and action_type.startswith("prank_")
                and payload.get("ok") is True and payload.get("effect_confirmed") is not True):
            # A returned UI/API call proves that the request was launched, not
            # that the person actually saw or heard its effect.
            payload = {**payload, "state": "launch_accepted", "effect_confirmed": False}
        cmd_id = getattr(self._command_context, "cmd_id", None)
        if cmd_id is not None and "id" not in payload:
            payload = {**payload, "id": cmd_id}
        try:
            import media_history
            if topic_suffix in media_history.MEDIA_ACTIONS:
                media_history.record_response(CONFIG_DIR / "media-history.json", topic_suffix, payload)
        except Exception:
            log.exception("Не удалось записать метаданные media-операции в локальную историю")
        body = encrypt_payload(payload) if ENCRYPT_PAYLOAD else payload
        envelope = sign_message(body)
        info = self._client.publish(
            f"{MQTT_PREFIX}/{DEVICE_ID}/{topic_suffix}",
            json.dumps(envelope, ensure_ascii=False),
            qos=1,
        )
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            log.warning("Не удалось опубликовать ответ %s (rc=%s)", topic_suffix, info.rc)
            return False
        else:
            try:
                info.wait_for_publish(timeout=3.0)
            except Exception:
                log.exception("Не дождались доставки ответа %s", topic_suffix)
                return False
        return info.is_published() if callable(getattr(info, "is_published", None)) else True

def _publish_ack(self, action, status, detail=None, cmd_id=None) -> None:
        """Подтверждение выполнения команды (received/ok/error)."""
        payload = {
            "type": "ack",
            "device_id": DEVICE_ID,
            "action": action,
            "status": status,
        }
        if cmd_id is not None:
            payload["id"] = cmd_id
        if detail:
            payload["detail"] = detail
        self._publish_response("ack", payload)

def _heartbeat_loop(self) -> None:
        while self._running.is_set():
            time.sleep(HEARTBEAT_INTERVAL + random.uniform(0, 10))
            if self._running.is_set():
                try:
                    write_runtime_health("agent", self._client.is_connected())
                except OSError:
                    log.exception("Could not write Windows Agent health heartbeat")
                if self._client.is_connected():
                    self._publish_status()
