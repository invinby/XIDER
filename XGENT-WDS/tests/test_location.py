"""One-shot location contract: OS doubles replace only native hardware/network."""
import datetime
import json
import urllib.request
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
import xgent_wds as agent

@pytest.fixture(autouse=True)
def isolated_native_diagnostic(monkeypatch):
    import location_adapter
    import os
    monkeypatch.setattr(os, "startfile", lambda uri: None, raising=False)
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

def test_windows_native_diagnostic_requires_explicit_os_grant():
    import location_adapter as adapter
    backend = object.__new__(adapter.WindowsLocationBackend)
    backend.locator = SimpleNamespace(location_status=0)
    backend.capability = SimpleNamespace(check_access=lambda: 3)
    assert backend.diagnose() == ("not_determined", True)
    backend.capability = SimpleNamespace(check_access=lambda: 2)
    assert backend.diagnose() == ("denied", True)
    backend.capability = SimpleNamespace(check_access=lambda: 4)
    backend.locator.location_status = 3
    assert backend.diagnose() == ("granted", False)

@pytest.mark.parametrize("status,want", [(0, "denied"), (1, "not_determined"),
                                      (2, "denied"), (3, "not_determined"), (4, "granted")])
def test_windows_capability_enum_matches_documented_os_status(status, want):
    import location_adapter as adapter
    backend = object.__new__(adapter.WindowsLocationBackend)
    backend.locator = SimpleNamespace(location_status=0)
    backend.capability = SimpleNamespace(check_access=lambda: status)
    assert backend.diagnose() == (want, True)

def test_windows_limited_diagnostic_opens_owner_settings_without_os_consent(monkeypatch):
    import os
    opened = []
    monkeypatch.setattr(os, "startfile", opened.append, raising=False)
    result = client_result({})
    assert result["state"] == "needs_live_test"
    assert opened == ["ms-settings:privacy-location"]

@pytest.mark.parametrize("status", [1, 2, 4])
def test_windows_allowed_startup_status_performs_exactly_one_bounded_read(status):
    import location_adapter as adapter
    attempts = []
    async def get_position(maximum_age, timeout):
        attempts.append((maximum_age.total_seconds(), timeout.total_seconds()))
        return SimpleNamespace(coordinate=SimpleNamespace(
            point=SimpleNamespace(position=SimpleNamespace(latitude=41.311, longitude=69.279)),
            accuracy=80.0))
    backend = object.__new__(adapter.WindowsLocationBackend)
    backend.capability = SimpleNamespace(check_access=lambda: 4)
    backend.locator = SimpleNamespace(location_status=status, get_geoposition_async=get_position)
    result = adapter.read_system_location(backend)
    assert attempts == [(0.0, 8.0)]
    assert result["state"] == "granted" and result["source"] == "system"
    assert result["accuracy_m"] == 80.0
