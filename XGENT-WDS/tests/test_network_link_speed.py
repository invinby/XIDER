from types import SimpleNamespace

import network_link_speed as link_speed
import xgent_wds as agent


def test_link_speed_reports_os_advertised_mbps_for_selected_adapter():
    stats = {"Ethernet 4": SimpleNamespace(isup=True, speed=1000)}
    catalog = link_speed.collect_catalog(stats)
    interface_id = catalog["interfaces"][0]["interface_id"]

    result = link_speed.collect_link_speed(interface_id, stats, checked_at="2026-10-21T06:00:00Z")

    assert result == {
        "state": "available",
        "source": link_speed.SOURCE,
        "interface_id": interface_id,
        "label": "Ethernet 4",
        "speed_mbps": 1000,
        "checked_at": "2026-10-21T06:00:00Z",
        "detail": None,
    }


def test_link_speed_does_not_turn_missing_or_invalid_speed_into_zero():
    missing = {"Ethernet": SimpleNamespace(isup=True, speed=0)}
    catalog = link_speed.collect_catalog(missing)
    interface_id = catalog["interfaces"][0]["interface_id"]

    result = link_speed.collect_link_speed(interface_id, missing, checked_at="2026-10-21T06:00:00Z")

    assert result["state"] == "unsupported"
    assert result["speed_mbps"] is None
    assert result["detail"] == "link_speed_unavailable"


def test_agent_publishes_correlated_link_speed_result(monkeypatch):
    published = []
    client = object.__new__(agent.XgentClient)
    client._publish_response = lambda topic, payload: published.append((topic, payload))
    monkeypatch.setattr(link_speed, "collect_catalog", lambda: {
        "state": "available", "interfaces": [{"interface_id": "a" * 20, "label": "Ethernet"}],
    })
    monkeypatch.setattr(link_speed, "collect_link_speed", lambda interface_id: {
        "state": "available", "source": link_speed.SOURCE, "interface_id": interface_id,
        "label": "Ethernet", "speed_mbps": 1000, "checked_at": "2026-10-21T06:00:00Z", "detail": None,
    })

    client._do_network_link_speed({"id": "link-1", "interface_id": "a" * 20})

    topic, response = published[0]
    assert topic == "network_link_speed"
    assert response["feature_id"] == "D04-F21" and response["request_id"] == "link-1"
    assert response["data"]["speed_mbps"] == 1000
