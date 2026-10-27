from __future__ import annotations

import pytest

from broker.x_dock_provision import provision


def test_provision_creates_limited_bot_and_removes_default_demo_account():
    commands = []

    def request(command):
        commands.append(command)
        return {"command": command["command"]}

    provision(request, bot_username="xider-bot", bot_password="B" * 48)

    assert [command["command"] for command in commands] == [
        "createRole", "createClient", "setDefaultACLAccess", "deleteClient", "deleteRole",
    ]
    assert commands[0]["acls"] == [
        {"acltype": "publishClientSend", "topic": "xgent/v1/#", "priority": 0, "allow": True},
        {"acltype": "publishClientReceive", "topic": "xgent/v1/#", "priority": 0, "allow": True},
        {"acltype": "subscribePattern", "topic": "xgent/v1/#", "priority": 0, "allow": True},
    ]
    assert commands[1]["roles"] == [{"rolename": "xider-bot", "priority": 0}]
    assert commands[2]["acls"] == [
        {"acltype": "publishClientSend", "allow": False},
        {"acltype": "publishClientReceive", "allow": False},
        {"acltype": "subscribe", "allow": False},
    ]
    assert commands[-2:] == [
        {"command": "deleteClient", "username": "democlient"},
        {"command": "deleteRole", "rolename": "client"},
    ]


@pytest.mark.parametrize(
    ("username", "password", "prefix"),
    [
        ("", "B" * 48, "xgent/v1"),
        ("xider-bot", "short", "xgent/v1"),
        ("xider-bot", "B" * 48 + "\n", "xgent/v1"),
        ("xider-bot", "B" * 48, "other/v1"),
    ],
)
def test_provision_rejects_invalid_configuration_before_mutating_broker(username, password, prefix):
    def request(_command):
        pytest.fail("invalid configuration must be rejected before broker changes")

    with pytest.raises(ValueError):
        provision(request, bot_username=username, bot_password=password, prefix=prefix)


def test_provision_stops_on_first_broker_rejection():
    commands = []

    def request(command):
        commands.append(command)
        if command["command"] == "createClient":
            return {"command": "createClient", "error": "duplicate"}
        return {"command": command["command"]}

    with pytest.raises(RuntimeError, match="createClient"):
        provision(request, bot_username="xider-bot", bot_password="B" * 48)
    assert [command["command"] for command in commands] == ["createRole", "createClient"]
