"""Explicit local filesystem action command handlers."""

from __future__ import annotations


def agent_files(
    payload: dict, *, device_id, agent_version, collect_inventory,
    schema, publish_response, logger,
) -> None:
    """Publish allowlisted component metadata without exposing file contents."""
    try:
        report = collect_inventory(agent_version=agent_version)
        result = {
            "type": "agent_files", "device_id": device_id,
            "id": payload.get("id"), "request_id": payload.get("id"),
            "ok": report.get("state") in {"available", "partial"},
            **report,
        }
    except Exception:
        logger.exception("Agent-file inventory collection failed")
        result = {
            "type": "agent_files", "device_id": device_id, "ok": False,
            "id": payload.get("id"), "request_id": payload.get("id"),
            "schema": schema, "state": "error",
            "components": [], "protection": {
                "obfuscation_state": "unknown", "integrity_state": "unverified",
            },
        }
    publish_response("output", result)


def agent_file_get(
    payload: dict, *, device_id, read_allowlisted_text_file,
    response_schema, fullmatch, publish_response,
) -> None:
    """Publish one bounded allowlisted text-file result with a safe request ID."""
    raw_request_id = payload.get("id")
    request_id = (
        raw_request_id
        if isinstance(raw_request_id, str)
        and fullmatch(r"[A-Za-z0-9_-]{1,128}", raw_request_id)
        else None
    )
    requested_file_id = payload.get("file_id")
    result = read_allowlisted_text_file(requested_file_id)
    response = {
        "type": "agent_file_get",
        "schema": response_schema,
        "device_id": device_id,
        "id": request_id,
        "request_id": request_id,
        "file_id": result.get("file_id"),
        "state": result.get("state", "unavailable"),
        "content": result.get("content", ""),
        "truncated": bool(result.get("truncated", False)),
    }
    publish_response("output", response)


def file_get(
    payload: dict, *, device_id, publish_response, getsize, open_file,
    basename, base64_module, logger,
) -> None:
    path = (payload.get("path") or "").strip()
    if not path:
        return
    try:
        max_bytes = 45_000_000
        size = getsize(path)
        if size > max_bytes:
            publish_response("file_get", {
                "type": "file_get", "device_id": device_id,
                "ok": False, "error": f"файл {size} байт > лимит",
            })
            return
        with open_file(path, "rb") as fh:
            b64 = base64_module.b64encode(fh.read()).decode("ascii")
        publish_response("file_get", {
            "type": "file_get", "device_id": device_id,
            "ok": True, "path": path, "b64": b64,
            "name": basename(path),
            # Canonical schema; aliases keep older bots compatible.
            "data": b64, "filename": basename(path),
        })
        logger.info("file_get: %s (%d байт)", path, size)
    except OSError as exc:
        publish_response("file_get", {
            "type": "file_get", "device_id": device_id,
            "ok": False, "error": str(exc),
        })


def file_put(
    payload: dict, *, device_id, decode_payload, write_atomic, basename,
    expanduser, expandvars, join, publish_response, logger,
) -> None:
    try:
        content = decode_payload(payload)
        name = basename(payload.get("name") or "file.bin")
        raw_path = (payload.get("path") or "").strip()
        if raw_path:
            target = expanduser(expandvars(raw_path))
        else:
            target_dir = (payload.get("dir") or "~").strip()
            base = expanduser(expandvars(target_dir))
            target = join(base, name)
        write_atomic(target, content)
        publish_response("file_put", {
            "type": "file_put", "device_id": device_id,
            "ok": True, "path": target,
        })
        logger.info("file_put: %s", target)
    except (OSError, TypeError, ValueError) as exc:
        publish_response("file_put", {
            "type": "file_put", "device_id": device_id,
            "ok": False, "error": str(exc),
        })


def file_del(
    payload: dict, *, device_id, publish_response, expanduser, expandvars, remove,
) -> None:
    raw_path = payload.get("path")
    path = raw_path.strip() if isinstance(raw_path, str) else ""
    if not path:
        publish_response("file_del", {
            "type": "file_del", "device_id": device_id, "ok": False,
            "state": "failed", "error": "invalid_or_missing_path",
        })
        return
    try:
        remove(expanduser(expandvars(path)))
        ok, err = True, None
    except OSError as exc:
        ok, err = False, str(exc)
    publish_response("file_del", {
        "type": "file_del", "device_id": device_id,
        "ok": ok, "path": path, "error": err,
    })


def find_file(payload: dict, *, device_id, find_matches, publish_response) -> None:
    pattern = (payload.get("pattern") or "").strip()
    root = (payload.get("root") or "~").strip()
    result = find_matches(pattern, root) if pattern else {"ok": False, "matches": [], "total": 0}
    publish_response("find_file", {"type": "find_file", "device_id": device_id, **result})


def path_open(payload: dict, *, publish_response, device_id, expanduser, startfile) -> None:
    raw_path = payload.get("path")
    path = raw_path.strip() if isinstance(raw_path, str) else ""
    if not path:
        publish_response("path_open", {
            "type": "path_open", "device_id": device_id, "ok": False,
            "state": "failed", "error": "invalid_or_missing_path",
        })
        return
    try:
        startfile(expanduser(path))
        ok, error = True, None
    except (OSError, AttributeError) as exc:
        ok, error = False, str(exc)
    publish_response("path_open", {
        "type": "path_open", "device_id": device_id,
        "ok": ok, "path": path, "error": error,
    })
