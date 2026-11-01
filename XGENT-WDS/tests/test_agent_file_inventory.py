from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

import agent_file_inventory as inventory


def _write(root: Path, name: str, data: bytes = b"owned component") -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_inventory_is_bounded_metadata_only_and_hashes_known_regular_files(tmp_path, monkeypatch):
    agent = _write(tmp_path, "XGENT-WDS.exe", b"agent binary")
    _write(tmp_path, "xgent_wds.py", b"agent source")
    _write(tmp_path, "config.py", b'VERSION = "4.3.4"\nSHARED_KEY = "should not be returned"')
    _write(tmp_path, ".env", b"SHARED_KEY=secret\nMQTT_PASSWORD=secret")
    _write(tmp_path, "guardian.json", b'{"should_not_be_seen":true}')
    monkeypatch.setattr(inventory, "_authenticode_status", lambda _p: ("verified", "signature test"))

    result = inventory.collect_agent_file_inventory(tmp_path)

    assert result["schema"] == "xider.agent-files.v1"
    assert result["state"] == "partial"
    assert result["components"][0]["id"] == "agent"
    assert result["components"][0]["version"] == "4.3.4"
    assert result["components"][0]["build_mode"] == "python_source"
    assert result["components"][0]["obfuscation_state"] == "not_applied"
    record = next(row for row in result["components"][0]["files"] if row["path"].endswith("XGENT-WDS.exe"))
    assert record["size_bytes"] == len(b"agent binary")
    assert record["sha256"] == hashlib.sha256(b"agent binary").hexdigest()
    assert record["signature_state"] == "verified"
    assert record["integrity_state"] == "unverified"
    assert result["protection"]["obfuscation_state"] == "not_applied"
    assert ".env" not in str(result)
    assert "should not be returned" not in str(result)
    assert "MQTT_PASSWORD" not in str(result)
    assert "guardian.json" not in str(result)
    assert "No trusted signed per-file manifest is installed." in record["evidence"]
    assert agent.exists()


def test_inventory_reports_guard_keeper_as_separate_component(tmp_path):
    _write(tmp_path, "xider_guardian_wds.py")
    _write(tmp_path, "install_guardian.ps1")
    result = inventory.collect_agent_file_inventory(tmp_path, agent_version="4.3.4", guardian_version="1.0-windows")

    agent, guardian = result["components"]
    assert agent["state"] == "absent"
    assert agent["version"] is None
    assert guardian["state"] == "present"
    assert guardian["version"] == "1.0-windows"
    assert len(guardian["files"]) == len(inventory._GUARDIAN_FILES)
    assert next(row for row in guardian["files"] if row["name"] == "XIDER-Guard-Keeper.exe")["state"] == "unsupported"


def test_compiled_windows_components_report_obfuscation_as_unverified(tmp_path, monkeypatch):
    monkeypatch.setattr(inventory.sys, "executable", r"C:\XIDER\XGENT-WDS.exe")

    result = inventory.collect_agent_file_inventory(tmp_path)

    assert all(component["build_mode"] == "nuitka_compiled" for component in result["components"])
    assert all(component["obfuscation_state"] == "unknown" for component in result["components"])
    assert result["protection"]["obfuscation_state"] == "unknown"
    assert "signed release inventory" in result["protection"]["evidence"]


def test_component_is_partial_when_only_one_allowlisted_file_is_found(tmp_path):
    _write(tmp_path, "config.py", b'VERSION = "4.3.4"')

    result = inventory.collect_agent_file_inventory(tmp_path)
    agent = result["components"][0]

    assert agent["state"] == "partial"
    assert agent["version"] is None
    assert len(agent["files"]) == len(inventory._AGENT_FILES)
    assert sum(row["state"] == "present" for row in agent["files"]) == 1
    assert next(row for row in agent["files"] if row["name"] == "XGENT-WDS.exe")["state"] == "unsupported"
    assert next(row for row in agent["files"] if row["name"] == "xgent_wds.py")["state"] == "missing"


def test_inventory_preserves_unavailable_and_missing_file_statuses(tmp_path):
    root = tmp_path / "install"
    root.mkdir()
    external = _write(tmp_path, "outside.py", b"outside")
    link = root / "xgent_wds.py"
    try:
        link.symlink_to(external)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"file symlink unavailable: {exc}")

    agent = inventory.collect_agent_file_inventory(root)["components"][0]
    by_name = {row["name"]: row for row in agent["files"]}

    assert agent["state"] == "partial"
    assert by_name["xgent_wds.py"]["state"] == "unavailable"
    assert by_name["config.py"]["state"] == "missing"
    assert by_name["XGENT-WDS.exe"]["state"] == "unsupported"
    assert by_name["xgent_wds.py"]["sha256"] is None


