from types import SimpleNamespace

from xg_commands import network


def test_wifi_profiles_caps_results_and_preserves_per_profile_failure_handling():
    calls, sent = [], []
    names = [f"network-{index}" for index in range(17)]

    def run(check_output, command, **kwargs):
        calls.append((check_output, command, kwargs))
        if len(command) == 4:
            return "profile list"
        if command[4] == "name=network-3":
            raise RuntimeError("profile unavailable")
        return f"key={command[4].removeprefix('name=')}"

    network.wifi_passwords(
        {}, device_id="d", publish_response=lambda *args: sent.append(args),
        run_without_console=run, check_output="check_output", devnull="DEVNULL",
        parse_wifi_profile_names=lambda _output: names,
        parse_key_content=lambda output: output.removeprefix("key="),
    )
    assert len(calls) == 16
    assert calls[0][1] == ["netsh", "wlan", "show", "profiles"]
    assert calls[0][2] == {
        "text": True, "stderr": "DEVNULL", "timeout": 10, "encoding": "utf-8", "errors": "ignore",
    }
    assert "network-0: network-0" in sent[0][1]["text"]
    assert "network-3: (недоступен)" in sent[0][1]["text"]
    assert "и ещё 2" in sent[0][1]["text"]
    assert sent[0][1]["ok"] is True


def test_bluetooth_inventory_limits_items_and_preserves_empty_state():
    sent = []
    network.bluetooth_list(
        {}, device_id="d", publish_response=lambda *args: sent.append(args),
        run_without_console=lambda _check, _command, **_kwargs: "\n".join(f"Device {i}" for i in range(22)),
        check_output="check_output", devnull="DEVNULL",
    )
    assert len(sent[0][1]["items"]) == 20
    assert sent[0][1]["items"][0] == "Device 0"
    assert "Device 21" in sent[0][1]["text"]

    sent.clear()
    network.bluetooth_list(
        {}, device_id="d", publish_response=lambda *args: sent.append(args),
        run_without_console=lambda *_args, **_kwargs: " \n",
        check_output="check_output", devnull="DEVNULL",
    )
    assert sent[0][1]["items"] == []
    assert "(нет активных)" in sent[0][1]["text"]
