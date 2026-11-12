"""Browser-related command handlers."""

from __future__ import annotations


def open_url(
    payload: dict, *, cmd_id, device_id, validate_url, open_new_tab,
    remember_result, publish_response, notify_text,
) -> dict:
    try:
        url, count = validate_url(payload)
    except ValueError as exc:
        result = {
            "type": "open_url", "device_id": device_id, "ok": False,
            "requested_count": payload.get("count", 1), "accepted_count": 0,
            "detail": str(exc)[:200],
        }
        if isinstance(cmd_id, str) and cmd_id:
            result["id"] = cmd_id
        remember_result(payload, result)
        publish_response("output", result)
        raise

    accepted_count = 0
    failure_detail = None
    for _ in range(count):
        try:
            accepted = open_new_tab(url)
        except Exception:
            failure_detail = "Default browser failed to accept a launch request"
            break
        if accepted is not True:
            failure_detail = "Default browser did not confirm accepting a launch request"
            break
        accepted_count += 1
    result = {
        "type": "open_url", "device_id": device_id,
        "ok": accepted_count == count,
        "url": url,
        "requested_count": count,
        "accepted_count": accepted_count,
    }
    if isinstance(cmd_id, str) and cmd_id:
        result["id"] = cmd_id
    if failure_detail:
        result["detail"] = failure_detail
    remember_result(payload, result)
    publish_response("output", result)
    if failure_detail:
        raise RuntimeError(failure_detail)
    notify_text(f"Принято браузером к запуску: {accepted_count} из {count} · {url}")
    return result


from types import FunctionType, MethodType


def bind_handler_methods(client, runtime_globals):
    bound = {}
    method_names = {'_claim_open_url_operation', '_do_open_url', '_remember_open_url_result', '_open_url_signature'}
    for name in method_names:
        handler = globals()[name]
        runtime_handler = FunctionType(
            handler.__code__, runtime_globals, handler.__name__, handler.__defaults__, handler.__closure__
        )
        runtime_handler.__kwdefaults__ = handler.__kwdefaults__
        runtime_handler.__annotations__ = handler.__annotations__
        bound[name] = MethodType(runtime_handler, client)
    return bound


def _open_url_signature(self, payload: dict) -> str:
        try:
            return json.dumps(
                [payload.get("url"), payload.get("count", 1)],
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError):
            return repr((payload.get("url"), payload.get("count", 1)))

def _claim_open_url_operation(self, payload: dict) -> tuple[str, dict | None]:
        """Reserve one MQTT command id for an identical open_url request."""
        cmd_id = payload.get("id")
        if not isinstance(cmd_id, str) or not cmd_id or len(cmd_id) > 128:
            return "new", None
        signature = self._open_url_signature(payload)
        now = time.monotonic()
        with self._open_url_operations_lock:
            for old_id, entry in list(self._open_url_operations.items()):
                if (
                    entry.get("result") is not None
                    and now - entry["updated_at"] >= _OPEN_URL_OPERATION_TTL_SEC
                ):
                    self._open_url_operations.pop(old_id, None)
            entry = self._open_url_operations.get(cmd_id)
            if entry is not None:
                if entry["signature"] != signature:
                    return "conflict", None
                entry["updated_at"] = now
                result = entry.get("result")
                return ("complete", dict(result)) if result is not None else ("pending", None)
            if len(self._open_url_operations) >= _OPEN_URL_OPERATION_LIMIT:
                completed_ids = [
                    old_id for old_id, old_entry in self._open_url_operations.items()
                    if old_entry.get("result") is not None
                ]
                if not completed_ids:
                    return "full", None
                oldest_id = min(
                    completed_ids,
                    key=lambda key: self._open_url_operations[key]["updated_at"],
                )
                self._open_url_operations.pop(oldest_id, None)
            self._open_url_operations[cmd_id] = {
                "signature": signature,
                "updated_at": now,
                "result": None,
            }
        return "new", None

def _remember_open_url_result(self, payload: dict, result: dict) -> None:
        cmd_id = payload.get("id")
        if not isinstance(cmd_id, str) or not cmd_id:
            cmd_id = getattr(self._command_context, "cmd_id", None)
        if not isinstance(cmd_id, str) or not cmd_id:
            return
        signature = self._open_url_signature(payload)
        with self._open_url_operations_lock:
            entry = self._open_url_operations.get(cmd_id)
            if entry is not None and entry["signature"] == signature:
                entry["result"] = dict(result)
                entry["updated_at"] = time.monotonic()

def _do_open_url(self, payload: dict) -> dict:
        cmd_id = payload.get("id") or getattr(self._command_context, "cmd_id", None)
        return _browser_commands.open_url(
            payload, cmd_id=cmd_id, device_id=DEVICE_ID,
            validate_url=_validated_open_url, open_new_tab=webbrowser.open_new_tab,
            remember_result=self._remember_open_url_result,
            publish_response=self._publish_response, notify_text=self._notify_text,
        )
