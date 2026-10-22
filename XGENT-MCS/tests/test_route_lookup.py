import route_lookup as routes
import xgent_mcs as agent


def test_macos_route_parser_reports_route_without_exposing_raw_output():
    output = """route to: 203.0.113.8
destination: 203.0.113.0
gateway: 192.0.2.1
interface: en0
flags: <UP,GATEWAY,DONE,STATIC>
mtu: 1500
"""
    assert routes.parse_macos_route(output, "203.0.113.8") == {
        "state": "available", "destination": "203.0.113.8", "route_prefix": "203.0.113.0",
        "gateway": "192.0.2.1", "interface": "en0", "metric": None,
    }


def test_macos_query_uses_validated_destination_and_read_only_route_get():
    calls = []
    def runner(args, **kwargs):
        calls.append((args, kwargs))
        return type("Result", (), {"stdout": "destination: 2001:db8::/64\ngateway: 2001:db8::1\ninterface: en0\n", "stderr": "", "returncode": 0})()

    result = routes.lookup_route("2001:db8::4", platform="macos", runner=runner, which=lambda name: "/sbin/route")

    assert result["state"] == "available" and result["interface"] == "en0"
    assert calls[0][0] == ["/sbin/route", "-n", "get", "-inet6", "2001:db8::4"]
    assert calls[0][1]["timeout"] <= 8


def test_agent_publishes_correlated_route_lookup(monkeypatch):
    published = []
    client = object.__new__(agent.XgentClient)
    client._publish_response = lambda topic, payload: published.append((topic, payload))
    monkeypatch.setattr(routes, "lookup_route", lambda destination: {
        "state": "available", "destination": destination, "route_prefix": "default",
        "gateway": "192.0.2.1", "interface": "en0", "metric": None,
    })

    client._do_route_lookup({"id": "route-1", "destination": "203.0.113.8"})

    topic, response = published[0]
    assert topic == "route_lookup"
    assert response["feature_id"] == "D04-F22" and response["request_id"] == "route-1"
    assert response["data"]["interface"] == "en0"
