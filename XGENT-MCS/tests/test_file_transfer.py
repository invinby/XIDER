"""File uploads validate payloads and replace destinations atomically."""

import base64

import pytest

import xgent_mcs as mcs


def _client_with_responses(monkeypatch):
    client = object.__new__(mcs.XgentClient)
    responses = []
    monkeypatch.setattr(client, "_publish_response", lambda topic, payload: responses.append((topic, payload)))
    return client, responses


def test_file_put_writes_valid_base64_payload(tmp_path, monkeypatch):
    client, responses = _client_with_responses(monkeypatch)
    target = tmp_path / "Downloads" / "example.bin"

    client._do_file_put({"path": str(target), "name": "example.bin", "b64": base64.b64encode(b"XIDER").decode()})

    assert target.read_bytes() == b"XIDER"
    assert responses[-1][0] == "file_put"
    assert responses[-1][1]["ok"] is True
    assert responses[-1][1]["path"] == str(target)
    assert not list(target.parent.glob(".xider-upload-*"))


def test_file_put_accepts_a_valid_empty_file(tmp_path, monkeypatch):
    client, responses = _client_with_responses(monkeypatch)
    target = tmp_path / "empty.bin"

    client._do_file_put({"path": str(target), "b64": ""})

    assert target.read_bytes() == b""
    assert responses[-1][1]["ok"] is True


@pytest.mark.parametrize("payload", [{}, {"b64": "not-base64!"}])
def test_file_put_rejects_missing_or_corrupt_content_without_touching_destination(tmp_path, monkeypatch, payload):
    client, responses = _client_with_responses(monkeypatch)
    target = tmp_path / "existing.bin"
    target.write_bytes(b"preserve me")
    payload = {"path": str(target), **payload}

    client._do_file_put(payload)

    assert target.read_bytes() == b"preserve me"
    assert responses[-1][1]["ok"] is False
    assert responses[-1][1]["error"]


def test_file_put_enforces_decoded_size_limit(monkeypatch):
    monkeypatch.setattr(mcs, "MAX_FILE_PUT_BYTES", 2)

    with pytest.raises(ValueError, match="30 МиБ"):
        mcs._decode_file_put_payload({"b64": base64.b64encode(b"abc").decode()})


def test_atomic_file_put_preserves_old_destination_if_replace_fails(tmp_path, monkeypatch):
    target = tmp_path / "existing.bin"
    target.write_bytes(b"old")

    def fail_replace(_temporary, _target):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(mcs.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        mcs._write_file_put_atomic(str(target), b"new")

    assert target.read_bytes() == b"old"
    assert not list(tmp_path.glob(".xider-upload-*"))
