import vpn_route
import xgent_wds as agent


def test_vpn_summary_reports_only_tunnel_candidate_counts_not_interface_names():
    result = vpn_route.summarize_interfaces([
        {"name": "Wi-Fi", "description": "Intel Wireless", "is_up": True},
        {"name": "wg0", "description": "WireGuard Tunnel", "is_up": True},
        {"name": "old-tap", "description": "TAP-Windows Adapter", "is_up": False},
    ])

    assert result == {
        "state": "available", "up_tunnel_candidates": 1,
        "down_tunnel_candidates": 1, "route_assessment": "not_verified",
    }
    assert "wg0" not in repr(result) and "TAP-Windows" not in repr(result)


def test_vpn_summary_distinguishes_no_candidates_from_unavailable_evidence():
    assert vpn_route.summarize_interfaces([
        {"name": "Wi-Fi", "description": "Intel Wireless", "is_up": True},
    ]) == {
        "state": "available", "up_tunnel_candidates": 0,
        "down_tunnel_candidates": 0, "route_assessment": "not_verified",
    }
    assert vpn_route.summarize_interfaces(None) == {
        "state": "unknown", "up_tunnel_candidates": 0,
        "down_tunnel_candidates": 0, "route_assessment": "not_verified",
    }


def test_agent_publishes_correlated_vpn_route_summary(monkeypatch):
    monkeypatch.setattr(vpn_route, "collect_vpn_summary", lambda: {
        "state": "available", "up_tunnel_candidates": 1,
        "down_tunnel_candidates": 0, "route_assessment": "not_verified",
    })
    client = object.__new__(agent.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, dict(payload)))

    client._do_vpn_route_status({"id": "vpn-1"})

    topic, result = sent[0]
    assert topic == "vpn_route_status"
    assert result["feature_id"] == "D04-F24" and result["request_id"] == "vpn-1"
    assert result["route_assessment"] == "not_verified"
    assert "interface_names" not in result
