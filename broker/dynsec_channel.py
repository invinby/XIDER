"""TLS-only, correlated Mosquitto Dynamic Security control channel."""

from __future__ import annotations

import json
import queue
import secrets
import threading
from collections.abc import Mapping

import paho.mqtt.client as mqtt

from broker.dynsec_issuer import ProvisionError


CONTROL = "$CONTROL/dynamic-security/v1"
RESPONSE = f"{CONTROL}/response"


class DynSecChannel:
    def __init__(self, host: str, port: int, username: str, password: str, timeout: float = 10):
        if not host or not username or not password or not 1 <= port <= 65535:
            raise ValueError("broker admin connection is incomplete")
        self.host, self.port, self.timeout = host, port, timeout
        self._ready = threading.Event()
        self._responses: queue.Queue[dict] = queue.Queue()
        self._lock = threading.Lock()
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"xider-issuer-{secrets.token_hex(8)}",
            protocol=mqtt.MQTTv311,
        )
        self._client.username_pw_set(username, password)
        self._client.tls_set()  # System CA and hostname validation stay enabled.
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_subscribe = self._on_subscribe
        self._client.on_message = self._on_message

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            result, _ = client.subscribe(RESPONSE, qos=1)
            if result != mqtt.MQTT_ERR_SUCCESS:
                self._ready.clear()
        else:
            self._ready.clear()

    def _on_subscribe(self, client, userdata, mid, reason_codes, properties=None):
        if reason_codes and all(getattr(code, "value", code) < 128 for code in reason_codes):
            self._ready.set()

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self._ready.clear()

    def _on_message(self, client, userdata, message):
        if message.topic != RESPONSE or len(message.payload) > 65536:
            return
        try:
            payload = json.loads(message.payload)
        except (UnicodeDecodeError, ValueError):
            return
        if isinstance(payload, dict):
            self._responses.put(payload)

    def __enter__(self):
        try:
            self._client.connect(self.host, self.port, keepalive=30)
            self._client.loop_start()
            if not self._ready.wait(self.timeout):
                raise ProvisionError("broker control subscription not confirmed")
            return self
        except Exception:
            self._client.loop_stop()
            raise

    def __exit__(self, exc_type, exc, tb):
        self._client.disconnect()
        self._client.loop_stop()

    def request(self, command: Mapping) -> Mapping:
        if not self._ready.is_set():
            raise ProvisionError("broker control channel is disconnected")
        with self._lock:
            nonce = secrets.token_hex(16)
            body = {"commands": [{**command, "correlationData": nonce}]}
            info = self._client.publish(CONTROL, json.dumps(body), qos=1)
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                raise ProvisionError("broker control publish failed")
            info.wait_for_publish(timeout=self.timeout)
            if not info.is_published():
                raise ProvisionError("broker control publish timed out")
            # Other administrators may use the same response topic. Only our
            # unpredictable correlation value is accepted as our command result.
            import time

            deadline = time.monotonic() + self.timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProvisionError("broker control response timed out")
                try:
                    payload = self._responses.get(timeout=remaining)
                except queue.Empty as exc:
                    raise ProvisionError("broker control response timed out") from exc
                responses = payload.get("responses")
                if not isinstance(responses, list):
                    continue
                for response in responses:
                    if isinstance(response, dict) and response.get("correlationData") == nonce:
                        return response
