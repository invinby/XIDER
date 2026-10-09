"""Opt-in frozen packaging probe; exits before agent imports or device services.

Run the built executable with --command-approval-bundle-smoke REPORT.json.
Normal launches only inspect argv. Keys and replay state are synthetic and
temporary; no command is executed or transported.
"""
import sys


def _probe():
    import base64
    import hashlib
    import importlib.util
    import json
    import os
    from pathlib import Path
    import tempfile
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization

    def require(value, message):
        if not value:
            raise RuntimeError(message)

    require(getattr(sys, "frozen", False), "Probe requires a frozen executable")
    runtime = sys.modules["pyimod02_importers"]
    archive = runtime.pyz_archive
    root = Path(sys._MEIPASS)
    finder = runtime.PyiFrozenFinder(str(root))
    spec = finder.find_spec("command_module_loader")
    loader = getattr(spec, "loader", None)
    require(type(loader) is runtime.PyiFrozenLoader
            and loader._pyz_archive is archive
            and loader._pyz_entry_name == "command_module_loader"
            and loader._is_package is False
            and "command_module_loader" in archive.toc
            and Path(spec.origin) == root / "command_module_loader.py",
            "Bootstrap loader is not bundled in the active PYZ")
    shared = importlib.util.module_from_spec(spec)
    loader.exec_module(shared)
    signing = shared.load_component("command_signing", source_root=root)
    replay = shared.load_component("command_replay", source_root=root)

    # Characterize PyInstaller's real filesystem fallback: it must be rejected
    # even when a valid source module exists and the helper is already cached.
    shadow = root / "command_signing.py"
    require(not shadow.exists(), "Unexpected external verifier source")
    entry = archive.toc.pop("command_signing")
    try:
        shadow.write_text("raise RuntimeError('filesystem shadow executed')\n", encoding="utf-8")
        try:
            shared.load_component("command_signing", source_root=root)
        except ImportError:
            pass
        else:
            raise RuntimeError("Filesystem fallback accepted")
    finally:
        archive.toc["command_signing"] = entry
        shadow.unlink(missing_ok=True)

    def b64(value):
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")
    def canonical(value):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    key_id = hashlib.sha256(public).hexdigest()[:16]
    os.environ["XIDER_COMMAND_VERIFY_KEYSET_B64"] = b64(canonical({key_id: b64(public)}))
    command = "bundle probe - never executed"
    payload = {"key_id": key_id, "request_id": "bundle_probe", "requester_id": 1,
               "approver_id": 2, "device_id": "bundle-probe-device",
               "command_hash": hashlib.sha256(command.encode()).hexdigest(),
               "issued_at": 100, "expires_at": 220, "nonce": b64(os.urandom(32))}
    envelope = {**payload, "signature": b64(key.sign(canonical(payload)))}
    require(signing.verify_approved_command(envelope, command=command,
            expected_device_id="bundle-probe-device", now=101) == payload, "Valid approval rejected")
    require(signing.verify_approved_command(envelope, command=command + "altered",
            expected_device_id="bundle-probe-device", now=101) is None, "Tampered command accepted")
    with tempfile.TemporaryDirectory(prefix="xider-bundle-probe-") as temporary:
        path = Path(temporary) / "replay.json"
        require(replay.CommandReplayLedger(path).reserve("bundle_probe", 220, now=101), "Reservation failed")
        require(not replay.CommandReplayLedger(path).reserve("bundle_probe", 220, now=101), "Replay accepted")
    return {"ok": True, "frozen": True, "modules": ["command_module_loader", "command_signing", "command_replay"],
            "filesystem_fallback_rejected": True, "valid_signature": True,
            "tamper_rejected": True, "durable_replay_rejected": True}


if len(sys.argv) > 1 and sys.argv[1] == "--command-approval-bundle-smoke":
    import json
    from pathlib import Path
    if len(sys.argv) != 3:
        raise SystemExit(2)
    try:
        result = _probe()
    except Exception as exc:
        result = {"ok": False, "error": type(exc).__name__ + ": " + str(exc)}
    Path(sys.argv[2]).write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
    raise SystemExit(0 if result["ok"] else 1)
