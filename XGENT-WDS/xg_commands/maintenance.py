"""Local maintenance command response builders."""

from __future__ import annotations


def shell_command(
    client, payload: dict, *, device_id, publish_response,
    reserve_approval, load_component, logger,
) -> None:
    """Preserve the owner-approval gate and result envelope for shell execution."""
    command = payload.get("command")
    if not isinstance(command, str) or not command.strip():
        publish_response("shell", {
            "type": "shell", "device_id": device_id, "ok": False,
            "state": "failed", "error": "empty_or_invalid_command",
            "returncode": -1, "status": "rejected",
        })
        return
    if not reserve_approval(client, payload):
        publish_response("shell", {
            "type": "shell", "device_id": device_id, "ok": False,
            "error": "approval_invalid_or_replayed", "returncode": -1, "status": "rejected",
        })
        logger.warning("Shell request rejected by approval or replay guard")
        return
    try:
        execution = load_component("shell_execution")
    except ImportError:
        result = {"status": "error", "returncode": -2,
                  "output": "Execution component unavailable"}
    else:
        approved_id = (payload.get("approval") or {}).get("request_id")
        result = execution.execute_shell(command, payload.get("timeout"), request_id=approved_id)
    publish_response("shell", {"type": "shell", "device_id": device_id, **result})
    logger.info("Shell executed (rc=%s)", result["returncode"])


def shell_cancel(payload: dict, *, device_id, load_component, publish_response) -> None:
    target_id = payload.get("target_request_id")
    try:
        execution = load_component("shell_execution")
        status = execution.cancel_shell(target_id)
    except Exception:
        status = "unknown"
    if status not in {"cancelled", "unknown"}:
        status = "unknown"
    publish_response("shell_cancel", {
        "type": "shell_cancel", "device_id": device_id,
        "target_request_id": target_id if isinstance(target_id, str) and len(target_id) <= 128 else "",
        "status": status,
    })


def clean_temp(payload: dict, *, publish_response, device_id: str, cleanup_stale_media_temp) -> None:
    """Report cleanup of expired regular files in XIDER's private media-temp namespace."""
    report = cleanup_stale_media_temp()
    state = report.get("state")
    deleted = int(report.get("deleted", 0) or 0)
    skipped = int(report.get("skipped", 0) or 0)
    failed = int(report.get("failed", 0) or 0)
    if state == "success":
        text = (
            "🧹 Очистка временных файлов XIDER завершена.\n"
            f"• Удалено просроченных файлов: {deleted}\n"
            f"• Пропущено (активные или неподходящие): {skipped}"
        )
        ok = True
    elif state == "partial":
        text = (
            "⚠️ Очистка временных файлов XIDER завершилась частично.\n"
            f"• Удалено просроченных файлов: {deleted}\n"
            f"• Пропущено: {skipped}\n"
            f"• Не удалось обработать: {failed}"
        )
        ok = False
    else:
        text = "⚠️ Не удалось безопасно проверить временную папку XIDER; файлы не очищены."
        ok = False
    publish_response("output", {
        "type": "sys_clean_temp", "device_id": device_id, "ok": ok,
        "state": state if state in {"success", "partial"} else "error",
        "deleted": deleted, "skipped": skipped, "failed": failed, "text": text,
    })
