"""Read-only local filesystem inventory helpers."""

from __future__ import annotations


def list_directory(path: str, *, os_module) -> dict:
    """Return a bounded directory listing using the existing agent response shape."""
    entries = []
    base = os_module.path.expandvars(os_module.path.expanduser(path or "."))
    try:
        for entry in sorted(os_module.scandir(base), key=lambda e: (not e.is_dir(), e.name.lower())):
            item = {"name": entry.name, "is_dir": entry.is_dir()}
            if not item["is_dir"]:
                try:
                    item["size"] = entry.stat().st_size
                except OSError:
                    item["size"] = 0
            entries.append(item)
        return {"ok": True, "path": base, "files": entries[:100], "total": len(entries)}
    except OSError as exc:
        return {"ok": False, "path": base, "error": str(exc), "files": [], "total": 0}


def find_matches(pattern: str, root: str, limit: int = 30, max_depth: int = 4, *, os_module) -> dict:
    """Search file names by substring with the agent's existing limits and response shape."""
    matches = []
    for current, dirs, files in os_module.walk(os_module.path.expanduser(root or "~")):
        depth = current[len(root):].count(os_module.sep)
        if depth >= max_depth:
            dirs[:] = []
            continue
        for name in files:
            if pattern.lower() in name.lower():
                matches.append(os_module.path.join(current, name))
                if len(matches) >= limit:
                    return {"ok": True, "matches": matches, "total": len(matches)}
    return {"ok": True, "matches": matches, "total": len(matches)}
