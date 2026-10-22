import shell_late_result_inbox


def test_refresh_keeps_only_bounded_metadata_for_fresh_late_timeouts():
    inbox = shell_late_result_inbox.ShellLateResultInbox()
    candidates = [
        {"device_id": "device-1", "request_id": "request-1", "timed_out_at": 995,
         "status": "result_timeout", "stdout": "must never be retained"},
        {"device_id": "device-1", "request_id": "request-1", "timed_out_at": 995,
         "status": "result_timeout"},
        {"device_id": "device-1", "request_id": "old-request", "timed_out_at": 950,
         "status": "result_timeout"},
        {"device_id": "device-1", "request_id": "completed-request", "timed_out_at": 995,
         "status": "completed"},
        {"device_id": "device-2", "request_id": "request-2", "timed_out_at": 995,
         "status": "result_timeout"},
    ]

    rows = inbox.refresh(20, 20, candidates, now=1_000, device_id="device-1")

    assert len(rows) == 1
    row = rows[0]
    assert row["device_id"] == "device-1"
    assert row["request_id"] == "request-1"
    assert row["timed_out_at"] == 995
    assert row["expires_at"] == 1_025
    assert set(row) == {"token", "device_id", "request_id", "timed_out_at", "expires_at"}
    assert "must never be retained" not in repr(inbox._items)


def test_consume_is_owner_bound_expiry_bound_and_single_use():
    inbox = shell_late_result_inbox.ShellLateResultInbox()
    rows = inbox.refresh(20, 20, [
        {"device_id": "device-1", "request_id": "request-1", "timed_out_at": 995,
         "status": "result_timeout"},
    ], now=1_000, device_id="device-1")
    token = rows[0]["token"]

    assert inbox.consume(21, 20, token, now=1_001) is None
    assert inbox.consume(20, 20, token, now=1_002) == {
        "device_id": "device-1", "request_id": "request-1", "timed_out_at": 995,
    }
    assert inbox.consume(20, 20, token, now=1_003) is None

    expired_inbox = shell_late_result_inbox.ShellLateResultInbox()
    expired = expired_inbox.refresh(20, 20, [
        {"device_id": "device-1", "request_id": "request-2", "timed_out_at": 995,
         "status": "result_timeout"},
    ], now=1_000, device_id="device-1")[0]
    assert expired_inbox.consume(20, 20, expired["token"], now=1_026) is None


def test_non_owner_or_invalid_candidates_cannot_create_inbox_entries():
    inbox = shell_late_result_inbox.ShellLateResultInbox()
    candidate = {"device_id": "device-1", "request_id": "request-1", "timed_out_at": 995,
                 "status": "result_timeout"}

    assert inbox.refresh(21, 20, [candidate], now=1_000) == []
    assert inbox.refresh(20, 20, [{**candidate, "request_id": "bad id"}], now=1_000) == []
    assert inbox.refresh(20, 20, [{**candidate, "device_id": "device-2"}], now=1_000,
                         device_id="device-1") == []
