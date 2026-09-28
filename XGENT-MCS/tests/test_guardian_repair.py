import io
import os
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def guardian_module(monkeypatch):
    monkeypatch.setenv("SHARED_KEY", "test-only-shared-key-guard-0123456789")
    monkeypatch.setenv("MQTT_BROKER", "localhost")
    monkeypatch.setenv("MQTT_PORT", "8883")
    monkeypatch.setenv("MQTT_TLS", "true")
    monkeypatch.setenv("ENCRYPT_PAYLOAD", "true")
    monkeypatch.setenv("MQTT_PREFIX", "xgent/test")
    monkeypatch.setenv("XIDER_UPDATE_BRANCH", "test-repair")
    import xider_guardian

    return xider_guardian


def test_guardian_downloads_missing_allowlisted_agent_and_recreates_env(
    guardian_module, monkeypatch, tmp_path
):
    module = guardian_module
    monkeypatch.setattr(module, "SCRIPT_DIR", tmp_path)
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        for name in module.AGENT_FILES:
            content = b"# recovered test file\n"
            archive.writestr(f"XIDER-test/XGENT-MCS/{name}", content)

    def fake_download(_url, destination):
        Path(destination).write_bytes(payload.getvalue())

    monkeypatch.setattr(module.urllib.request, "urlretrieve", fake_download)
    guardian = object.__new__(module.Guardian)

    restored = guardian._restore_missing_agent_files()

    assert set(restored) == set(module.AGENT_FILES) | {".env"}
    assert (tmp_path / "xgent_mcs.py").read_text(encoding="utf-8").startswith("# recovered")
    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert 'SHARED_KEY="test-only-shared-key-guard-0123456789"' in env
    assert "MQTT_BROKER=localhost" not in env
    if os.name != "nt":
        assert (tmp_path / ".env").stat().st_mode & 0o777 == 0o600


def test_guardian_rejects_unsafe_update_branch(guardian_module, monkeypatch, tmp_path):
    module = guardian_module
    monkeypatch.setattr(module, "SCRIPT_DIR", tmp_path)
    monkeypatch.setattr(module, "XIDER_UPDATE_BRANCH", "../main")
    guardian = object.__new__(module.Guardian)

    with pytest.raises(RuntimeError, match="Небезопасное имя ветки"):
        guardian._restore_missing_agent_files()
