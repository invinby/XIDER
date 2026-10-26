"""Issue one MQTT identity per approved X-DOCK device.

The caller supplies an authenticated Mosquitto Dynamic Security command channel.
This module never prints a credential and never accepts a broker administrator
credential from an installer or an unapproved enrollment request.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from typing import Callable, Mapping


_DEVICE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{5,63}\Z")
_PREFIX = re.compile(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+){0,7}\Z")


class ProvisionError(RuntimeError):
    """Broker rejected a provisioning step or its result could not be proved."""


@dataclass(frozen=True)
class DeviceCredential:
    device_id: str
    username: str
    password: str


def _names(device_id: str, prefix: str, issuance_id: str | None = None) -> tuple[str, str, str]:
    if not _DEVICE_ID.fullmatch(device_id):
        raise ValueError("invalid device ID")
    if not _PREFIX.fullmatch(prefix):
        raise ValueError("invalid MQTT prefix")
    suffix = ""
    if issuance_id is not None:
        if not re.fullmatch(r"[0-9a-f]{32}", issuance_id):
            raise ValueError("invalid issuance ID")
        suffix = f"-{issuance_id}"
    name = f"xider-device-{device_id}{suffix}"
    return name, name, prefix


def device_role(device_id: str, prefix: str, issuance_id: str | None = None) -> dict:
    """Build the exact topic policy used by the agent and its Guardian.

    Dynamic Security's defaults must deny publish and subscribe. The issuing
    channel applies those defaults before creating any device identity.
    """
    _, role, prefix = _names(device_id, prefix, issuance_id)
    own = f"{prefix}/{device_id}"
    commands = (f"{own}/cmd", f"{prefix}/all/cmd")
    acls = [
        {"acltype": "publishClientSend", "topic": f"{own}/+", "priority": 0, "allow": True},
        # A device must never be able to inject commands into its own inbox.
        {"acltype": "publishClientSend", "topic": commands[0], "priority": 10, "allow": False},
    ]
    for topic in commands:
        acls.extend(
            (
                {"acltype": "subscribeLiteral", "topic": topic, "priority": 0, "allow": True},
                {"acltype": "publishClientReceive", "topic": topic, "priority": 0, "allow": True},
            )
        )
    return {"command": "createRole", "rolename": role, "acls": acls}


class DynSecIssuer:
    """Provision/revoke via a callable that returns one correlated command reply.

    The callable must authenticate as a broker admin, send one command to
    ``$CONTROL/dynamic-security/v1`` and return the matching response object.
    Transport timeouts and missing responses must raise, never count as success.
    """

    def __init__(self, request: Callable[[Mapping], Mapping], prefix: str = "xgent/v1"):
        _names("device0", prefix)
        self._request = request
        self.prefix = prefix

    def _send(self, command: dict) -> Mapping:
        reply = self._request(command)
        if not isinstance(reply, Mapping) or reply.get("command") != command["command"]:
            raise ProvisionError("missing or mismatched broker response")
        if reply.get("error"):
            # Broker errors can include user-supplied fields; do not echo them.
            raise ProvisionError(f"broker rejected {command['command']}")
        return reply

    def _deny_defaults(self) -> None:
        self._send(
            {
                "command": "setDefaultACLAccess",
                "acls": [
                    {"acltype": "publishClientSend", "allow": False},
                    {"acltype": "publishClientReceive", "allow": False},
                    {"acltype": "subscribe", "allow": False},
                ],
            }
        )

    def issue(self, device_id: str, issuance_id: str | None = None) -> DeviceCredential:
        username, role, _ = _names(device_id, self.prefix, issuance_id)
        password = secrets.token_urlsafe(48)
        self._deny_defaults()
        try:
            self._send(device_role(device_id, self.prefix, issuance_id))
            self._send(
                {
                    "command": "createClient",
                    "username": username,
                    "password": password,
                    "roles": [{"rolename": role, "priority": 0}],
                }
            )
        except Exception:
            # Revoke only a per-request identity whose name is guaranteed unique.
            # A legacy device-wide name may already belong to a live installation;
            # never disable or delete it after an ambiguous broker response.
            if issuance_id is None:
                raise
            try:
                self._send({"command": "disableClient", "username": username})
                self._send({"command": "deleteClient", "username": username})
                try:
                    self._send({"command": "deleteRole", "rolename": role})
                except Exception:
                    pass
            except Exception:
                # Do not remove a role while a possibly-created client remains.
                pass
            raise
        return DeviceCredential(device_id=device_id, username=username, password=password)

    def disable(self, device_id: str) -> None:
        username, _, _ = _names(device_id, self.prefix)
        self._send({"command": "disableClient", "username": username})

    def revoke(self, device_id: str, issuance_id: str | None = None) -> None:
        """Remove a newly created identity and its role after failed pairing."""
        username, role, _ = _names(device_id, self.prefix, issuance_id)
        self._send({"command": "disableClient", "username": username})
        self._send({"command": "deleteClient", "username": username})
        self._send({"command": "deleteRole", "rolename": role})
