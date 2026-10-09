"""End-to-end unit tests for the macOS agent's separate shell approval gate."""

import base64
import hashlib
import json
import sys
import threading
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import command_replay
import command_module_loader as command_loader
import xgent_mcs as mcs


DEVICE_ID = "MacBook-Air.local"
COMMAND = "printf 'точный текст\n'  "
NOW = 1_800_000_030
ISSUED_AT = NOW - 30
EXPIRES_AT = ISSUED_AT + 120
PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
PUBLIC_KEY = PRIVATE_KEY.public_key().public_bytes(
    serialization.Encoding.Raw,
    serialization.PublicFormat.Raw,
)
KEY_ID = hashlib.sha256(PUBLIC_KEY).hexdigest()[:16]


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _canonical(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _set_keyset(monkeypatch):
    encoded = _b64(_canonical({KEY_ID: _b64(PUBLIC_KEY)}))
    monkeypatch.setenv("XIDER_COMMAND_VERIFY_KEYSET_B64", encoded)


def _approval(*, command=COMMAND, request_id="approval_123456789", device_id=DEVICE_ID,
              issued_at=ISSUED_AT, expires_at=EXPIRES_AT):
    payload = {
        "key_id": KEY_ID,
        "request_id": request_id,
        "requester_id": 101,
        "approver_id": 202,
        "device_id": device_id,
        "command_hash": hashlib.sha256(command.encode("utf-8")).hexdigest(),
        "issued_at": issued_at,
        "expires_at": expires_at,
        "nonce": _b64(bytes(range(32))),
    }
    return {**payload, "signature": _b64(PRIVATE_KEY.sign(_canonical(payload)))}


def _client(monkeypatch, tmp_path):
    _set_keyset(monkeypatch)
    monkeypatch.setattr(mcs, "DEVICE_ID", DEVICE_ID)
    monkeypatch.setattr(mcs.time, "time", lambda: NOW)
    client = object.__new__(mcs.XgentClient)
    client._command_replay_ledger = command_replay.CommandReplayLedger(
        tmp_path / "command-replay.json"
    )
    client.responses = []
    client._publish_response = lambda topic, payload: client.responses.append((topic, payload))
    return client


def _fake_run(calls, before_run=None):
    def run(command, **kwargs):
        calls.append((command, kwargs))
        if before_run is not None:
            before_run()
        return SimpleNamespace(stdout="ok\n", stderr="", returncode=0)

    return run


@pytest.mark.parametrize("requested,expected", [(None, 20), (60, 20), (5, 5), (0, 20), ("bad", 20)])
def test_shell_enforces_execution_timeout(monkeypatch, tmp_path, requested, expected):
    client = _client(monkeypatch, tmp_path)
    def expired(command, **kwargs):
        assert kwargs["timeout"] == expected
        raise mcs.subprocess.TimeoutExpired(command, expected, output=b"partial", stderr=b"error")
    monkeypatch.setattr(mcs.subprocess, "run", expired)
    client._do_shell({"type": "shell", "command": COMMAND, "approval": _approval(), "timeout": requested})
    result = client.responses[-1][1]
    assert result["status"] == "timeout"
    assert result["returncode"] == -1
    assert result["stdout"] == "partial"
    assert result["stderr"] == "error"
    assert "command" not in result


def test_shell_caps_each_utf8_stream_before_transport(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(mcs.subprocess, "run", lambda *a, **kw: SimpleNamespace(
        stdout="а" * 5000, stderr="🙂" * 3000, returncode=7))
    client._do_shell({"type": "shell", "command": COMMAND, "approval": _approval()})
    result = client.responses[-1][1]
    assert result["stdout"] == "а" * 4096
    assert result["stderr"] == "🙂" * 2048
    assert len(result["stdout"].encode("utf-8")) <= 8192
    assert len(result["stderr"].encode("utf-8")) <= 8192
    assert result["stdout_truncated"] is True
    assert result["stderr_truncated"] is True
    assert result["status"] == "failed"
    assert result["returncode"] == 7
    assert "command" not in result


def test_shell_exact_limit_is_not_marked_truncated(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(mcs.subprocess, "run", lambda *a, **kw: SimpleNamespace(
        stdout="x" * 8192, stderr="", returncode=0))
    client._do_shell({"type": "shell", "command": COMMAND, "approval": _approval()})
    result = client.responses[-1][1]
    assert result["stdout"] == "x" * 8192
    assert result["stdout_truncated"] is False
    assert result["stderr_truncated"] is False
    assert result["status"] == "completed"


def test_shell_exception_does_not_echo_command_or_exception_credentials(monkeypatch, tmp_path, caplog):
    client = _client(monkeypatch, tmp_path)
    def failed(*args, **kwargs):
        raise OSError("password=synthetic-password command=" + COMMAND)
    monkeypatch.setattr(mcs.subprocess, "run", failed)
    client._do_shell({"type": "shell", "command": COMMAND, "approval": _approval()})
    result = client.responses[-1][1]
    assert result["status"] == "error"
    assert "synthetic-password" not in json.dumps(result) + caplog.text
    assert COMMAND not in json.dumps(result) + caplog.text


def test_shell_redacts_each_stream_before_byte_cap(monkeypatch, tmp_path, caplog):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(mcs.subprocess, "run", lambda *a, **kw: SimpleNamespace(
        stdout='password="synthetic long password"\ntoken=synthetic-token\n' + "x" * 9000,
        stderr='-----BEGIN OPENSSH PRIVATE KEY-----\nprivate-fixture', returncode=0))
    client._do_shell({"type": "shell", "command": COMMAND, "approval": _approval()})
    result = client.responses[-1][1]
    captured = json.dumps(result) + caplog.text
    assert all(secret not in captured for secret in ("synthetic long password", "synthetic-token", "private-fixture"))
    assert result["stdout_truncated"] is True
    assert len(result["stdout"].encode("utf-8")) <= 8192
    assert len(result["stderr"].encode("utf-8")) <= 8192


def test_shell_drops_incomplete_utf8_codepoint_at_cap(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(mcs.subprocess, "run", lambda *a, **kw: SimpleNamespace(
        stdout="x" * 8191 + "🙂", stderr="", returncode=0))
    client._do_shell({"type": "shell", "command": COMMAND, "approval": _approval()})
    assert client.responses[-1][1]["stdout"] == "x" * 8191
    assert client.responses[-1][1]["stdout_truncated"] is True


@pytest.mark.parametrize("raw,secret", [
    ('password="first synthetic-second' + chr(92), "synthetic-second"),
    ('123456:abcdefghijklmnopqrstuvwxyz', '123456:abcdefghijklmnopqrstuvwxyz'),
    ('Bearer synthetic-bearer', 'synthetic-bearer'),
    ('-----BEGIN ENCRYPTED PRIVATE KEY-----\nencrypted-fixture', 'encrypted-fixture'),
])
def test_shell_stream_redaction_handles_truncated_and_token_forms(raw, secret):
    safe, truncated = mcs._bounded_shell_stream(raw)
    assert secret not in safe
    assert len(safe.encode("utf-8")) <= 8192


def test_mac_unsigned_and_hmac_only_shell_never_reach_subprocess(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(mcs.subprocess, "run", _fake_run(calls))

    client._do_shell({"type": "shell", "command": COMMAND, "id": "mqtt-1"})
    client._do_shell({"type": "shell", "command": COMMAND, "signature": "hmac-only"})

    assert calls == []
    assert len(client.responses) == 2
    assert all(payload["ok"] is False for _topic, payload in client.responses)


@pytest.mark.parametrize(
    "approval_factory",
    [
        lambda: {**_approval(), "signature": "A" * 86},
        lambda: _approval(command=COMMAND + "tampered"),
        lambda: _approval(device_id="different-device"),
        lambda: _approval(issued_at=NOW + 1, expires_at=NOW + 100),
        lambda: _approval(issued_at=NOW - 121, expires_at=NOW - 1),
    ],
    ids=["bad-signature", "command-hash-mismatch", "wrong-device", "not-yet-valid", "expired"],
)
def test_mac_invalid_approval_never_reaches_subprocess(monkeypatch, tmp_path, approval_factory):
    client = _client(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(mcs.subprocess, "run", _fake_run(calls))

    client._do_shell({"type": "shell", "command": COMMAND, "approval": approval_factory()})

    assert calls == []
    assert client.responses[-1][1]["ok"] is False


def test_mac_missing_public_key_fails_closed(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.delenv("XIDER_COMMAND_VERIFY_KEYSET_B64")
    calls = []
    monkeypatch.setattr(mcs.subprocess, "run", _fake_run(calls))

    client._do_shell({"type": "shell", "command": COMMAND, "approval": _approval()})

    assert calls == []


@pytest.mark.parametrize("missing_module", ["command_signing", "command_replay"])
def test_mac_missing_approval_component_fails_closed(monkeypatch, tmp_path, missing_module):
    client = _client(monkeypatch, tmp_path)
    original_loader = mcs._load_command_approval_component

    def load_component(name):
        if name == missing_module:
            raise ImportError("approval component unavailable")
        return original_loader(name)

    monkeypatch.setattr(mcs, "_load_command_approval_component", load_component)
    calls = []
    monkeypatch.setattr(mcs.subprocess, "run", _fake_run(calls))

    client._do_shell({"type": "shell", "command": COMMAND, "approval": _approval()})

    assert calls == []


def test_mac_path_bound_loader_ignores_preloaded_modules_with_forged_paths(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(mcs, "_COMMAND_MODULE_LOADER", None)
    monkeypatch.setattr(command_loader, "_CACHE", {})
    for name in ("command_signing", "command_replay"):
        fake = types.ModuleType(name)
        # Forged __file__ used to pass the former origin check in the lazy import.
        fake.__file__ = str(Path(mcs.__file__).resolve().parent / f"{name}.py")
        monkeypatch.setitem(sys.modules, name, fake)

    signing, ledger = mcs._shell_approval_runtime(client)
    replay = mcs._load_command_approval_component("command_replay")

    expected_root = Path(mcs.__file__).resolve().parent
    assert Path(signing.__spec__.origin).resolve() == (expected_root / "command_signing.py").resolve()
    assert Path(replay.__spec__.origin).resolve() == (expected_root / "command_replay.py").resolve()
    assert ledger.path == tmp_path / "command-replay.json"


def test_mac_path_bound_loader_ignores_sys_path_shadow_files(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(mcs, "_COMMAND_MODULE_LOADER", None)
    shadow_dir = tmp_path / "shadow"
    shadow_dir.mkdir()
    marker = tmp_path / "shadow-executed"
    for name in ("command_signing", "command_replay"):
        (shadow_dir / f"{name}.py").write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n",
            encoding="utf-8",
        )
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.syspath_prepend(str(shadow_dir))

    signing, ledger = mcs._shell_approval_runtime(client)
    replay = mcs._load_command_approval_component("command_replay")

    expected_root = Path(mcs.__file__).resolve().parent
    assert Path(signing.__spec__.origin).resolve() == (expected_root / "command_signing.py").resolve()
    assert Path(replay.__spec__.origin).resolve() == (expected_root / "command_replay.py").resolve()
    assert ledger.path == tmp_path / "command-replay.json"
    assert not marker.exists()


def test_mac_frozen_loader_rejects_filesystem_fallback_before_exec(monkeypatch, tmp_path):
    executed = []
    archive = types.SimpleNamespace(toc={})

    class FallbackLoader:
        def exec_module(self, _module):
            executed.append("fallback")

    class PyiFrozenFinder:
        __module__ = "pyimod02_importers"

        def __init__(self, path):
            self._path = path
            self._pyz_archive = archive

        def find_spec(self, name):
            from importlib.machinery import ModuleSpec
            return ModuleSpec(name, FallbackLoader(), origin=str(tmp_path / f"{name}.py"))

    class PyiFrozenLoader:
        __module__ = "pyimod02_importers"
        pass

    runtime = types.ModuleType("pyimod02_importers")
    runtime.PyiFrozenFinder = PyiFrozenFinder
    runtime.PyiFrozenLoader = PyiFrozenLoader
    runtime.pyz_archive = archive
    monkeypatch.setitem(sys.modules, "pyimod02_importers", runtime)
    monkeypatch.setattr(mcs.sys, "frozen", True, raising=False)
    monkeypatch.setattr(mcs.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(command_loader, "_CACHE", {})

    with pytest.raises(ImportError):
        command_loader.load_component("command_signing", source_root=tmp_path, frozen=True)

    assert executed == []


def test_mac_frozen_loader_accepts_only_matching_bundled_pyz_entry(monkeypatch, tmp_path):
    executed = []
    archive = types.SimpleNamespace(toc={"command_signing": (0, 1)})

    class PyiFrozenLoader:
        __module__ = "pyimod02_importers"

        def __init__(self, name, pyz_archive, pyz_entry_name, is_package=False):
            self.name = name
            self._pyz_archive = pyz_archive
            self._pyz_entry_name = pyz_entry_name
            self._is_package = is_package
            self.path = str(tmp_path / f"{name}.py")

        def create_module(self, _spec):
            return None

        def exec_module(self, module):
            executed.append(module.__name__)
            module.bundled = True

    class PyiFrozenFinder:
        __module__ = "pyimod02_importers"

        def __init__(self, path):
            self._path = path
            self._pyz_archive = archive

        def find_spec(self, name):
            from importlib.machinery import ModuleSpec
            loader = PyiFrozenLoader(name, archive, name)
            return ModuleSpec(name, loader, origin=loader.path)

    runtime = types.ModuleType("pyimod02_importers")
    runtime.PyiFrozenFinder = PyiFrozenFinder
    runtime.PyiFrozenLoader = PyiFrozenLoader
    runtime.pyz_archive = archive
    monkeypatch.setitem(sys.modules, "pyimod02_importers", runtime)
    monkeypatch.setattr(mcs.sys, "frozen", True, raising=False)
    monkeypatch.setattr(mcs.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(command_loader, "_CACHE", {})

    module = command_loader.load_component("command_signing", source_root=tmp_path, frozen=True)

    assert module.bundled is True
    assert executed == ["command_signing"]


def test_mac_valid_approval_runs_exact_command_once_and_ledger_excludes_secrets(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    calls = []
    ledger_path = client._command_replay_ledger.path

    def assert_reserved_before_subprocess():
        stored = json.loads(ledger_path.read_text(encoding="utf-8"))
        assert stored["entries"]["approval_123456789"] == EXPIRES_AT

    monkeypatch.setattr(mcs.subprocess, "run", _fake_run(calls, assert_reserved_before_subprocess))
    client._do_shell({"type": "shell", "command": COMMAND, "approval": _approval()})

    assert len(calls) == 1
    assert calls[0][0] == COMMAND
    assert calls[0][1]["shell"] is True
    stored_text = ledger_path.read_text(encoding="utf-8")
    assert COMMAND not in stored_text
    assert _approval()["signature"] not in stored_text


def test_mac_replay_is_rejected_after_store_reconstruction(monkeypatch, tmp_path):
    first = _client(monkeypatch, tmp_path)
    second = object.__new__(mcs.XgentClient)
    second._command_replay_ledger = command_replay.CommandReplayLedger(
        tmp_path / "command-replay.json"
    )
    second.responses = []
    second._publish_response = lambda topic, payload: second.responses.append((topic, payload))
    calls = []
    monkeypatch.setattr(mcs.subprocess, "run", _fake_run(calls))
    request = {"type": "shell", "command": COMMAND, "approval": _approval()}

    first._do_shell(request)
    first._do_shell(request)
    second._do_shell(request)

    assert len(calls) == 1
    assert second.responses[-1][1]["ok"] is False


def test_mac_concurrent_duplicate_approval_runs_once(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    calls = []
    entered = threading.Event()
    release = threading.Event()

    def blocking_run(command, **kwargs):
        calls.append((command, kwargs))
        entered.set()
        release.wait(timeout=2)
        return SimpleNamespace(stdout="ok", stderr="", returncode=0)

    monkeypatch.setattr(mcs.subprocess, "run", blocking_run)
    request = {"type": "shell", "command": COMMAND, "approval": _approval()}
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(client._do_shell, request)
        assert entered.wait(timeout=2)
        second = pool.submit(client._do_shell, request)
        second.result(timeout=2)
        release.set()
        first.result(timeout=2)

    assert len(calls) == 1


def test_mac_corrupt_unwritable_and_full_replay_stores_fail_closed(monkeypatch, tmp_path):
    corrupt = _client(monkeypatch, tmp_path)
    corrupt._command_replay_ledger.path.write_text("not-json", encoding="utf-8")
    calls = []
    monkeypatch.setattr(mcs.subprocess, "run", _fake_run(calls))
    request = {"type": "shell", "command": COMMAND, "approval": _approval()}
    corrupt._do_shell(request)
    assert calls == []

    unwritable = _client(monkeypatch, tmp_path / "unwritable")
    unwritable._command_replay_ledger._atomic_write = lambda _entries: (_ for _ in ()).throw(OSError("disk"))
    unwritable._do_shell(request)
    assert calls == []

    full = _client(monkeypatch, tmp_path / "full")
    full._command_replay_ledger.max_entries = 0
    full._do_shell(request)
    assert calls == []


def test_mac_protocol_is_advertised_only_with_key_and_writable_replay_store(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, payload))
    monkeypatch.setattr(mcs, "_detect_feature_status", lambda: {
        "shell": "supported", "battery": "supported", "open_app": "supported",
        "microphone": "permission_unverified", "clipboard": "device_unverified",
    })
    monkeypatch.setattr(mcs, "_capability_snapshot", lambda _status: {})

    client._do_capabilities({})

    assert sent[-1][1]["command_approval_protocol"] == 1
    monkeypatch.delenv("XIDER_COMMAND_VERIFY_KEYSET_B64")
    client._do_capabilities({})
    assert "command_approval_protocol" not in sent[-1][1]
    _set_keyset(monkeypatch)

    client._client = MagicMock()
    monkeypatch.setattr(mcs, "ENCRYPT_PAYLOAD", False)
    client._publish_status()
    envelope = json.loads(client._client.publish.call_args.args[1])
    payload = mcs.verify_message(envelope)
    assert payload["command_approval_protocol"] == 1

    client._command_replay_ledger._atomic_write = lambda _entries: (_ for _ in ()).throw(OSError("disk"))
    client._do_capabilities({})
    assert "command_approval_protocol" not in sent[-1][1]
    client._publish_status()
    unavailable = mcs.verify_message(json.loads(client._client.publish.call_args.args[1]))
    assert "command_approval_protocol" not in unavailable
