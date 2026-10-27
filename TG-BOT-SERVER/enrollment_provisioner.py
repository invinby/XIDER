"""Complete an owner-approved X-DOCK request with isolated credentials."""

from __future__ import annotations

import secrets
from typing import Protocol

from device_keys import remove_device_key_if_matches, save_device_key
from enrollment_store import EnrollmentStore, seal_response


class BrokerIssuer(Protocol):
    def issue(self, device_id: str, issuance_id: str): ...
    def revoke(self, device_id: str, issuance_id: str) -> None: ...


class EnrollmentProvisioner:
    def __init__(
        self, store: EnrollmentStore, issuer: BrokerIssuer, *, broker_host: str,
        broker_port: int, broker_prefix: str, command_verify_keyset: str = "",
        broker_transport: str = "tcp", broker_ws_path: str = "/mqtt",
    ):
        if not broker_host or any(char in broker_host for char in " /\\\r\n"):
            raise ValueError("invalid broker host")
        if type(broker_port) is not int or not 1 <= broker_port <= 65535:
            raise ValueError("invalid broker port")
        self.store = store
        self.issuer = issuer
        self.host = broker_host
        self.port = broker_port
        self.prefix = broker_prefix
        if broker_transport not in {"tcp", "websockets"}:
            raise ValueError("invalid broker transport")
        if not broker_ws_path.startswith("/") or any(char in broker_ws_path for char in "\r\n\x00"):
            raise ValueError("invalid broker websocket path")
        self.transport = broker_transport
        self.ws_path = broker_ws_path
        self.command_verify_keyset = command_verify_keyset

    def fulfill(self, request_id: str) -> bool:
        approved = self.store.claim_approved(request_id)
        if approved is None:
            return False
        device_id = approved["device_id"]
        secret = secrets.token_urlsafe(48)
        credential = None
        saved = False
        try:
            # Per-request broker names make cleanup safe even if the control
            # response is lost after Mosquitto has applied createClient.
            credential = self.issuer.issue(device_id, request_id)
            save_device_key(device_id, secret)
            saved = True
            settings = {
                "SHARED_KEY": secret,
                "MQTT_BROKER": self.host,
                "MQTT_PORT": str(self.port),
                "MQTT_PREFIX": self.prefix,
                "MQTT_TLS": "true",
                "MQTT_TRANSPORT": self.transport,
                "MQTT_WS_PATH": self.ws_path,
                "MQTT_USERNAME": credential.username,
                "MQTT_PASSWORD": credential.password,
                "ENCRYPT_PAYLOAD": "true",
            }
            if self.command_verify_keyset:
                settings["XIDER_COMMAND_VERIFY_KEYSET_B64"] = self.command_verify_keyset
            sealed = seal_response(request_id, approved["fingerprint"], approved["encryption_key"], settings)
            if not self.store.set_sealed_reply(request_id, sealed):
                raise RuntimeError("approved enrollment expired before credential delivery")
        except Exception:
            # Keep both broker and bot unusable if either step fails. The
            # original exception is preserved for server-side diagnostics.
            try:
                if credential is not None:
                    self.issuer.revoke(device_id, request_id)
            finally:
                if saved:
                    remove_device_key_if_matches(device_id, secret)
                self.store.release_claim(request_id)
            raise
        return True
