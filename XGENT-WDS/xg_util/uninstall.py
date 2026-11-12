"""Narrow helpers for removing the current per-user Windows agent install."""

from __future__ import annotations

import ctypes
import os
import shutil
import sys
from pathlib import Path


_AGENT_MARKERS = ("xgent_wds.py", "XGENT-WDS.exe")


def resolve_agent_install_root(module_file: str, executable: str | None = None) -> Path | None:
    """Resolve a dedicated agent directory; refuse source checkouts and broad roots."""
    active = Path(executable or sys.executable).expanduser().resolve()
    module = Path(module_file).expanduser().resolve()
    candidates = [active.parent, module.parent]
    local_app_data = os.environ.get("LOCALAPPDATA")
    for candidate in candidates:
        try:
            candidate = candidate.resolve()
            if candidate == Path(candidate.anchor) or candidate == Path.home().resolve():
                continue
            if any((parent / ".git").exists() for parent in (candidate, *candidate.parents)):
                continue
            if not any((candidate / marker).is_file() for marker in _AGENT_MARKERS):
                continue
            if local_app_data:
                local_root = Path(local_app_data).resolve()
                if candidate == local_root or local_root not in candidate.parents:
                    continue
            elif active.name.lower() != "xgent-wds.exe":
                continue
            return candidate
        except (OSError, RuntimeError, ValueError):
            continue
    return None


def _schedule_delete(path: Path) -> bool:
    if os.name != "nt":
        return False
    try:
        move_file = ctypes.windll.kernel32.MoveFileExW
        move_file.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint)
        move_file.restype = ctypes.c_int
        return bool(move_file(str(path), None, 0x4))  # MOVEFILE_DELAY_UNTIL_REBOOT
    except (AttributeError, OSError, TypeError):
        return False


def remove_agent_install_tree(root: Path | None) -> dict:
    """Remove the validated dedicated agent tree or queue locked paths for reboot."""
    if root is None:
        return {"state": "skipped", "removed": 0, "scheduled": 0, "error": "install root is not safely identified"}
    root = Path(root).resolve()
    if root == Path(root.anchor) or any((parent / ".git").exists() for parent in (root, *root.parents)):
        return {"state": "skipped", "removed": 0, "scheduled": 0, "error": "refusing to remove a broad or source-control directory"}
    if not any((root / marker).is_file() for marker in _AGENT_MARKERS):
        return {"state": "skipped", "removed": 0, "scheduled": 0, "error": "agent markers are missing"}
    if not root.exists():
        return {"state": "removed", "removed": 0, "scheduled": 0, "error": None}
    try:
        shutil.rmtree(root)
        return {"state": "removed", "removed": 1, "scheduled": 0, "error": None}
    except OSError as exc:
        paths = []
        try:
            paths = sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True)
        except OSError:
            pass
        queued = 0
        failed = []
        for path in paths + [root]:
            if not _schedule_delete(path):
                failed.append(str(path))
            else:
                queued += 1
        if queued and not failed:
            return {"state": "scheduled", "removed": 0, "scheduled": queued, "error": str(exc)}
        return {"state": "failed", "removed": 0, "scheduled": queued,
                "error": f"{exc}; deferred deletion failed for {len(failed)} path(s)"}
