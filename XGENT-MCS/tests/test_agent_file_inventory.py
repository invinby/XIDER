import hashlib
import json

import pytest

import agent_file_inventory as inventory
import xgent_mcs as mcs


def test_inventory_hashes_only_allowlisted_regular_files_and_never_returns_contents(tmp_path):
    root = tmp_path / "install"
    root.mkdir()
    source = b"VERSION = \"4.3.4\"\n# private-source-sentinel"
    (root / "xgent_mcs.py").write_bytes(source)
    (root / "config.py").write_text('VERSION = "4.3.4"\n', encoding="utf-8")
    (root / ".env").write_text("SHARED_KEY=secret-sentinel", encoding="utf-8")
    (root / "agent.log").write_text("log-sentinel", encoding="utf-8")
    (root / "unrelated.txt").write_text("unrelated-sentinel", encoding="utf-8")

    report = inventory.collect_agent_file_inventory(root)

    assert report["schema"] == "xider.agent-files.v1"
    assert report["generated_at"].endswith("Z")
    agent = next(item for item in report["components"] if item["id"] == "agent")
    source_row = next(item for item in agent["files"] if item["path"].endswith("xgent_mcs.py"))
    assert source_row["size_bytes"] == len(source)
    assert source_row["sha256"] == hashlib.sha256(source).hexdigest()
    assert source_row["integrity_state"] == "unverified"
    assert source_row["signature_state"] == "not_applicable"
    assert agent["version"] == "4.3.4"
    assert agent["build_mode"] == "python_source"
    assert agent["obfuscation_state"] == "not_applied"
    serialized = json.dumps(report)
    for sentinel in ("private-source-sentinel", "secret-sentinel", "log-sentinel", "unrelated-sentinel"):
        assert sentinel not in serialized
    assert all(".env" not in row["path"] and "agent.log" not in row["path"]
               for component in report["components"] for row in component["files"])


def test_inventory_rejects_a_symlinked_install_root(tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    (actual / "xgent_mcs.py").write_text("source", encoding="utf-8")
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(actual, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")

    report = inventory.collect_agent_file_inventory(linked)

    assert report["state"] == "missing"
    assert all(component["state"] == "unknown" for component in report["components"])
    assert all(component["files"] == [] for component in report["components"])


def test_inventory_skips_symlinked_allowlisted_file(tmp_path):
    root = tmp_path / "install"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("secret-outside-content", encoding="utf-8")
    try:
        (root / "xgent_mcs.py").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")

    report = inventory.collect_agent_file_inventory(root)

    agent = next(item for item in report["components"] if item["id"] == "agent")
    source_row = next(row for row in agent["files"] if row["name"] == "xgent_mcs.py")
    assert agent["state"] == "partial"
    assert source_row["state"] == "unavailable"
    assert source_row["sha256"] is None
    assert all("outside.py" not in row["path"] for row in agent["files"])
    assert "secret-outside-content" not in json.dumps(report)


def test_inventory_marks_every_file_and_requires_all_applicable_files_for_present(tmp_path):
    root = tmp_path / "install"
    root.mkdir()
    (root / "xgent_mcs.py").write_text('VERSION = "4.3.4"', encoding="utf-8")
    (root / "config.py").write_text('VERSION = "4.3.4"', encoding="utf-8")

    report = inventory.collect_agent_file_inventory(root)
    agent = next(item for item in report["components"] if item["id"] == "agent")
    by_name = {row["name"]: row for row in agent["files"]}

    assert agent["state"] == "partial"
    assert agent["version"] == "4.3.4"
    assert len(agent["files"]) == len(inventory._AGENT_FILES)
    assert by_name["xgent_mcs.py"]["state"] == "present"
    assert by_name["config.py"]["state"] == "present"
    assert by_name["XIDER.app/Contents/MacOS/XGENT-MCS"]["state"] == "unsupported"


def test_inventory_does_not_show_version_without_a_present_entrypoint(tmp_path):
    root = tmp_path / "install"
    root.mkdir()
    (root / "config.py").write_text('VERSION = "4.3.4"', encoding="utf-8")

    report = inventory.collect_agent_file_inventory(root)
    agent = next(item for item in report["components"] if item["id"] == "agent")

    assert agent["state"] == "partial"
    assert agent["version"] is None


def test_inventory_reports_macos_code_signature_and_still_marks_integrity_unverified(tmp_path, monkeypatch):
    root = tmp_path / "install"
    binary = root / "XIDER.app" / "Contents" / "MacOS" / "XGENT-MCS"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"fixture-binary")
    monkeypatch.setattr(inventory.sys, "platform", "darwin")
    monkeypatch.setattr(inventory.sys, "executable", "/Applications/XIDER.app/Contents/MacOS/XGENT-MCS")
    monkeypatch.setattr(inventory.shutil, "which", lambda name: "/usr/bin/codesign")

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(inventory.subprocess, "run", lambda *_args, **_kwargs: Result())
    report = inventory.collect_agent_file_inventory(root, agent_version="4.3.4")

    agent = next(item for item in report["components"] if item["id"] == "agent")
    binary_row = next(item for item in agent["files"] if item["path"].endswith("XGENT-MCS"))
    assert binary_row["signature_state"] == "verified"
    assert binary_row["signature_evidence"] == "codesign_verify_exit_0"
    assert binary_row["integrity_state"] == "unverified"
    assert report["protection"]["obfuscation_state"] == "not_applied"
    assert all(component["obfuscation_state"] == "not_applied" for component in report["components"])
    assert all(component["build_mode"] == "nuitka_compiled" for component in report["components"])


def test_agent_files_command_publishes_correlated_bounded_inventory(tmp_path, monkeypatch):
    client = object.__new__(mcs.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, payload))
    monkeypatch.setattr(mcs, "DEVICE_ID", "mac-device")
    monkeypatch.setattr(mcs, "VERSION", "4.3.4")
    monkeypatch.setattr(mcs, "_agent_install_dir", lambda: tmp_path)
    (tmp_path / "xgent_mcs.py").write_text("source-only", encoding="utf-8")
    monkeypatch.setattr(inventory, "_codesign_state", lambda _path: ("verified", "fixture"))

    client._do_agent_files({"id": "inventory-ticket"})

    assert "agent_files" in mcs.SUPPORTED_COMMANDS
    assert len(sent) == 1
    topic, result = sent[0]
    assert topic == "output"
    assert result["type"] == "agent_files"
    assert result["device_id"] == "mac-device"
    assert result["id"] == result["request_id"] == "inventory-ticket"
    assert result["state"] == "partial"
    assert result["protection"]["obfuscation_state"] == "not_applied"
    assert "source-only" not in str(result)


