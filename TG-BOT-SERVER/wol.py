"""Wake-on-LAN: сборка и отправка magic packet."""

import re
import socket


def build_magic_packet(mac: str) -> bytes:
    """Собрать magic packet из MAC ('AA-BB-CC-DD-EE-FF', 'aa:bb:...', 'aabbccddeeff')."""
    hex_str = re.sub(r"[^0-9a-fA-F]", "", mac or "")
    if len(hex_str) != 12:
        raise ValueError(f"Некорректный MAC: {mac!r}")
    return bytes.fromhex("FF" * 6 + hex_str * 16)


def send_wol(mac: str, broadcast: str = "255.255.255.255", port: int = 9) -> bool:
    """Отправить magic packet UDP-broadcast'ом. True если отправлено."""
    packet = build_magic_packet(mac)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(2)
        sock.sendto(packet, (broadcast, port))
    return True
