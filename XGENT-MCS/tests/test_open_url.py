import threading
import time

import pytest

import xgent_mcs as mcs


def _make_client():
    client = object.__new__(mcs.XgentClient)
    client._command_context = threading.local()
    client._handlers = {"open_url": client._do_open_url}
    client._standby = False
    client._open_url_operations = {}
    client._open_url_operations_lock = threading.Lock()
    client._notifications = []
    client._responses = []
    client._acks = []
    client._notify_text = client._notifications.append
    client._publish_response = lambda topic, payload: client._responses.append((topic, dict(payload))) or True
    client._publish_ack = lambda *args, **kwargs: client._acks.append((args, kwargs))
    return client


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_accepts_http_and_https(monkeypatch, scheme):
    client = _make_client()
    client._command_context.cmd_id = "open-operation-1"
    opened = []
    monkeypatch.setattr(mcs.webbrowser, "open_new_tab", lambda url: opened.append(url) or True)

    result = client._do_open_url({
        "type": "open_url",
        "id": "open-operation-1",
        "url": f"{scheme}://example.com/docs",
        "count": 2,
    })

    assert opened == [f"{scheme}://example.com/docs"] * 2
    assert result["id"] == "open-operation-1"
    assert result["ok"] is True
    assert result["requested_count"] == 2
    assert result["accepted_count"] == 2
    assert client._responses == [("output", result)]


@pytest.mark.parametrize("browser_result", [False, None])
def test_counts_only_confirmed_browser_launch_acceptance(monkeypatch, browser_result):
    client = _make_client()
    client._command_context.cmd_id = "open-operation-2"
    monkeypatch.setattr(mcs.webbrowser, "open_new_tab", lambda _url: browser_result)

    with pytest.raises(RuntimeError):
        client._do_open_url({
            "type": "open_url",
            "id": "open-operation-2",
            "url": "https://example.com/docs",
            "count": 1,
        })

    result = client._responses[-1][1]
    assert result["id"] == "open-operation-2"
    assert result["ok"] is False
    assert result["accepted_count"] == 0
    assert not client._notifications


@pytest.mark.parametrize("scheme_url", [
    "file:///etc/passwd",
    "javascript:alert(1)",
    "data:text/html,hello",
    "ftp://example.com/file",
])
def test_rejects_file_javascript_data_and_unknown_schemes(monkeypatch, scheme_url):
    client = _make_client()
    monkeypatch.setattr(mcs.webbrowser, "open_new_tab", lambda _url: pytest.fail("must not launch"))

    with pytest.raises(ValueError):
        client._do_open_url({"url": scheme_url})


@pytest.mark.parametrize("count", [0, 4, 6, "3", True])
def test_rejects_invalid_window_count(monkeypatch, count):
    client = _make_client()
    monkeypatch.setattr(mcs.webbrowser, "open_new_tab", lambda _url: pytest.fail("must not launch"))

    with pytest.raises(ValueError):
        client._do_open_url({"url": "https://example.com", "count": count})


def test_duplicate_operation_id_launches_once(monkeypatch):
    client = _make_client()
    opened = []
    entered = threading.Event()
    release = threading.Event()
    first_completed = threading.Event()
    duplicate_completed = threading.Event()
    final_ack_count = [0]

    def open_tab(url):
        opened.append(url)
        entered.set()
        release.wait(2)
        return True

    def publish_ack(*args, **kwargs):
        if len(args) > 1 and args[1] in {"ok", "error"}:
            final_ack_count[0] += 1
            if final_ack_count[0] == 1:
                first_completed.set()
            elif final_ack_count[0] == 2:
                duplicate_completed.set()

    client._publish_ack = publish_ack
    monkeypatch.setattr(mcs.webbrowser, "open_new_tab", open_tab)
    request = {
        "type": "open_url",
        "id": "same-mqtt-request-id",
        "url": "https://example.com/once",
        "count": 1,
    }

    client._dispatch(request)
    assert entered.wait(2)
    client._dispatch(dict(request))
    release.set()
    assert first_completed.wait(2)
    client._dispatch(dict(request))
    assert duplicate_completed.wait(2)

    assert opened == ["https://example.com/once"]


def test_duplicate_operation_id_with_different_parameters_is_rejected(monkeypatch):
    client = _make_client()
    opened = []
    first_response = threading.Event()
    monkeypatch.setattr(mcs.webbrowser, "open_new_tab", lambda url: opened.append(url) or True)
    monkeypatch.setattr(
        client, "_publish_response",
        lambda topic, payload: client._responses.append((topic, dict(payload)))
        or first_response.set()
        or True,
    )

    client._dispatch({
        "type": "open_url", "id": "same-id", "url": "https://example.com/one", "count": 1,
    })
    assert first_response.wait(2)
    client._dispatch({
        "type": "open_url", "id": "same-id", "url": "https://example.com/two", "count": 1,
    })

    assert opened == ["https://example.com/one"]
    assert client._responses[-1][1]["detail"] == "Request id was reused with different URL parameters"
    assert client._acks[-1][1]["detail"] == "operation_id_conflict"


def test_pending_operation_cache_fails_closed_without_evicting_pending_ids():
    client = _make_client()
    url = "https://example.com/pending"
    signature = client._open_url_signature({"url": url, "count": 1})
    stale_pending = time.monotonic() - mcs._OPEN_URL_OPERATION_TTL_SEC - 1
    client._open_url_operations.update({
        f"pending-{index}": {
            "signature": signature,
            "updated_at": stale_pending,
            "result": None,
        }
        for index in range(mcs._OPEN_URL_OPERATION_LIMIT)
    })
    threaded = []
    client._run_threaded = lambda *args: threaded.append(args)

    client._dispatch({
        "type": "open_url", "id": "new-id", "url": url, "count": 1,
    })

    assert not threaded
    assert len(client._open_url_operations) == mcs._OPEN_URL_OPERATION_LIMIT
    assert "pending-0" in client._open_url_operations
    assert client._claim_open_url_operation({
        "id": "pending-0", "url": url, "count": 1,
    })[0] == "pending"
    assert client._responses[-1][1]["detail"] == "operation_cache_full"
    assert client._acks[-1][1]["detail"] == "operation_cache_full"


def test_operation_cache_evicts_oldest_completed_before_pending():
    client = _make_client()
    url = "https://example.com/cache"
    signature = client._open_url_signature({"url": url, "count": 1})
    now = time.monotonic()
    client._open_url_operations["completed-oldest"] = {
        "signature": signature,
        "updated_at": now - 1,
        "result": {"type": "open_url", "id": "completed-oldest", "ok": True},
    }
    client._open_url_operations.update({
        f"pending-{index}": {
            "signature": signature,
            "updated_at": now,
            "result": None,
        }
        for index in range(mcs._OPEN_URL_OPERATION_LIMIT - 1)
    })

    assert client._claim_open_url_operation({
        "id": "new-id", "url": url, "count": 1,
    })[0] == "new"

    assert len(client._open_url_operations) == mcs._OPEN_URL_OPERATION_LIMIT
    assert "completed-oldest" not in client._open_url_operations
    assert all(
        client._open_url_operations[f"pending-{index}"]["result"] is None
        for index in range(mcs._OPEN_URL_OPERATION_LIMIT - 1)
    )
