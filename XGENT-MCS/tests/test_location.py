"""One-shot location contract: OS doubles replace only native hardware/network."""
import datetime
import json
import urllib.request
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
import xgent_mcs as agent

@pytest.fixture(autouse=True)
def isolated_native_diagnostic(monkeypatch):
    import location_adapter
    monkeypatch.setattr(location_adapter, "native_backend",
                        lambda: SimpleNamespace(diagnose=lambda: ("not_determined", True)))

def client_result(payload):
    client = object.__new__(agent.XgentClient)
    sent = []
    client._publish_response = lambda topic, result: sent.append((topic, result))
    client._do_geo_location(payload)
    assert sent[0][0] == "geo_location"
    return sent[0][1]

def test_ip_is_explicit_and_never_has_coordinates(monkeypatch):
    response = Mock()
    response.__enter__ = lambda self: self
    response.__exit__ = lambda *args: None
    response.read.return_value = json.dumps({"city": "Tashkent", "region": "Tashkent",
        "latitude": 41.3, "longitude": 69.2, "loc": "41.3,69.2", "ip": "203.0.113.4"}).encode()
    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: response)
    result = client_result({"ip_fallback": True})
    assert result.get("source") == "ip_estimate"
    assert all(result.get(key) is None for key in ("latitude", "longitude", "accuracy_m"))
    assert "41.3" not in result["text"] and "69.2" not in result["text"]
    assert "Tashkent" in result["label"]
    assert "GPS" in result["text"]