def test_agent_files_helper_is_in_frozen_macos_bundle_spec():
    spec = inventory.Path(__file__).resolve().parents[1] / "XGENT-MCS.spec"
    assert "'agent_file_inventory'" in spec.read_text(encoding="utf-8")


def test_read_allowlisted_text_file_returns_bounded_content_and_rejects_unknown_ids(tmp_path):
    root = tmp_path / "install"
    root.mkdir()
    source = "A" * (inventory.MAX_READ_BYTES + 20)
    (root / "xgent_mcs.py").write_text(source, encoding="utf-8")
    (root / "config.py").write_text("PASSWORD = 'never expose'", encoding="utf-8")

    result = inventory.read_allowlisted_text_file("agent_mcs", root)
    denied = inventory.read_allowlisted_text_file("agent_config", root)

    assert result["state"] == "available"
    assert result["relative_path"] == "xgent_mcs.py"
    assert result["content"].encode("utf-8") == b"A" * inventory.MAX_READ_BYTES
    assert result["truncated"] is True
    assert denied["state"] == "not_allowed"
    assert denied["content"] == ""


def test_read_allowlisted_text_file_rejects_symlinked_parent_and_reports_missing(tmp_path):
    root = tmp_path / "install"
    root.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "xider_layers.py").write_text("outside", encoding="utf-8")
    try:
        (root / "xider_layers.py").symlink_to(elsewhere / "xider_layers.py")
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")

    missing = inventory.read_allowlisted_text_file("agent_mcs", root)
    outside = inventory.read_allowlisted_text_file("agent_layers", root)

    assert missing["state"] == "unavailable"
    assert outside["state"] == "unavailable"
    assert "outside" not in outside["content"]


def test_inventory_marks_content_readable_files_with_file_ids(tmp_path):
    root = tmp_path / "install"
    root.mkdir()
    (root / "xgent_mcs.py").write_text("source", encoding="utf-8")
    (root / "config.py").write_text('VERSION = "4.3.4"\n', encoding="utf-8")

    report = inventory.collect_agent_file_inventory(root)

    agent = next(item for item in report["components"] if item["id"] == "agent")
    source = next(item for item in agent["files"] if item["path"].endswith("xgent_mcs.py"))
    config = next(item for item in agent["files"] if item["path"].endswith("config.py"))
    assert source["file_id"] == "agent_mcs"
    assert "file_id" not in config


def test_agent_file_get_command_publishes_correlated_content_without_logging_it(tmp_path, monkeypatch):
    client = object.__new__(mcs.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, payload))
    monkeypatch.setattr(mcs, "DEVICE_ID", "mac-device")
    monkeypatch.setattr(mcs, "_agent_install_dir", lambda: tmp_path)
    monkeypatch.setattr(mcs.log, "exception", lambda *args, **kwargs: pytest.fail("unexpected content log"))
    (tmp_path / "xgent_mcs.py").write_text("owner-readable-source", encoding="utf-8")

    client._do_agent_file_get({"id": "read-ticket", "file_id": "agent_mcs"})

    assert "agent_file_get" in mcs.SUPPORTED_COMMANDS
    assert len(sent) == 1
    topic, result = sent[0]
    assert topic == "output"
    assert result == {
        "type": "agent_file_get", "schema": "xider.agent-file-content.v1",
        "device_id": "mac-device", "id": "read-ticket", "request_id": "read-ticket",
        "file_id": "agent_mcs", "state": "available",
        "relative_path": "xgent_mcs.py", "content": "owner-readable-source", "truncated": False,
    }


def test_allowlisted_file_ids_fit_bot_normalizer_and_telegram_callback_limit():
    for file_id in inventory._READABLE_FILE_IDS:
        encoded = file_id.encode("utf-8")
        assert len(encoded) <= 24
        assert all(char.islower() or char.isdigit() or char in "_-" for char in file_id)
        callback_data = f"agentfiles:file:ffffffffff:999:999:{file_id}"
        assert len(callback_data.encode("utf-8")) <= 64
