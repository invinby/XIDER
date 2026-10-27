"""Harden a fresh, loopback-only Mosquitto Dynamic Security instance for X-DOCK.

The caller must keep the broker bound to loopback until this command succeeds.
Credentials are read only from the process environment and are never printed.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from broker.dynsec_channel import DynSecChannel


BOT_ROLE = "xider-bot"
DEFAULT_PREFIX = "xgent/v1"


def provision(request, *, bot_username: str, bot_password: str, prefix: str = DEFAULT_PREFIX) -> None:
    if not bot_username or len(bot_username) > 64:
        raise ValueError("invalid X-DOCK bot username")
    if len(bot_password) < 32 or "\n" in bot_password or "\r" in bot_password:
        raise ValueError("X-DOCK bot password must contain at least 32 characters")
    if prefix != DEFAULT_PREFIX:
        raise ValueError("X-DOCK broker prefix must match the deployed bot prefix")

    commands = (
        {
            "command": "createRole",
            "rolename": BOT_ROLE,
            "acls": [
                {"acltype": "publishClientSend", "topic": f"{prefix}/#", "priority": 0, "allow": True},
                {"acltype": "publishClientReceive", "topic": f"{prefix}/#", "priority": 0, "allow": True},
                {"acltype": "subscribePattern", "topic": f"{prefix}/#", "priority": 0, "allow": True},
            ],
        },
        {
            "command": "createClient",
            "username": bot_username,
            "password": bot_password,
            "roles": [{"rolename": BOT_ROLE, "priority": 0}],
        },
        {
            "command": "setDefaultACLAccess",
            "acls": [
                {"acltype": "publishClientSend", "allow": False},
                {"acltype": "publishClientReceive", "allow": False},
                {"acltype": "subscribe", "allow": False},
            ],
        },
    )
    for command in commands:
        response = request(command)
        if not isinstance(response, dict) or response.get("command") != command["command"] or response.get("error"):
            raise RuntimeError(f"Dynamic Security rejected {command['command']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18884)
    parser.add_argument("--ca-file", required=True, type=Path)
    args = parser.parse_args()

    admin_username = os.environ.get("XIDER_DOCK_ADMIN_USERNAME", "")
    admin_password = os.environ.get("XIDER_DOCK_ADMIN_PASSWORD", "")
    bot_username = os.environ.get("XIDER_DOCK_BOT_USERNAME", "")
    bot_password = os.environ.get("XIDER_DOCK_BOT_PASSWORD", "")
    if not args.ca_file.is_file():
        parser.error("the loopback bootstrap CA file does not exist")
    try:
        with DynSecChannel(
            args.host, args.port, admin_username, admin_password,
            ca_certs=str(args.ca_file),
        ) as channel:
            provision(
                channel.request,
                bot_username=bot_username,
                bot_password=bot_password,
            )
    except Exception as exc:
        parser.error(str(exc))
    print("[OK] X-DOCK Dynamic Security hardened; bot role is limited to xgent/v1/#.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