def test_inventory_rejects_reparse_point_install_root(tmp_path):
    root = tmp_path / "real"
    root.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(root, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")

    result = inventory.collect_agent_file_inventory(alias)
    assert result["state"] == "error"
    assert result["components"][0]["state"] == "unknown"
    assert "reparse point" in result["error"].lower()


def test_inventory_skips_symlinked_allowlisted_file_outside_root(tmp_path):
    root = tmp_path / "install"
    root.mkdir()
    external = _write(tmp_path, "outside.exe", b"outside")
    link = root / "XGENT-WDS.exe"
    try:
        link.symlink_to(external)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"file symlink unavailable: {exc}")

    result = inventory.collect_agent_file_inventory(root)
    agent = result["components"][0]
    assert agent["state"] == "partial"
    exe = next(row for row in agent["files"] if row["name"] == "XGENT-WDS.exe")
    assert exe["state"] == "unavailable"
    assert exe["sha256"] is None
    assert str(external) not in str(result)


def test_inventory_returns_unknown_for_missing_root(tmp_path):
    result = inventory.collect_agent_file_inventory(tmp_path / "missing")
    assert result["state"] == "error"
    assert "inaccessible" in result["error"].lower()
    assert all(item["state"] == "unknown" for item in result["components"])


def test_agent_files_command_returns_correlated_metadata_and_is_packaged(monkeypatch):
    import xgent_wds as wds

    client = object.__new__(wds.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, payload))
    monkeypatch.setattr(wds, "DEVICE_ID", "windows-test")
    monkeypatch.setattr(
        inventory,
        "collect_agent_file_inventory",
        lambda **kwargs: {
            "schema": inventory.SCHEMA,
            "state": "partial",
            "components": [{"id": "agent", "files": []}],
            "protection": {"obfuscation_state": "unknown", "integrity_state": "unverified"},
        },
    )

    assert "agent_files" in wds.SUPPORTED_COMMANDS
    client._do_agent_files({"id": "inventory-request-1"})

    assert len(sent) == 1
    topic, response = sent[0]
    assert topic == "output"
    assert response["type"] == "agent_files"
    assert response["device_id"] == "windows-test"
    assert response["id"] == response["request_id"] == "inventory-request-1"
    assert response["state"] == "partial"
    assert response["ok"] is True

    spec = Path(wds.__file__).with_name("XGENT-WDS.spec").read_text(encoding="utf-8")
    assert "'agent_file_inventory'" in spec


def test_readable_file_view_accepts_only_fixed_text_file_ids_and_caps_output(tmp_path):
    source = _write(tmp_path, "version_info.txt", b"A" * (inventory.MAX_READ_BYTES + 100))
    _write(tmp_path, ".env", b"SECRET=do-not-read")

    result = inventory.read_allowlisted_text_file("agent-version", tmp_path)

    assert result["state"] == "available"
    assert result["file_id"] == "agent-version"
    assert result["relative_path"] == "version_info.txt"
    assert result["truncated"] is True
    assert len(result["content"].encode("utf-8")) <= inventory.MAX_READ_BYTES
    assert "SECRET" not in str(result)
    assert source.exists()
    assert inventory.read_allowlisted_text_file(".env", tmp_path)["state"] == "not_allowed"
    assert inventory.read_allowlisted_text_file("../outside.txt", tmp_path)["state"] == "not_allowed"


def test_readable_file_view_rejects_symlinked_allowlisted_file(tmp_path):
    root = tmp_path / "install"
    root.mkdir()
    external = _write(tmp_path, "outside.txt", b"external")
    link = root / "version_info.txt"
    try:
        link.symlink_to(external)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"file symlink unavailable: {exc}")

    result = inventory.read_allowlisted_text_file("agent-version", root)
    assert result["state"] == "unavailable"
    assert "external" not in str(result)


def test_agent_file_get_command_returns_correlated_allowlisted_content(monkeypatch, tmp_path):
    import xgent_wds as wds

    (tmp_path / "version_info.txt").write_text("XIDER test", encoding="utf-8")
    client = object.__new__(wds.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, payload))
    monkeypatch.setattr(wds, "DEVICE_ID", "windows-test")
    monkeypatch.setattr(inventory, "_default_install_root", lambda: tmp_path)

    assert "agent_file_get" in wds.SUPPORTED_COMMANDS
    client._do_agent_file_get({"id": "file-request-1", "file_id": "agent-version"})

    topic, response = sent[-1]
    assert topic == "output"
    assert response["type"] == "agent_file_get"
    assert response["device_id"] == "windows-test"
    assert response["id"] == response["request_id"] == "file-request-1"
    assert response["file_id"] == "agent-version"
    assert response["state"] == "available"
    assert response["content"] == "XIDER test"

    sent.clear()
    client._do_agent_file_get({"id": "file-request-2", "file_id": "../../.env"})
    assert sent[-1][1]["state"] == "not_allowed"
    assert "SECRET" not in str(sent[-1])
