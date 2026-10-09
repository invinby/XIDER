"""Canonical bounded execution/result policy for both desktop agents.

Redirect to temporary files instead of pipes: killing a timed-out shell must
not wait for a descendant which still holds an inherited output handle.
"""
import re
import subprocess
import tempfile


STREAM_BYTES = 8192
_CREDENTIAL_FIELDS = (r"(?:bot[_-]?)?token|password|passwd|secret|shared[_-]?key|"
                      r"mqtt[_-]?password|api[_-]?key|ssh[_-]?key|private[_-]?key|authorization")


def shell_timeout(value) -> int:
    return value if type(value) is int and 0 < value < 20 else 20


def redact_stream(text: str) -> str:
    text = re.sub(
        r"-----BEGIN (?:OPENSSH |RSA |EC |DSA |ENCRYPTED )?PRIVATE KEY-----.*?"
        r"(?:-----END (?:OPENSSH |RSA |EC |DSA |ENCRYPTED )?PRIVATE KEY-----|\Z)",
        "[PRIVATE KEY REDACTED]", text, flags=re.DOTALL,
    )
    text = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [REDACTED]", text)
    text = re.sub(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b", "[TOKEN]", text)
    text = re.sub(
        r"""(?i)["']?\b(""" + _CREDENTIAL_FIELDS + r""")["']?\s*[:=]\s*(["'])(?:\\(?:.|\Z)|(?!\2)[^\\])*(?:\2|\Z)""",
        r"\1=[REDACTED]", text, flags=re.DOTALL,
    )
    # Unquoted values have no safe space delimiter (Basic auth or passwords
    # with spaces). Conservatively remove the complete remainder of that line.
    return re.sub(r"(?i)\b(" + _CREDENTIAL_FIELDS + r''')[ \t]*[:=][ \t]*(?!\[REDACTED\])[^"'\s][^\r\n]*''',
                  r"\1=[REDACTED]", text)


def bounded_stream(value) -> tuple[str, bool]:
    if value is None:
        return "", False
    text = value.decode("utf-8", errors="ignore") if isinstance(value, bytes) else str(value)
    raw = redact_stream(text).encode("utf-8", errors="replace")
    return raw[:STREAM_BYTES].decode("utf-8", errors="ignore"), len(raw) > STREAM_BYTES


def _read_stream(handle) -> tuple[str, bool]:
    handle.seek(0)
    prefix = handle.read(STREAM_BYTES + 1)
    text, redacted_truncated = bounded_stream(prefix)
    return text, len(prefix) > STREAM_BYTES or redacted_truncated


def execute_shell(command: str, requested_timeout=None) -> dict:
    timeout = shell_timeout(requested_timeout)
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        try:
            result = subprocess.run(command, shell=True, stdout=stdout_file,
                                    stderr=stderr_file, timeout=timeout)
            code = result.returncode
            status = "completed" if code == 0 else "failed"
        except subprocess.TimeoutExpired:
            code, status = -1, "timeout"
        except Exception:
            # Exception text may contain the command or credentials.
            return {"stdout": "", "stderr": "Execution failed", "output": "Execution failed",
                    "stdout_truncated": False, "stderr_truncated": False,
                    "returncode": -2, "status": "error"}
        stdout, stdout_truncated = _read_stream(stdout_file)
        stderr, stderr_truncated = _read_stream(stderr_file)
    output = stdout + ("\n[STDERR]\n" + stderr if stderr else "")
    if status == "timeout":
        output = f"[TIMEOUT after {timeout}s]\n" + output
    return {"stdout": stdout, "stderr": stderr, "output": output,
            "stdout_truncated": stdout_truncated, "stderr_truncated": stderr_truncated,
            "returncode": code, "status": status}
