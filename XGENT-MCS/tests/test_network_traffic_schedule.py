import pytest

from network_traffic_schedule import load_schedule, save_schedule


def test_network_traffic_schedule_is_explicit_bounded_and_persisted(tmp_path):
    path = tmp_path / "network-traffic-schedule.json"
    assert load_schedule(path) == {
        "interval_seconds": 0, "schedule_id": None, "interface_id": None,
        "threshold_bytes": None, "next_sample_at": None,
    }

    saved = save_schedule(
        path, 900, "a" * 32, "b" * 20, 100_000_000, now=1_700_000_000,
    )
    assert saved == {
        "interval_seconds": 900, "schedule_id": "a" * 32,
        "interface_id": "b" * 20, "threshold_bytes": 100_000_000,
        "next_sample_at": 1_700_000_900,
    }
    assert load_schedule(path) == saved

    immediate = save_schedule(
        path, 900, "d" * 32, "b" * 20, 100_000_000, now=1_700_000_000, first_sample=True,
    )
    assert immediate["next_sample_at"] == 1_700_000_000

    disabled = save_schedule(path, 0, "c" * 32, "b" * 20, 100_000_000, now=1_700_000_000)
    assert disabled["interval_seconds"] == 0
    assert disabled["next_sample_at"] is None


@pytest.mark.parametrize("interval,interface_id,threshold", [
    (60, "b" * 20, 100_000_000),
    (900, "not-an-id", 100_000_000),
    (900, "b" * 20, 1),
])
def test_network_traffic_schedule_rejects_unsupported_or_unbounded_values(
    tmp_path, interval, interface_id, threshold,
):
    with pytest.raises(ValueError):
        save_schedule(tmp_path / "schedule.json", interval, "a" * 32,
                      interface_id, threshold, now=1_700_000_000)
