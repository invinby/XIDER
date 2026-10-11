"""Actual inbound MQTT callback rejects HMAC-only shell before execution.

Each component runs in its own local interpreter/config directory. Mutation
mode is test-only evidence that removing the approval gate trips the sentries.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]

CALLBACK_FIXTURE = r'''
import base64
import hashlib
import importlib
import json
import logging
import logging.handlers
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import dotenv

fixture_root = Path(sys.argv[2])
Path.home = classmethod(lambda cls: fixture_root)
dotenv.load_dotenv = lambda *args, **kwargs: False
logging.handlers.RotatingFileHandler = lambda *args, **kwargs: logging.NullHandler()

def no_network(event, args):
    if event in {"socket.connect", "socket.bind", "socket.getaddrinfo", "socket.__new__"}:
        raise AssertionError("fixture forbids socket/network access")
sys.addaudithook(no_network)

agent = importlib.import_module(sys.argv[1])
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
import os
server_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
public_key = server_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
key_id = hashlib.sha256(public_key).hexdigest()[:16]
def b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")
os.environ["XIDER_COMMAND_VERIFY_KEYSET_B64"] = b64(json.dumps({key_id: b64(public_key)}).encode())

client = object.__new__(agent.XgentClient)
client._standby = False
client._command_context = threading.local()
replay = agent._load_command_approval_component("command_replay")
client._command_replay_ledger = replay.CommandReplayLedger(fixture_root / "command-replay.json")
responses, acknowledgements, accepted_hmac, shell_entries, executor_calls, subprocess_calls = [], [], [], [], [], []
finished = threading.Event()
def response(topic, payload):
    responses.append((topic, payload))
def ack(action, status, **kwargs):
    acknowledgements.append((action, status))
    if status != "received":
        finished.set()
client._publish_response = response
client._publish_ack = ack

real_shell = client._do_shell
def shell_entry(payload):
    shell_entries.append(payload["id"])
    return real_shell(payload)
client._handlers = {"shell": shell_entry}

real_verify = agent.verify_message
def verify(envelope):
    payload = real_verify(envelope)
    if payload is not None:
        accepted_hmac.append(payload["id"])
    return payload
agent.verify_message = verify

def execution_sentry(*args, **kwargs):
    executor_calls.append(True)
    return {"ok": False, "status": "error", "returncode": -2}
real_loader = agent._load_command_approval_component
def load(name):
    if name == "shell_execution":
        return SimpleNamespace(execute_shell=execution_sentry)
    return real_loader(name)
agent._load_command_approval_component = load
def subprocess_sentry(*args, **kwargs):
    subprocess_calls.append(True)
    raise AssertionError("fixture forbids subprocess execution")
subprocess.run = subprocess_sentry
subprocess.Popen = subprocess_sentry

if sys.argv[3] == "mutate":
    agent._reserve_shell_approval = lambda *args: True

command = "echo fixture-must-never-execute"
now = int(time.time())
invalid_approval = {"key_id": key_id, "request_id": "inbound_approval_123", "requester_id": 101,
    "approver_id": 202, "device_id": agent.DEVICE_ID,
    "command_hash": hashlib.sha256(command.encode()).hexdigest(), "issued_at": now,
    "expires_at": now + 120, "nonce": b64(bytes(range(32))), "signature": "A" * 86}

for index, approval in enumerate((None, invalid_approval)):
    finished.clear()
    payload = {"type": "shell", "command": command, "id": "inbound-shell-" + str(index)}
    if approval is not None:
        payload["approval"] = approval
    envelope = agent.sign_message(payload)
    message = SimpleNamespace(topic=agent.MQTT_PREFIX + "/cmd/" + agent.DEVICE_ID,
                              payload=json.dumps(envelope).encode("utf-8"))
    # Real callback, HMAC verifier, dispatcher, thread worker and shell gate.
    client._on_message(None, None, message)
    assert finished.wait(3), "actual callback worker did not finish"

assert accepted_hmac == ["inbound-shell-0", "inbound-shell-1"], "fixture must pass real HMAC verification"
assert shell_entries == accepted_hmac, "actual dispatcher did not invoke real shell handler"
assert not executor_calls, "HMAC-only callback reached shell executor"
assert not subprocess_calls, "HMAC-only callback reached subprocess"
assert len(responses) == 2, "shell must publish explicit rejection"
assert all(topic == "shell" and row.get("ok") is False and row.get("status") == "rejected"
           and row.get("error") == "approval_invalid_or_replayed" for topic, row in responses)
assert not (fixture_root / "command-replay.json").exists(), "invalid approval must not reserve replay state"
print(json.dumps({"hmac_accepted": len(accepted_hmac), "shell_entries": len(shell_entries),
    "rejected": len(responses), "executor_calls": len(executor_calls), "subprocess_calls": len(subprocess_calls)}))
'''


@pytest.mark.parametrize("component,module", [("XGENT-WDS", "xgent_wds"), ("XGENT-MCS", "xgent_mcs")])
def test_actual_mqtt_callback_rejects_hmac_only_shell_before_executor(tmp_path, component, module):
    env = os.environ.copy()
    env.update(SHARED_KEY="xider-test-only-shared-key-0123456789", MQTT_BROKER="localhost",
               MQTT_PORT="8883", MQTT_PREFIX="xgent/offline", MQTT_TLS="true",
               MQTT_USERNAME="test-user", MQTT_PASSWORD="test-password-not-real", ENCRYPT_PAYLOAD="false")
    mode = "mutate" if env.get("XIDER_TEST_MUTATE_SHELL_GATE") == "1" else "normal"
    result = subprocess.run([sys.executable, "-c", CALLBACK_FIXTURE, module, str(tmp_path), mode],
        cwd=ROOT / component, env=env, capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode == 0, f"{component}: {result.stderr}"
    assert json.loads(result.stdout) == {"hmac_accepted": 2, "shell_entries": 2,
        "rejected": 2, "executor_calls": 0, "subprocess_calls": 0}
