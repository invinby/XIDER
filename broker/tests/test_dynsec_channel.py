import queue
import threading

import pytest

from broker import dynsec_channel
from broker.dynsec_issuer import ProvisionError


class _Publish:
    rc = dynsec_channel.mqtt.MQTT_ERR_SUCCESS

    def wait_for_publish(self, timeout):
        return None

    def is_published(self):
        return True


class _Client:
    def __init__(self, channel):
        self.channel = channel

    def publish(self, topic, body, qos):
        assert topic == dynsec_channel.CONTROL and qos == 1
        import json

        command = json.loads(body)["commands"][0]
        self.channel._responses.put({"responses": [
            {"command": command["command"], "correlationData": "other-admin"},
            {"command": command["command"], "correlationData": command["correlationData"]},
        ]})
        return _Publish()


def test_channel_accepts_only_its_correlated_response():
    channel = dynsec_channel.DynSecChannel.__new__(dynsec_channel.DynSecChannel)
    channel._ready = threading.Event()
    channel._ready.set()
    channel._responses = queue.Queue()
    channel._lock = threading.Lock()
    channel._client = _Client(channel)
    channel.timeout = 0.5
    assert channel.request({"command": "createRole", "rolename": "device"})["command"] == "createRole"


def test_channel_rejects_disconnected_request():
    channel = dynsec_channel.DynSecChannel.__new__(dynsec_channel.DynSecChannel)
    channel._ready = threading.Event()
    with pytest.raises(ProvisionError, match="disconnected"):
        channel.request({"command": "createRole"})


def test_subscribe_ack_accepts_paho_reason_code():
    from paho.mqtt.packettypes import PacketTypes
    from paho.mqtt.reasoncodes import ReasonCode

    channel = dynsec_channel.DynSecChannel.__new__(dynsec_channel.DynSecChannel)
    channel._ready = threading.Event()
    channel._on_subscribe(None, None, 1, [ReasonCode(PacketTypes.SUBACK, identifier=0)])
    assert channel._ready.is_set()


def test_channel_accepts_an_explicit_ca_file_for_loopback_bootstrap(monkeypatch):
    calls = []

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def reconnect_delay_set(self, **kwargs):
            pass

        def username_pw_set(self, *args):
            pass

        def tls_set(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(dynsec_channel.mqtt, "Client", Client)

    dynsec_channel.DynSecChannel(
        "localhost", 18884, "admin", "password", ca_certs="/tmp/xider-local-ca.pem",
    )

    assert calls == [{"ca_certs": "/tmp/xider-local-ca.pem"}]
