import datetime
import io
import ipaddress
from types import SimpleNamespace

from xg_commands import network


def test_external_ip_reports_only_validated_public_ip():
    published = []
    calls = []
    network.external_ip(
        {"id": "valid_id-1"}, device_id="device-4",
        publish_response=lambda topic, response: published.append((topic, response)),
        urlopen=lambda url, **kwargs: calls.append((url, kwargs)) or io.BytesIO(b"8.8.8.8"),
        ipaddress_module=ipaddress, datetime_module=datetime,
    )

    assert calls == [("https://api.ipify.org", {"timeout": 6})]
    response = published[0][1]
    assert published[0][0] == "ext_ip"
    assert response["device_id"] == "device-4"
    assert response["request_id"] == "valid_id-1"
    assert response["state"] == "available" and response["ip"] == "8.8.8.8"
    assert response["text"] == "🌍 Внешний IP: 8.8.8.8"
    assert datetime.datetime.fromisoformat(response["checked_at"]).utcoffset() == datetime.timedelta(0)


def test_external_ip_hides_provider_errors_and_rejects_invalid_request_ids():
    published = []
    network.external_ip(
        {"id": "bad id"}, device_id="device-4",
        publish_response=lambda topic, response: published.append((topic, response)),
        urlopen=lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("proxy details")),
        ipaddress_module=ipaddress, datetime_module=datetime,
    )

    response = published[0][1]
    assert response["request_id"] is None
    assert response["state"] == "unknown" and response["ip"] == ""
    assert response["text"] == "Внешний IP не подтверждён"
    assert "proxy details" not in str(response)
