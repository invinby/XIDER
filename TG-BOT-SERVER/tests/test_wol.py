"""Тесты Wake-on-LAN: сборка magic packet."""

import pytest

from wol import build_magic_packet, send_wol


def test_build_mac_with_dashes():
    pkt = build_magic_packet("AA-BB-CC-DD-EE-FF")
    assert pkt[:6] == b"\xff" * 6
    assert pkt[6:] == bytes.fromhex("AABBCCDDEEFF") * 16
    assert len(pkt) == 102


def test_build_mac_with_colons_and_case():
    pkt = build_magic_packet("aa:bb:cc:dd:ee:ff")
    assert pkt[6:] == bytes.fromhex("aabbccddeeff") * 16


def test_build_invalid_mac():
    with pytest.raises(ValueError):
        build_magic_packet("AA-BB-CC")
    with pytest.raises(ValueError):
        build_magic_packet("")


def test_send_wol_uses_socket(monkeypatch):
    import wol

    sent = {}

    class FakeSock:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def setsockopt(self, *a):
            pass

        def settimeout(self, *a):
            pass

        def sendto(self, data, addr):
            sent["data"] = data
            sent["addr"] = addr

    monkeypatch.setattr(wol.socket, "socket", lambda *a, **kw: FakeSock())
    assert send_wol("AA-BB-CC-DD-EE-FF") is True
    assert len(sent["data"]) == 102
    assert sent["addr"][1] == 9
