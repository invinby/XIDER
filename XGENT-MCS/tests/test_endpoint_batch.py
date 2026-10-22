import endpoint_batch
import xgent_mcs as agent


def test_fixed_endpoint_set_probes_exactly_three_targets_once_and_discards_addresses():
    calls = []

    def probe(host, port, protocol, *, timeout):
        calls.append((host, port, protocol, timeout))
        return {"state": "reachable", "detail": None, "source": "test"}

    result = endpoint_batch.check_public_set(probe=probe)

    assert [row[0] for row in calls] == ["example.com", "www.cloudflare.com", "github.com"]
    assert all(port == 443 and protocol == "tcp" and 0.25 <= timeout <= 3 for _, port, protocol, timeout in calls)
    assert result == {
        "preset": "public_basics", "state": "available", "results": [
            {"target_id": "example", "state": "reachable", "detail": None},
            {"target_id": "cloudflare", "state": "reachable", "detail": None},
            {"target_id": "github", "state": "reachable", "detail": None},
        ],
    }
    assert "github.com" not in repr(result)


def test_endpoint_set_preserves_distinct_unknown_and_unreachable_results():
    states = iter(("reachable", "unreachable", "unknown"))
    result = endpoint_batch.check_public_set(
        probe=lambda *_args, **_kwargs: {"state": next(states), "detail": None, "source": "test"},
    )
    assert [item["state"] for item in result["results"]] == ["reachable", "unreachable", "unknown"]
    assert all(item["detail"] is None for item in result["results"])


def test_agent_publishes_correlated_bounded_endpoint_set(monkeypatch):
    monkeypatch.setattr(endpoint_batch, "check_public_set", lambda: {
        "preset": "public_basics", "state": "available", "results": [
            {"target_id": "example", "state": "reachable", "detail": None},
            {"target_id": "cloudflare", "state": "unknown", "detail": "probe_timeout"},
            {"target_id": "github", "state": "unreachable", "detail": "connection_refused"},
        ],
    })
    client = object.__new__(agent.XgentClient)
    sent = []
    client._publish_response = lambda topic, payload: sent.append((topic, dict(payload)))

    client._do_endpoint_batch({"id": "batch-1"})

    topic, result = sent[0]
    assert topic == "endpoint_batch"
    assert result["feature_id"] == "D04-F25" and result["request_id"] == "batch-1"
    assert len(result["results"]) == 3
    assert "host" not in result and "address" not in result and "ip" not in result
