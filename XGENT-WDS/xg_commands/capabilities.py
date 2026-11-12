"""Client capability and passive runner-diagnostic command handler."""

from __future__ import annotations


def capabilities(
    *, publish_response, device_id: str, platform: str, version: str, hostname: str,
    supported_commands, detect_feature_status, detect_features, capability_snapshot,
    shell_runner_diagnostic, command_approval_enabled, logger,
) -> None:
    feature_status = detect_feature_status()
    response = {
        "type": "capabilities",
        "device_id": device_id,
        "platform": platform,
        "version": version,
        "hostname": hostname,
        "commands": sorted(supported_commands),
        "features": detect_features(),
        "feature_status": feature_status,
        **capability_snapshot(feature_status),
        "shell_runner": shell_runner_diagnostic(),
    }
    if command_approval_enabled():
        response["command_approval_protocol"] = 1
    publish_response("capabilities", response)
    logger.info("Отчёт о возможностях отправлен")
