import logging

from xg_commands import power


def test_unknown_power_action_keeps_dual_response_contract():
    sent = []
    power.power(
        {"action": "other", "id": "power-unknown"}, device_id="windows-1",
        publish_response=lambda topic, payload: sent.append((topic, payload)),
        logger=logging.getLogger("test.power"), subprocess_module=object(),
        time_module=object(), ctypes_module=object(), run_without_console=object(),
        threading_module=object(),
    )

    assert [topic for topic, _ in sent] == ["power", "output"]
    assert sent[0][1] == sent[1][1]
    assert sent[0][1]["state"] == "unsupported"
    assert sent[0][1]["id"] == sent[0][1]["request_id"] == "power-unknown"
