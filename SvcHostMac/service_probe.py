"""Single-endpoint TCP/TLS availability probe; no port-range scanning."""
from __future__ import annotations

import errno
import socket
import ssl

import tls_certificate

SOURCE = "single_endpoint_tcp_tls"
MAX_TIMEOUT = 3.0


def probe_service(host: str, port: int, protocol: str, *, timeout: float = 2.5,
                  connector=socket.create_connection, context_factory=ssl.create_default_context) -> dict:
    parsed = tls_certificate.parse_target(f"[{host}]:{port}" if isinstance(host, str) and ":" in host else f"{host}:{port}")
    if (parsed is None or parsed["host"] != host or parsed["port"] != port
            or protocol not in {"tcp", "tls"} or type(port) is not int):
        return {"state": "invalid_request", "detail": "invalid_endpoint", "source": SOURCE}
    if type(timeout) not in {int, float} or not 0.25 <= timeout <= MAX_TIMEOUT:
        raise ValueError("timeout must be between 0.25 and 3 seconds")
    raw = secure = None
    try:
        raw = connector((host, port), timeout=float(timeout))
        if protocol == "tcp":
            return {"state": "reachable", "detail": None, "source": SOURCE}
        secure = context_factory().wrap_socket(raw, server_hostname=host)
        return {"state": "reachable", "detail": None, "source": SOURCE}
    except ssl.SSLCertVerificationError:
        return {"state": "reachable_untrusted", "detail": "tls_certificate_untrusted", "source": SOURCE}
    except (TimeoutError, socket.timeout):
        return {"state": "unknown", "detail": "probe_timeout", "source": SOURCE}
    except ConnectionRefusedError:
        return {"state": "unreachable", "detail": "connection_refused", "source": SOURCE}
    except ssl.SSLError:
        return {"state": "unknown", "detail": "tls_handshake_failed", "source": SOURCE}
    except OSError as exc:
        if exc.errno == errno.ECONNREFUSED:
            return {"state": "unreachable", "detail": "connection_refused", "source": SOURCE}
        return {"state": "unknown", "detail": "probe_failed", "source": SOURCE}
    finally:
        closer = secure if secure is not None else raw
        if closer is not None:
            try:
                closer.close()
            except OSError:
                pass
