"""Canonical bounded execution/result policy for both desktop agents.

Redirect to temporary files instead of pipes: killing a timed-out shell must
not wait for a descendant which still holds an inherited output handle.
"""
import os
import signal
import re
import subprocess
import tempfile


STREAM_BYTES = 8192
# Look beyond the output cap before classifying credential shapes. The token
# suffix minimum is 20 characters; do not let a raw cut hide that evidence.
_CAPTURE_BYTES = STREAM_BYTES + 128 + 1
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
    text = re.sub(r"\b\d{6,}:[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])", "[TOKEN]", text)
    # Unquoted values have no safe space delimiter (Basic auth or passwords
    # with spaces). Run this before introducing our own quoted-value markers:
    # an arbitrary raw [REDACTED] prefix must not gain trusted status.
    text = re.sub(r"(?i)\b(" + _CREDENTIAL_FIELDS + r''')[ \t]*[:=][ \t]*[^"'\s][^\r\n]*''',
                  r"\1=[REDACTED]", text)
    return re.sub(
        r"""(?i)["']?\b(""" + _CREDENTIAL_FIELDS + r""")["']?\s*[:=]\s*(["'])(?:\\(?:.|\Z)|(?!\2)[^\\])*(?:\2|\Z)""",
        r"\1=[REDACTED]", text, flags=re.DOTALL,
    )


def bounded_stream(value) -> tuple[str, bool]:
    if value is None:
        return "", False
    text = value.decode("utf-8", errors="ignore") if isinstance(value, bytes) else str(value)
    raw = redact_stream(text).encode("utf-8", errors="replace")
    return raw[:STREAM_BYTES].decode("utf-8", errors="ignore"), len(raw) > STREAM_BYTES


def _read_stream(handle) -> tuple[str, bool]:
    handle.seek(0)
    prefix = handle.read(_CAPTURE_BYTES)
    text = prefix.decode("utf-8", errors="ignore")
    if len(prefix) == _CAPTURE_BYTES:
        # Lookahead itself is bounded: a token may straddle its new boundary,
        # including the numeric id before its colon. Do not transport a tail
        # that could become a complete credential only beyond the raw cut.
        text = re.sub(r"\b\d+(?::[A-Za-z0-9_-]*)?\Z", "[TOKEN]", text)
    text, redacted_truncated = bounded_stream(text)
    return text, len(prefix) > STREAM_BYTES or redacted_truncated


def _terminate_owned_tree(process):
    if os.name == "nt":
        import psutil
        try:
            root = psutil.Process(process.pid)
            # Snapshot identities before taskkill can reap/reparent the root.
            owned = list(reversed(root.children(recursive=True))) + [root]
        except psutil.Error:
            owned = []
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
        except (OSError, subprocess.SubprocessError):
            pass
        finally:
            # Do not let taskkill's failure bypass cleanup. Process objects
            # retain their identities; kill checks PID reuse, unlike name kills.
            for child in owned:
                try:
                    child.kill()
                except psutil.Error:
                    pass
            psutil.wait_procs(owned, timeout=1)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _run_process(command, *, shell, stdout, stderr, timeout):
    """Bound the shell and its ordinary descendants to one process lifetime."""
    options = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
               else {"start_new_session": True})
    process = subprocess.Popen(command, shell=shell, stdout=stdout, stderr=stderr, **options)
    try:
        process.wait(timeout=timeout)
    except BaseException:
        # Terminate the whole group before reaping the shell. In particular,
        # TimeoutExpired must not leave a child executing an approved command.
        try:
            _terminate_owned_tree(process)
        except Exception:
            # Cleanup errors must not replace the original command timeout.
            pass
        finally:
            try:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=1)
            except Exception:
                pass
        raise
    return process


def execute_shell(command: str, requested_timeout=None) -> dict:
    timeout = shell_timeout(requested_timeout)
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        try:
            result = _run_process(command, shell=True, stdout=stdout_file,
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
