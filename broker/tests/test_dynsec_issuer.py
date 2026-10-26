import pytest

from broker.dynsec_issuer import DynSecIssuer, ProvisionError, device_role


def test_device_role_scopes_agent_and_guardian_to_one_device():
    role = device_role("macbook_123", "xgent/v1")
    acl = role["acls"]
    assert {a["topic"] for a in acl if a["acltype"] == "subscribeLiteral"} == {
        "xgent/v1/macbook_123/cmd",
        "xgent/v1/all/cmd",
    }
    assert {a["topic"] for a in acl if a["acltype"] == "publishClientReceive"} == {
        "xgent/v1/macbook_123/cmd",
        "xgent/v1/all/cmd",
    }
    assert any(
        a["acltype"] == "publishClientSend"
        and a["topic"] == "xgent/v1/macbook_123/cmd"
        and a["allow"] is False
        and a["priority"] > 0
        for a in acl
    )
    assert not any("other" in a["topic"] or a["topic"] == "#" for a in acl)


@pytest.mark.parametrize("device_id,prefix", [
    ("a/#", "xgent/v1"), ("a/+", "xgent/v1"), ("abc", "xgent/v1"),
    ("macbook_123", "xgent/#"), ("macbook_123", "xgent//v1"),
])
def test_invalid_topic_components_rejected(device_id, prefix):
    with pytest.raises(ValueError):
        device_role(device_id, prefix)


def test_issue_uses_unique_password_and_deny_defaults():
    commands = []

    def request(command):
        commands.append(command)
        return {"command": command["command"]}

    issued = DynSecIssuer(request).issue("macbook_123")
    assert issued.username == "xider-device-macbook_123"
    assert len(issued.password) >= 48
    assert [command["command"] for command in commands] == [
        "setDefaultACLAccess", "createRole", "createClient",
    ]
    assert commands[2]["password"] == issued.password
    assert {entry["acltype"] for entry in commands[0]["acls"]} == {
        "publishClientSend", "publishClientReceive", "subscribe",
    }
    assert all(entry["allow"] is False for entry in commands[0]["acls"])


def test_legacy_identity_collision_is_never_disabled_or_deleted():
    commands = []

    def request(command):
        commands.append(command["command"])
        if command["command"] == "createClient":
            return {"command": "createClient", "error": "collision"}
        return {"command": command["command"]}

    with pytest.raises(ProvisionError):
        DynSecIssuer(request).issue("macbook_123")
    assert commands == ["setDefaultACLAccess", "createRole", "createClient"]


def test_request_scoped_identity_can_be_safely_cleaned_after_ambiguous_failure():
    commands = []
    request_id = "a" * 32

    def request(command):
        commands.append(command)
        if command["command"] == "createClient":
            return {"command": "createClient", "error": "timeout"}
        return {"command": command["command"]}

    with pytest.raises(ProvisionError):
        DynSecIssuer(request).issue("macbook_123", request_id)
    assert commands[-3:] == [
        {"command": "disableClient", "username": f"xider-device-macbook_123-{request_id}"},
        {"command": "deleteClient", "username": f"xider-device-macbook_123-{request_id}"},
        {"command": "deleteRole", "rolename": f"xider-device-macbook_123-{request_id}"},
    ]


def test_missing_response_fails_closed():
    with pytest.raises(ProvisionError, match="missing or mismatched"):
        DynSecIssuer(lambda command: {}).issue("macbook_123")


def test_revoke_disables_before_deleting_identity_and_role():
    commands = []

    def request(command):
        commands.append(command)
        return {"command": command["command"]}

    DynSecIssuer(request).revoke("macbook_123")
    assert [item["command"] for item in commands] == ["disableClient", "deleteClient", "deleteRole"]
