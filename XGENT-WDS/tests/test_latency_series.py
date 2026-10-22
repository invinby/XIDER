import socket

import pytest

import latency_series


class _Connection:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_series_measures_five_bounded_tcp_connects_and_summarizes_successes():
    connections = []
    ticks = iter([1.0, 1.012, 2.0, 2.024, 3.0, 3.036, 4.0, 4.048, 5.0, 5.060])

    def connector(address, *, timeout):
        connection = _Connection()
        connections.append((address, timeout, connection))
        return connection

    result = latency_series.measure_latency_series(
        "example.com", 443, connector=connector, monotonic=lambda: next(ticks), sleeper=lambda _: None,
    )

    assert result["state"] == "available"
    assert result["source"] == latency_series.SOURCE
    assert [sample["latency_ms"] for sample in result["samples"]] == [12.0, 24.0, 36.0, 48.0, 60.0]
    assert result["summary"] == {"successful": 5, "attempted": 5, "min_ms": 12.0, "mean_ms": 36.0, "max_ms": 60.0}
    assert len(connections) == 5
    assert all(address == ("example.com", 443) and timeout == latency_series.CONNECT_TIMEOUT for address, timeout, _ in connections)
    assert all(connection.closed for _, _, connection in connections)


def test_failed_samples_are_unknown_not_zero_and_do_not_pollute_summary():
    results = iter([_Connection(), socket.timeout(), _Connection(), ConnectionRefusedError(), _Connection()])
    ticks = iter([0.0, 0.01, 1.0, 2.0, 2.03, 3.0, 4.0, 4.05])

    def connector(*_args, **_kwargs):
        result = next(results)
        if isinstance(result, BaseException):
            raise result
        return result

    result = latency_series.measure_latency_series(
        "example.com", 443, connector=connector, monotonic=lambda: next(ticks), sleeper=lambda _: None,
    )

    assert result["state"] == "partial"
    assert [sample["state"] for sample in result["samples"]] == ["connected", "timeout", "connected", "refused", "connected"]
    assert [sample["latency_ms"] for sample in result["samples"]] == [10.0, None, 30.0, None, 50.0]
    assert result["summary"] == {"successful": 3, "attempted": 5, "min_ms": 10.0, "mean_ms": 30.0, "max_ms": 50.0}


@pytest.mark.parametrize(("host", "port"), [("https://example.com", 443), ("example.com", 0), ("example.com", 65536)])
def test_invalid_endpoint_never_connects(host, port):
    calls = []
    result = latency_series.measure_latency_series(host, port, connector=lambda *args, **kwargs: calls.append(args))
    assert result["state"] == "invalid_request"
    assert result["samples"] == []
    assert not calls


def test_agent_publishes_correlated_series_from_fixed_five_attempt_contract(monkeypatch):
    import xgent_wds as agent

    observed = []
    monkeypatch.setattr(latency_series, "measure_latency_series", lambda host, port: observed.append((host, port)) or {
        "state": "available", "source": latency_series.SOURCE,
        "samples": [{"index": i, "state": "connected", "latency_ms": float(i)} for i in range(1, 6)],
        "summary": {"successful": 5, "attempted": 5, "min_ms": 1.0, "mean_ms": 3.0, "max_ms": 5.0},
        "detail": None,
    })
    client = object.__new__(agent.XgentClient)
    published = []
    client._publish_response = lambda topic, body: published.append((topic, dict(body)))

    client._do_latency_series({"id": "latency-1", "host": "example.com", "port": 443})

    topic, body = published[0]
    assert topic == "latency_series"
    assert observed == [("example.com", 443)]
    assert body["type"] == "latency_series" and body["schema"] == "xider.latency-series.v1"
    assert body["feature_id"] == "D04-F01" and body["request_id"] == "latency-1"
    assert body["host"] == "example.com" and body["port"] == 443
    assert body["samples"][0]["index"] == 1 and body["summary"]["attempted"] == 5
