from xg_commands import capabilities


def test_capabilities_command_sorts_commands_and_adds_approval_only_when_enabled():
    published = []
    logs = []
    status = {"shell": "supported"}
    capabilities.capabilities(
        publish_response=lambda topic, response: published.append((topic, response)),
        device_id="device-5",
        platform="Windows",
        version="1.2.3",
        hostname="host-5",
        supported_commands={"zeta", "alpha"},
        detect_feature_status=lambda: status,
        detect_features=lambda: {"shell": True},
        capability_snapshot=lambda actual_status: {"schema": 1, "items": []} if actual_status is status else {},
        shell_runner_diagnostic=lambda: {"schema": 1, "runner_state": "available"},
        command_approval_enabled=lambda: True,
        logger=type("Logger", (), {"info": lambda _self, message: logs.append(message)})(),
    )

    topic, response = published[0]
    assert topic == "capabilities"
    assert response["commands"] == ["alpha", "zeta"]
    assert response["feature_status"] is status
    assert response["command_approval_protocol"] == 1
    assert response["shell_runner"] == {"schema": 1, "runner_state": "available"}
    assert logs == ["Отчёт о возможностях отправлен"]