def test_default_request_never_contacts_ip_provider(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Default request disclosed IP to an external provider")
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    result = client_result({})
    assert result.get("source") == "system"
    assert result["state"] in {"unavailable", "needs_live_test", "denied"}

@pytest.mark.parametrize("permission,enabled,want", [
    ("denied", True, "denied"), ("granted", False, "unavailable"),
    ("not_determined", True, "needs_live_test"), ("unknown", True, "needs_live_test"),
    ("unavailable", True, "unavailable"),
])
def test_permission_gate_never_reads_or_requests_consent(permission, enabled, want):
    import location_adapter
    backend = SimpleNamespace(diagnose=lambda: (permission, enabled))
    result = location_adapter.read_system_location(backend)
    assert result["state"] == want
    assert result["latitude"] is None
    assert result["source"] == "system"
    assert "settings" in result["label"].lower() or "настрой" in result["label"].lower()

@pytest.mark.parametrize("error,want", [(TimeoutError(), "needs_live_test"), (PermissionError(), "denied"),
                                      (OSError(), "unavailable")])
def test_native_failure_does_not_claim_location(error, want):
    import location_adapter
    def read(timeout):
        raise error
    backend = SimpleNamespace(diagnose=lambda: ("granted", True), read_once=read)
    result = location_adapter.read_system_location(backend)
    assert result["state"] == want and result["latitude"] is None

def test_valid_one_shot_system_coordinate_preserves_uncertainty():
    import location_adapter
    calls = []
    backend = SimpleNamespace(diagnose=lambda: ("granted", True),
        read_once=lambda timeout: calls.append(timeout) or (41.311, 69.279, 80.0))
    result = location_adapter.read_system_location(backend)
    assert result["source"] == "system" and result["state"] == "granted"
    assert (result["latitude"], result["longitude"], result["accuracy_m"]) == (41.311, 69.279, 80.0)
    assert len(calls) == 1
    assert datetime.datetime.fromisoformat(result["checked_at"]).utcoffset() == datetime.timedelta(0)

@pytest.mark.parametrize("position", [(91, 0, 1), (0, 181, 1), (0, 0, -1), (float("nan"), 0, 1)])
def test_invalid_native_coordinate_fails_closed(position):
    import location_adapter
    result = location_adapter.read_system_location(SimpleNamespace(
        diagnose=lambda: ("granted", True), read_once=lambda timeout: position))
    assert result["state"] == "unknown" and result["latitude"] is None

def test_string_truthy_value_is_not_ip_opt_in(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("String must not opt in")
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    assert client_result({"ip_fallback": "true"}).get("source") == "system"

@pytest.mark.parametrize("native_error", [False, True])
def test_mac_runloop_dispatch_returns_one_native_fix_or_failure(monkeypatch, native_error):
    import location_adapter as adapter
    import queue
    import threading
    import time
    class Manager:
        @classmethod
        def alloc(cls):
            return cls()
        def init(self):
            return self
        def setDelegate_(self, delegate):
            self.delegate = delegate
        def requestLocation(self):
            if native_error:
                raise OSError("native request failed")
            self.delegate.job["value"] = (41.311, 69.279, 80.0)
            self.delegate.job["done"].set()
        def stopUpdatingLocation(self):
            pass
        @staticmethod
        def locationServicesEnabled():
            return True
        @staticmethod
        def authorizationStatus():
            return 3
    runtime = object.__new__(adapter.MacLocationRuntime)
    runtime.queue = queue.Queue()
    runtime.active = []
    runtime.core = SimpleNamespace(CLLocationManager=Manager)
    runtime.delegate_class = type("Delegate", (), {"alloc": classmethod(lambda cls: cls()),
                                                  "init": lambda self: self})
    runtime.runloop = SimpleNamespace(runUntilDate_=lambda date: None)
    runtime.date = SimpleNamespace(dateWithTimeIntervalSinceNow_=lambda value: value)
    result = []
    def read():
        try:
            result.append(runtime.read_once(1))
        except OSError:
            result.append("unavailable")
    worker = threading.Thread(target=read)
    worker.start()
    deadline = time.monotonic() + 1
    while runtime.queue.empty() and time.monotonic() < deadline:
        time.sleep(0.001)
    runtime.pump()
    worker.join(1)
    assert result == (["unavailable"] if native_error else [(41.311, 69.279, 80.0)])
    assert not runtime.active

def test_mac_expired_job_cannot_later_read_location():
    import location_adapter as adapter
    import queue
    import threading
    runtime = object.__new__(adapter.MacLocationRuntime)
    runtime.queue = queue.Queue()
    runtime.active = []
    runtime.runloop = SimpleNamespace(runUntilDate_=lambda date: None)
    runtime.date = SimpleNamespace(dateWithTimeIntervalSinceNow_=lambda value: value)
    with pytest.raises(TimeoutError):
        runtime.read_once(0.001)
    # No core object exists: processing an expired job must skip native access.
    runtime.pump()

def test_mac_source_runtime_never_requests_location(monkeypatch):
    import location_adapter as adapter
    monkeypatch.setattr(adapter, "_runtime", None)
    result = adapter.read_system_location()
    assert result["state"] == "needs_live_test"
    assert result["latitude"] is None

def test_missing_native_runtime_does_not_crash_agent_startup(monkeypatch):
    import location_adapter as adapter
    monkeypatch.setattr(adapter, "signed_bundle_available", lambda: True)
    def missing():
        raise ImportError("CoreLocation unavailable")
    monkeypatch.setattr(adapter, "MacLocationRuntime", missing)
    assert adapter.initialize_runtime() is None

def test_launchagent_preserves_bundle_executable_identity(monkeypatch, tmp_path):
    import sys
    bundle_exe = tmp_path / "XIDER.app/Contents/MacOS/XGENT-MCS"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(bundle_exe))
    command = getattr(agent, "_worker_launch_command", None)
    assert callable(command), "Bundle worker startup missing"
    assert command() == [str(bundle_exe)]

def test_guardian_bundle_command_cannot_restore_removed_app(monkeypatch, tmp_path):
    import xider_guardian as guardian
    monkeypatch.setattr(guardian, "SCRIPT_DIR", tmp_path)
    worker = object.__new__(guardian.Guardian)
    worker.state = {"desired_running": True}
    command = getattr(worker, "_worker_command", None)
    assert callable(command), "Guardian must retain signed app worker identity"
    executable = tmp_path / "XIDER.app/Contents/MacOS/XGENT-MCS"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"fixture")
    monkeypatch.setattr(guardian, "_save_state", lambda *args: None)
    assert command() == [str(executable)]
    executable.unlink()
    with pytest.raises(RuntimeError, match="app"):
        command()
