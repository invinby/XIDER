"""Render fail-closed Mosquitto configuration for the isolated X-DOCK broker."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from pathlib import PurePosixPath
import tempfile


def _absolute_path(name: str, value: str | Path) -> str:
    path = PurePosixPath(str(value))
    if not path.is_absolute():
        raise ValueError(f"{name} must be an absolute path")
    text = str(path)
    if any(char in text for char in "\r\n\x00"):
        raise ValueError(f"{name} contains an invalid character")
    return text


def render_config(
    *,
    plugin_path: str | Path,
    security_file: str | Path,
    data_dir: str | Path,
    cert_file: str | Path,
    key_file: str | Path,
    port: int,
    bind_address: str | None = None,
    password_init_file: str | Path | None = None,
) -> str:
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    if bind_address is not None and bind_address not in {"127.0.0.1", "::1"}:
        raise ValueError("bootstrap listener must bind to loopback only")

    plugin = _absolute_path("plugin_path", plugin_path)
    state = _absolute_path("security_file", security_file)
    data = _absolute_path("data_dir", data_dir)
    cert = _absolute_path("cert_file", cert_file)
    key = _absolute_path("key_file", key_file)
    init = _absolute_path("password_init_file", password_init_file) if password_init_file else None
    listener = f"listener {port}" + (f" {bind_address}" if bind_address else "")
    lines = [
        "per_listener_settings false",
        "allow_anonymous false",
        listener,
        "protocol mqtt",
        f"certfile {cert}",
        f"keyfile {key}",
        "tls_version tlsv1.2",
        f"plugin {plugin}",
        f"plugin_opt_config_file {state}",
    ]
    if init:
        lines.append(f"plugin_opt_password_init_file {init}")
    lines.extend((
        "persistence true",
        f"persistence_location {data.rstrip('/')}/",
        "log_dest stdout",
        "log_type error",
        "log_type warning",
        "log_type notice",
    ))
    return "\n".join(lines) + "\n"


def write_config(destination: Path, content: str) -> None:
    if destination.is_symlink():
        raise RuntimeError("refusing symlink X-DOCK broker config")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o640)
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin", required=True, type=Path)
    parser.add_argument("--security-file", required=True, type=Path)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--cert-file", required=True, type=Path)
    parser.add_argument("--key-file", required=True, type=Path)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--bind-address", choices=("127.0.0.1", "::1"))
    parser.add_argument("--password-init-file", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        content = render_config(
            plugin_path=args.plugin,
            security_file=args.security_file,
            data_dir=args.data_dir,
            cert_file=args.cert_file,
            key_file=args.key_file,
            port=args.port,
            bind_address=args.bind_address,
            password_init_file=args.password_init_file,
        )
        write_config(args.output, content)
    except (OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
