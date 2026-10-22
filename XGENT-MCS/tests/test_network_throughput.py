from urllib.error import URLError

import network_throughput as throughput


class _Response:
    status = 200

    def __init__(self, body=b"x" * 100, headers=None):
        self.body = body
        self.headers = headers or {}
        self.read_limits = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, limit=-1):
        self.read_limits.append(limit)
        return self.body[:limit]


def test_endpoint_requires_one_https_well_known_path_without_redirect_inputs():
    assert throughput.parse_endpoint("https://test.example/.well-known/xider-bandwidth") == {
        "url": "https://test.example/.well-known/xider-bandwidth",
        "host": "test.example",
    }
    for value in (
        "http://test.example/.well-known/xider-bandwidth",
        "https://user@test.example/.well-known/xider-bandwidth",
        "https://test.example/other",
        "https://test.example/.well-known/xider-bandwidth?x=1",
        "https://test.example/.well-known/xider-bandwidth#fragment",
    ):
        assert throughput.parse_endpoint(value) is None


def test_measurement_uses_capped_download_and_fixed_upload_with_measured_rates():
    calls = []
    responses = [_Response(), _Response(b"", {"X-Xider-Received-Bytes": str(throughput.UPLOAD_BYTES)})]

    def opener(request, *, timeout):
        calls.append((request, timeout))
        return responses[len(calls) - 1]

    ticks = iter((1.0, 2.0, 3.0, 4.0))
    result = throughput.measure_throughput(
        "https://test.example/.well-known/xider-bandwidth",
        opener=opener,
        clock=lambda: next(ticks),
    )

    assert result == {
        "state": "available",
        "source": "https_xider_bandwidth_endpoint",
        "download_bytes": 100,
        "download_duration_ms": 1000,
        "download_bytes_per_second": 100,
        "upload_bytes": throughput.UPLOAD_BYTES,
        "upload_duration_ms": 1000,
        "upload_bytes_per_second": throughput.UPLOAD_BYTES,
    }
    assert len(calls) == 2
    download, upload = calls[0][0], calls[1][0]
    assert download.method == "GET" and "direction=download" in download.full_url
    assert download.get_header("Accept-encoding") == "identity"
    assert upload.method == "POST" and "direction=upload" in upload.full_url
    assert upload.data == b"\0" * throughput.UPLOAD_BYTES
    assert calls[0][1] <= 5 and calls[1][1] <= 5
    assert responses[0].read_limits == [throughput.MAX_DOWNLOAD_BYTES + 1]


def test_oversized_download_and_failed_upload_never_report_available():
    oversized = _Response(b"x" * (throughput.MAX_DOWNLOAD_BYTES + 1))
    too_large = throughput.measure_throughput(
        "https://test.example/.well-known/xider-bandwidth",
        opener=lambda *_args, **_kwargs: oversized,
    )
    assert too_large["state"] == "limit_exceeded"
    assert too_large["download_bytes"] == throughput.MAX_DOWNLOAD_BYTES + 1

    calls = 0

    def upload_failure(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _Response()
        raise URLError("provider detail must not escape")

    failed = throughput.measure_throughput(
        "https://test.example/.well-known/xider-bandwidth", opener=upload_failure
    )
    assert failed["state"] == "unknown"
    assert failed["detail"] == "probe_failed"
    assert "provider detail" not in str(failed)


def test_agent_publishes_correlated_throughput_result(monkeypatch):
    import xgent_mcs as mcs

    published = []
    client = object.__new__(mcs.XgentClient)
    client._publish_response = lambda topic, payload: published.append((topic, payload))
    monkeypatch.setattr(mcs.network_throughput, "measure_throughput", lambda endpoint: {
        "state": "available", "source": throughput.SOURCE,
        "download_bytes": 100, "download_duration_ms": 1000,
        "download_bytes_per_second": 100, "upload_bytes": throughput.UPLOAD_BYTES,
        "upload_duration_ms": 1000, "upload_bytes_per_second": throughput.UPLOAD_BYTES,
    })

    client._do_network_throughput({"id": "throughput-1", "endpoint": "https://test.example/.well-known/xider-bandwidth"})

    topic, result = published[0]
    assert topic == "network_throughput"
    assert result["schema"] == "xider.network-throughput.v1"
    assert result["feature_id"] == "D04-F04" and result["request_id"] == "throughput-1"
    assert result["endpoint"] == "https://test.example/.well-known/xider-bandwidth"
    assert result["data"]["download_bytes_per_second"] == 100
