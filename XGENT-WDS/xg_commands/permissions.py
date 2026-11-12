"""Explicit Windows privacy-settings navigation and consent requests."""

from __future__ import annotations


def geo_location(
    payload: dict, *, device_id, publish_response, read_system_location,
    read_ip_estimate, location_text, compare_location_results, open_settings,
) -> None:
    """Handle one explicit system-location request and optional IP estimate."""
    value = read_system_location()
    if payload.get("compare_sources") is True:
        comparison = compare_location_results(value, read_ip_estimate())
        comparison.update(
            type="geo_location", device_id=device_id, feature_id="P01-F25",
            schema="xider.location-comparison.v1", request_id=payload.get("id"),
            ok=(value.get("state") == "granted" or comparison["ip_estimate"].get("state") == "granted"),
            text=location_text(comparison),
        )
        publish_response("geo_location", comparison)
        return
    if value["state"] == "needs_live_test":
        try:
            open_settings("ms-settings:privacy-location")
        except OSError:
            pass  # Guidance remains visible in the owner response.
    if value["state"] != "granted" and payload.get("ip_fallback") is True:
        system_state = value["state"]
        value = read_ip_estimate()
        value["system_state"] = system_state
    publish_response("geo_location", {
        "type": "geo_location", "device_id": device_id,
        "ok": value["state"] == "granted", **value, "text": location_text(value),
    })


def open_permission_settings(payload: dict, *, device_id, publish_response, opener):
    settings = {
        "camera": "ms-settings:privacy-webcam",
        "microphone": "ms-settings:privacy-microphone",
        "location": "ms-settings:privacy-location",
    }
    permission = payload.get("permission")
    uri = settings.get(permission) if isinstance(permission, str) else None
    result = {
        "type": "permission_settings",
        "device_id": device_id,
        "permission": permission if uri else "unknown",
        "ok": False,
    }
    command_id = payload.get("id")
    if isinstance(command_id, str) and command_id:
        result["id"] = command_id
    if permission == "location":
        try:
            from location_adapter import native_backend
            permission_state, _enabled = native_backend().diagnose()
        except Exception:
            permission_state = "unknown"
        if permission_state in {"granted", "denied"}:
            # Windows does not repeat a decision prompt; let the owner
            # change or revoke it in the system Privacy page instead.
            try:
                if callable(opener):
                    opener(uri)
                    result.update(
                        ok=True, state="settings_opened", permission_state=permission_state,
                        text="Открыл настройки геолокации Windows. Там можно изменить или отозвать решение; координаты не запрашивались.",
                    )
                else:
                    result.update(state="unavailable", text="Не удалось открыть настройки геолокации Windows.")
            except OSError:
                result.update(state="unknown", text="Windows не подтвердила открытие настроек геолокации.")
            publish_response("permission_settings", result)
            return result
        try:
            from permission_prompt import request_windows_location_consent
            outcome = request_windows_location_consent()
            state = outcome.get("state") if isinstance(outcome, dict) else "unknown"
            result["state"] = state if state in {"granted", "denied", "cancelled", "unknown", "unavailable", "unsupported"} else "unknown"
            result["ok"] = result["state"] in {"granted", "denied", "cancelled", "unknown"}
            result["text"] = str(outcome.get("text", "Состояние разрешения неизвестно."))[:500]
            if result["state"] in {"unavailable", "unsupported"} and callable(opener):
                opener(uri)
                result["settings_opened"] = True
                result["ok"] = True
                result["text"] += " Открыл системные настройки; выдачу доступа нужно подтвердить там вручную."
        except Exception:
            result["state"] = "unavailable"
            result["text"] = "Локальный запрос Windows недоступен; разрешение не выдано. Открой настройки геолокации вручную."
            try:
                if callable(opener):
                    opener(uri)
                    result["settings_opened"] = True
                    result["ok"] = True
            except OSError:
                pass
        publish_response("permission_settings", result)
        return result
    if uri and callable(opener):
        try:
            opener(uri)
            result["ok"] = True
            result["state"] = "settings_opened"
            result["text"] = (
                "Открыл Параметры конфиденциальности. Для камеры и микрофона включи доступ "
                "классических приложений; отдельного окна Allow/Don't Allow у этого desktop-агента нет. "
                "Агент сам разрешения не меняет."
            )
        except OSError:
            result["state"] = "unknown"
            result["text"] = "Не удалось открыть Параметры Windows; открой нужный раздел вручную."
    elif uri:
        result["text"] = "Открытие Параметров Windows доступно только в Windows-сеансе; ничего не изменено."
    else:
        result["text"] = "Неизвестный или неподдерживаемый раздел настроек; команда отклонена."
    publish_response("permission_settings", result)
    return result
