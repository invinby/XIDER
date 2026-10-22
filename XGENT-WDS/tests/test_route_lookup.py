import json

import route_lookup as routes
import xgent_wds as agent


def test_windows_route_parser_returns_selected_os_route_and_discards_extra_fields():
    raw = json.dumps({"DestinationPrefix": "0.0.0.0/0", "NextHop": "192.0.2.1",
                      "InterfaceAlias": "Wi-Fi", "InterfaceIndex": 12, "RouteMetric": 25,
                      "PolicyStore": "ActiveStore", "Secret": "ignored"})
    assert routes.parse_windows_route(raw, "203.0.113.8") == {
        "state": "available", "destination": "203.0.113.8", "route_prefix": "0.0.0.0/0",
        "gateway": "192.0.2.1", "interface": "Wi-Fi", "metric": 25,
    }


def test_destination_must_be_one_literal_unicast_address():
    assert routes.normalize_destination("192.0.2.7") == "192.0.2.7"
    assert routes.normalize_destination("2001:db8::4") == "2001:db8::4"
    for invalid in ("example.com", "0.0.0.0", "224.0.0.1", "192.0.2.1%eth0", " 192.0.2.1"):
        try:
            routes.normalize_destination(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted invalid route target: {invalid!r}")


def test_windows_query_is_read_only_validated_and_does_not_send_packets():
    calls = []
    def runner(args, **kwargs):
        calls.append((args, kwargs))
        return type("Result", (), {"stdout": json.dumps({"DestinationPrefix": "203.0.113.0/24",
            "NextHop": "0.0.0.0", "InterfaceAlias": "Ethernet", "InterfaceIndex": 5,
            "RouteMetric": 10}), "stderr": "", "returncode": 0})()

    result = routes.lookup_route("203.0.113.8", platform="windows", runner=runner, which=lambda name: "powershell.exe")

    assert result["state"] == "available" and result["interface"] == "Ethernet"
    assert calls[0][0][0] == "powershell.exe"
    assert "Find-NetRoute" in calls[0][0][-1]
    assert "203.0.113.8" in calls[0][0][-1]
    assert kwargs_timeout(calls[0][1]) <= 8


def kwargs_timeout(kwargs):
    return kwargs["timeout"]


def test_agent_publishes_correlated_route_lookup(monkeypatch):
    published = []
    client = object.__new__(agent.XgentClient)
    client._publish_response = lambda topic, payload: published.append((topic, payload))
    monkeypatch.setattr(routes, "lookup_route", lambda destination: {
        "state": "available", "destination": destination, "route_prefix": "0.0.0.0/0",
        "gateway": "192.0.2.1", "interface": "Wi-Fi", "metric": 25,
    })

    client._do_route_lookup({"id": "route-1", "destination": "203.0.113.8"})

    topic, response = published[0]
    assert topic == "route_lookup"
    assert response["feature_id"] == "D04-F22" and response["request_id"] == "route-1"
    assert response["data"]["interface"] == "Wi-Fi"
