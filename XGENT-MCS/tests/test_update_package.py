import hashlib
import json
from pathlib import Path
import sys
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import update_package as package
import release_signature as signature

_TEST_PRIVATE_KEY = Ed25519PrivateKey.generate()
_TEST_PRIVATE_PEM = _TEST_PRIVATE_KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
)
_TEST_PUBLIC_KEY = _TEST_PRIVATE_KEY.public_key().public_bytes(
    serialization.Encoding.Raw,
    serialization.PublicFormat.Raw,
)
signature.TRUSTED_RELEASE_KEYS[signature.release_key_id(_TEST_PUBLIC_KEY)] = _TEST_PUBLIC_KEY


def test_release_key_readiness_requires_a_matching_pinned_ed25519_key(monkeypatch):
    monkeypatch.setattr(signature, "TRUSTED_RELEASE_KEYS", {})
    assert not signature.trusted_release_keys_ready()
    monkeypatch.setattr(signature, "TRUSTED_RELEASE_KEYS", {"wrong-id": _TEST_PUBLIC_KEY})
    assert not signature.trusted_release_keys_ready()
    key_id = signature.release_key_id(_TEST_PUBLIC_KEY)
    monkeypatch.setattr(signature, "TRUSTED_RELEASE_KEYS", {key_id: _TEST_PUBLIC_KEY})
    assert signature.trusted_release_keys_ready()


def _zip(path: Path, entries: list[tuple[str, bytes, int | None]]) -> None:
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for name, body, mode in entries:
            info = ZipInfo(name)
            if mode is not None:
                info.external_attr = mode << 16
            archive.writestr(info, body)


class _Response:
    def __init__(self, body: bytes, final_url: str, declared_length: str | None = None):
        self.body = body
        self.offset = 0
        self.headers = {"Content-Length": declared_length or str(len(body))}
        self.final_url = final_url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self):
        return self.final_url

    def read(self, size):
        chunk = self.body[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk


def _release_routes(
    tag="v4.0.1", *, digest=None, assets=None, manifest=None,
    archive=b"source zip", signed=True,
):
    digest = digest or hashlib.sha256(archive).hexdigest()
    assets = assets or [
        {"name": package.MANIFEST_ASSET},
        {"name": package.SOURCE_ASSET},
    ]
    manifest = manifest or {
        "schema": 1,
        "release": tag,
        "assets": [{
            "name": package.SOURCE_ASSET,
            "component": "source",
            "platform": "all",
            "size": len(archive),
            "sha256": digest,
        }],
    }
    if signed and "signature" not in manifest:
        manifest = signature.sign_manifest(manifest, _TEST_PRIVATE_PEM)
    return {
        f"{package.RELEASE_API}/latest": json.dumps({
            "tag_name": tag, "draft": False, "prerelease": False, "assets": assets,
        }).encode(),
        package._release_asset_url(tag, package.MANIFEST_ASSET): json.dumps(manifest).encode(),
        package._release_asset_url(tag, package.SOURCE_ASSET): archive,
    }


def _use_routes(monkeypatch, routes, *, bad_redirect=None):
    observed = []

    def fake_urlopen(request, timeout):
        url = request.full_url
        observed.append((url, timeout))
        body = routes[url]
        final_url = bad_redirect if bad_redirect and url.endswith(package.MANIFEST_ASSET) else url
        return _Response(body, final_url)

    monkeypatch.setattr(package.urllib.request, "urlopen", fake_urlopen)
    return observed


def test_source_update_uses_stable_release_and_checks_manifest_hash(tmp_path, monkeypatch):
    routes = _release_routes()
    observed = _use_routes(monkeypatch, routes)
    manifest_path = tmp_path / "release-manifest.json"
    archive_path = tmp_path / "XIDER-source.zip"

    assert package.download_verified_source_archive(manifest_path, archive_path) == "v4.0.1"
    assert archive_path.read_bytes() == b"source zip"
    assert observed[0][0] == f"{package.RELEASE_API}/latest"
    assert observed[1][0] == package._release_asset_url("v4.0.1", package.MANIFEST_ASSET)
    assert observed[2][0] == package._release_asset_url("v4.0.1", package.SOURCE_ASSET)
    assert all(timeout == package.DOWNLOAD_TIMEOUT_SECONDS for _, timeout in observed)


def test_source_update_accepts_additional_signed_bootstrap_assets(tmp_path, monkeypatch):
    tag = "v4.0.1"
    routes = _release_routes(tag)
    manifest_url = package._release_asset_url(tag, package.MANIFEST_ASSET)
    manifest = json.loads(routes[manifest_url])
    manifest.pop("signature", None)
    for name, component, platform in (
        ("XIDER-bootstrap-windows.ps1", "bootstrap", "windows"),
        ("XIDER-bootstrap-macos.sh", "bootstrap", "macos"),
        ("XIDER-QUICKSTART.txt", "quickstart", "all"),
    ):
        manifest["assets"].append({
            "name": name,
            "component": component,
            "platform": platform,
            "size": 1,
            "sha256": "0" * 64,
        })
    routes[manifest_url] = json.dumps(signature.sign_manifest(manifest, _TEST_PRIVATE_PEM)).encode()
    _use_routes(monkeypatch, routes)

    manifest_path = tmp_path / "release-manifest.json"
    archive_path = tmp_path / "XIDER-source.zip"
    assert package.download_verified_source_archive(manifest_path, archive_path) == tag
    assert archive_path.read_bytes() == b"source zip"


def test_source_update_does_not_delete_a_preexisting_archive_destination(tmp_path, monkeypatch):
    _use_routes(monkeypatch, _release_routes())
    archive_path = tmp_path / "source.zip"
    archive_path.write_bytes(b"owner data")

    with pytest.raises(FileExistsError):
        package.download_verified_source_archive(tmp_path / "manifest.json", archive_path)

    assert archive_path.read_bytes() == b"owner data"


def test_source_update_can_pin_a_semver_release_tag(tmp_path, monkeypatch):
    tag = "v4.2.0"
    routes = _release_routes(tag)
    api_doc = json.loads(routes.pop(f"{package.RELEASE_API}/latest"))
    routes[f"{package.RELEASE_API}/tags/{tag}"] = json.dumps(api_doc).encode()
    observed = _use_routes(monkeypatch, routes)

    assert package.download_verified_source_archive(
        tmp_path / "manifest.json", tmp_path / "source.zip", release_tag=tag,
    ) == tag
    assert observed[0][0] == f"{package.RELEASE_API}/tags/{tag}"


@pytest.mark.parametrize("tag", ["main", "../v4.0.1", "v4.0.1/extra", "v4.0.1?x=1"])
def test_source_update_rejects_non_release_refs_before_network(tmp_path, monkeypatch, tag):
    monkeypatch.setattr(
        package.urllib.request, "urlopen",
        lambda *_a, **_k: pytest.fail("invalid release ref must not access network"),
    )
    with pytest.raises(ValueError):
        package.download_verified_source_archive(
            tmp_path / "manifest.json", tmp_path / "source.zip", release_tag=tag,
        )


def test_source_update_rejects_missing_assets_and_manifest_tag_mismatch(tmp_path, monkeypatch):
    routes = _release_routes(assets=[{"name": package.MANIFEST_ASSET}])
    _use_routes(monkeypatch, routes)
    with pytest.raises(ValueError, match="пары source ZIP"):
        package.download_verified_source_archive(tmp_path / "manifest.json", tmp_path / "source.zip")

    tag = "v4.0.1"
    routes = _release_routes(tag, manifest={"schema": 1, "release": "v4.0.0", "assets": []})
    _use_routes(monkeypatch, routes)
    with pytest.raises(ValueError, match="не соответствует"):
        package.download_verified_source_archive(tmp_path / "manifest2.json", tmp_path / "source2.zip")
    assert not (tmp_path / "source2.zip").exists()


def test_source_update_rejects_hash_mismatch_and_untrusted_redirect(tmp_path, monkeypatch):
    routes = _release_routes(digest="0" * 64)
    _use_routes(monkeypatch, routes)
    with pytest.raises(ValueError, match="SHA-256"):
        package.download_verified_source_archive(tmp_path / "manifest.json", tmp_path / "source.zip")
    assert not (tmp_path / "source.zip").exists()

    routes = _release_routes()
    _use_routes(monkeypatch, routes, bad_redirect="https://attacker.invalid/payload")
    with pytest.raises(ValueError, match="недопустимый адрес"):
        package.download_verified_source_archive(tmp_path / "manifest2.json", tmp_path / "source2.zip")
    assert not (tmp_path / "manifest2.json").exists()


def test_source_update_rejects_unsigned_unknown_and_tampered_manifests(tmp_path, monkeypatch):
    routes = _release_routes(signed=False)
    observed = _use_routes(monkeypatch, routes)
    with pytest.raises(ValueError, match="publisher signature"):
        package.download_verified_source_archive(tmp_path / "unsigned.json", tmp_path / "unsigned.zip")
    assert not (tmp_path / "unsigned.zip").exists()
    assert all(not url.endswith(package.SOURCE_ASSET) for url, _timeout in observed)

    unknown_key = Ed25519PrivateKey.generate()
    unknown_pem = unknown_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    unknown = signature.sign_manifest(json.loads(routes[package._release_asset_url(
        "v4.0.1", package.MANIFEST_ASSET,
    )]), unknown_pem)
    routes = _release_routes(manifest=unknown)
    observed = _use_routes(monkeypatch, routes)
    with pytest.raises(ValueError, match="unknown publisher key"):
        package.download_verified_source_archive(tmp_path / "unknown.json", tmp_path / "unknown.zip")
    assert not (tmp_path / "unknown.zip").exists()
    assert all(not url.endswith(package.SOURCE_ASSET) for url, _timeout in observed)

    routes = _release_routes()
    signed_manifest = json.loads(routes[package._release_asset_url("v4.0.1", package.MANIFEST_ASSET)])
    signed_manifest["assets"][0]["sha256"] = "0" * 64
    routes[package._release_asset_url("v4.0.1", package.MANIFEST_ASSET)] = json.dumps(signed_manifest).encode()
    _use_routes(monkeypatch, routes)
    with pytest.raises(ValueError, match="signature verification failed"):
        package.download_verified_source_archive(tmp_path / "tampered.json", tmp_path / "tampered.zip")
    assert not (tmp_path / "tampered.zip").exists()


def test_download_archive_is_bounded_and_uses_timeout(tmp_path, monkeypatch):
    class Response:
        headers = {"Content-Length": "4"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size):
            if getattr(self, "done", False):
                return b""
            self.done = True
            return b"data"

    observed = {}

    def fake_urlopen(request, timeout):
        observed["url"] = request.full_url
        observed["timeout"] = timeout
        return Response()

    monkeypatch.setattr(package.urllib.request, "urlopen", fake_urlopen)
    destination = tmp_path / "source.zip"

    assert package.download_archive("https://example.invalid/release.zip", destination, timeout=7, max_bytes=4) == 4
    assert destination.read_bytes() == b"data"
    assert observed == {"url": "https://example.invalid/release.zip", "timeout": 7}


def test_download_rejects_oversize_before_writing(tmp_path, monkeypatch):
    class Response:
        headers = {"Content-Length": "5"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size):
            raise AssertionError("oversized response should be rejected from its header")

    monkeypatch.setattr(package.urllib.request, "urlopen", lambda *_a, **_k: Response())
    destination = tmp_path / "source.zip"

    with pytest.raises(ValueError, match="лимит"):
        package.download_archive("https://example.invalid/release.zip", destination, max_bytes=4)
    assert not destination.exists()


def test_download_failure_preserves_a_preexisting_destination(tmp_path, monkeypatch):
    destination = tmp_path / "caller-owned.bin"
    destination.write_bytes(b"keep this file")

    def fail_urlopen(*_args, **_kwargs):
        raise OSError("offline")

    monkeypatch.setattr(package.urllib.request, "urlopen", fail_urlopen)

    with pytest.raises(OSError, match="offline"):
        package.download_archive("https://example.invalid/release.zip", destination)

    assert destination.read_bytes() == b"keep this file"


def test_download_failure_removes_only_its_partial_file(tmp_path, monkeypatch):
    class PartialResponse:
        headers = {"Content-Length": "8"}

        def __init__(self):
            self.reads = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size):
            self.reads += 1
            if self.reads == 1:
                return b"partial"
            raise OSError("connection dropped")

    monkeypatch.setattr(package.urllib.request, "urlopen", lambda *_args, **_kwargs: PartialResponse())
    destination = tmp_path / "partial.bin"

    with pytest.raises(OSError, match="connection dropped"):
        package.download_archive("https://example.invalid/release.zip", destination)

    assert not destination.exists()


def test_extracts_only_required_agent_files(tmp_path):
    archive = tmp_path / "release.zip"
    stage = tmp_path / "stage"
    expected = ("xgent_mcs.py", "config.py")
    _zip(archive, [
        ("XIDER-source/XGENT-MCS/xgent_mcs.py", b"print('agent')", None),
        ("XIDER-source/XGENT-MCS/config.py", b"VALUE = 1", None),
        ("XIDER-source/TG-BOT-SERVER/bot.py", b"not copied", None),
    ])

    result = package.extract_agent_files(archive, stage, expected)

    assert result == stage.resolve()
    assert (stage / "xgent_mcs.py").read_bytes() == b"print('agent')"
    assert (stage / "config.py").read_bytes() == b"VALUE = 1"
    assert sorted(path.name for path in stage.iterdir()) == sorted(expected)


def test_extracts_only_allowlisted_windows_component_files(tmp_path):
    archive = tmp_path / "windows-release.zip"
    stage = tmp_path / "windows-stage"
    expected = ("xgent_wds.py", "config.py")
    _zip(archive, [
        ("XIDER-source/XGENT-WDS/xgent_wds.py", b"print('windows')", None),
        ("XIDER-source/XGENT-WDS/config.py", b"VERSION = '4.0.1'", None),
        ("XIDER-source/XGENT-MCS/config.py", b"wrong component", None),
        ("XIDER-source/TG-BOT-SERVER/bot.py", b"not copied", None),
    ])

    result = package.extract_agent_files(
        archive,
        stage,
        expected,
        component_dir="XGENT-WDS",
    )

    assert result == stage.resolve()
    assert (stage / "xgent_wds.py").read_bytes() == b"print('windows')"
    assert (stage / "config.py").read_bytes() == b"VERSION = '4.0.1'"
    assert sorted(path.name for path in stage.iterdir()) == sorted(expected)


def test_windows_update_stages_shared_verifier_and_updater_from_mcs(tmp_path):
    archive = tmp_path / "windows-shared-release.zip"
    stage = tmp_path / "windows-shared-stage"
    expected = ("xgent_wds.py", "release_signature.py", "update_package.py")
    _zip(archive, [
        ("XIDER-source/XGENT-WDS/xgent_wds.py", b"print('windows')", None),
        ("XIDER-source/XGENT-MCS/release_signature.py", b"VALUE = 'verifier'", None),
        ("XIDER-source/XGENT-MCS/update_package.py", b"VALUE = 'updater'", None),
    ])

    result = package.extract_agent_files(
        archive,
        stage,
        expected,
        component_dir="XGENT-WDS",
    )

    assert (result / "xgent_wds.py").read_bytes() == b"print('windows')"
    assert (result / "release_signature.py").read_bytes() == b"VALUE = 'verifier'"
    assert (result / "update_package.py").read_bytes() == b"VALUE = 'updater'"


def test_windows_update_rejects_duplicate_shared_helpers(tmp_path):
    archive = tmp_path / "windows-duplicate-helper.zip"
    _zip(archive, [
        ("XIDER-source/XGENT-WDS/release_signature.py", b"wrong", None),
        ("XIDER-source/XGENT-MCS/release_signature.py", b"right", None),
    ])

    with pytest.raises(ValueError, match="повторяется"):
        package.extract_agent_files(
            archive,
            tmp_path / "duplicate-stage",
            ("release_signature.py",),
            component_dir="XGENT-WDS",
        )


def test_extract_rejects_unknown_agent_component(tmp_path):
    archive = tmp_path / "release.zip"
    _zip(archive, [("XIDER-source/XGENT-MCS/config.py", b"value = 1", None)])

    with pytest.raises(ValueError, match="Неизвестный компонент"):
        package.extract_agent_files(
            archive,
            tmp_path / "unknown-stage",
            ("config.py",),
            component_dir="TG-BOT-SERVER",
        )


@pytest.mark.parametrize("name", ["../outside.py", "/absolute.py", "C:/drive.py"])
def test_extract_rejects_unsafe_zip_paths(tmp_path, name):
    archive = tmp_path / "release.zip"
    _zip(archive, [
        ("XIDER-source/XGENT-MCS/xgent_mcs.py", b"agent", None),
        ("XIDER-source/XGENT-MCS/config.py", b"config", None),
        (name, b"escape", None),
    ])

    with pytest.raises(ValueError, match="Небезопасный путь"):
        package.extract_agent_files(archive, tmp_path / "stage", ("xgent_mcs.py", "config.py"))
    assert not (tmp_path / "outside.py").exists()


def test_extract_rejects_symlink_duplicate_and_missing_files(tmp_path):
    archive = tmp_path / "release.zip"
    _zip(archive, [
        ("XIDER-source/XGENT-MCS/xgent_mcs.py", b"agent", None),
        ("XIDER-source/XGENT-MCS/config.py", b"config", None),
        ("XIDER-source/XGENT-MCS/link.py", b"target", 0o120777),
    ])
    with pytest.raises(ValueError, match="symlink"):
        package.extract_agent_files(archive, tmp_path / "symlink-stage", ("xgent_mcs.py", "config.py"))

    _zip(archive, [
        ("XIDER-source/XGENT-MCS/xgent_mcs.py", b"agent", None),
        ("XIDER-source/./XGENT-MCS/xgent_mcs.py", b"duplicate", None),
        ("XIDER-source/XGENT-MCS/config.py", b"config", None),
    ])
    with pytest.raises(ValueError, match="Дублирующийся"):
        package.extract_agent_files(archive, tmp_path / "duplicate-stage", ("xgent_mcs.py", "config.py"))

    _zip(archive, [("XIDER-source/XGENT-MCS/xgent_mcs.py", b"agent", None)])
    with pytest.raises(ValueError, match="отсутствуют"):
        package.extract_agent_files(archive, tmp_path / "missing-stage", ("xgent_mcs.py", "config.py"))


def test_extract_enforces_uncompressed_size_and_empty_stage(tmp_path):
    archive = tmp_path / "release.zip"
    _zip(archive, [
        ("XIDER-source/XGENT-MCS/xgent_mcs.py", b"1234", None),
        ("XIDER-source/XGENT-MCS/config.py", b"c", None),
    ])
    with pytest.raises(ValueError, match="лимит"):
        package.extract_agent_files(
            archive,
            tmp_path / "small-limit",
            ("xgent_mcs.py", "config.py"),
            max_uncompressed_bytes=4,
        )

    existing = tmp_path / "nonempty"
    existing.mkdir()
    (existing / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="пустым"):
        package.extract_agent_files(archive, existing, ("xgent_mcs.py", "config.py"))
    assert (existing / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_install_agent_files_backs_up_existing_files_and_adds_new_ones(tmp_path, monkeypatch):
    source = tmp_path / "stage"
    install = tmp_path / "install"
    backup = tmp_path / package.UPDATE_BACKUP_DIR / "update-1"
    state_path = tmp_path / package.UPDATE_STATE_NAME
    source.mkdir()
    install.mkdir()
    (source / "xgent_mcs.py").write_text("new-agent", encoding="utf-8")
    (source / "update_package.py").write_text("new-helper", encoding="utf-8")
    (install / "xgent_mcs.py").write_text("old-agent", encoding="utf-8")
    fsynced_directories = []
    original_fsync_directory = package._fsync_directory

    def record_fsync_directory(path):
        fsynced_directories.append(Path(path).resolve())
        return original_fsync_directory(path)

    monkeypatch.setattr(package, "_fsync_directory", record_fsync_directory)

    result = package.install_agent_files(
        source, install, backup, ("xgent_mcs.py", "update_package.py"),
        transaction_path=state_path,
    )

    assert result == backup.resolve()
    assert (backup / "xgent_mcs.py").read_text(encoding="utf-8") == "old-agent"
    assert (install / "xgent_mcs.py").read_text(encoding="utf-8") == "new-agent"
    assert (install / "update_package.py").read_text(encoding="utf-8") == "new-helper"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == "awaiting_health"
    assert state["start_attempts"] == 0
    # The backup directory and its entries are made durable before the
    # transaction journal can authorize any replacement of the live files.
    assert fsynced_directories.index(backup.resolve()) < fsynced_directories.index(state_path.parent.resolve())
    assert install.resolve() in fsynced_directories
    assert package.begin_agent_start(state_path, install) == "pending"
    assert package.mark_agent_update_healthy(state_path, install)
    assert not state_path.exists()
    assert (backup / "xgent_mcs.py").read_text(encoding="utf-8") == "old-agent"


def test_install_agent_files_rolls_back_touched_and_new_files_on_error(tmp_path, monkeypatch):
    source = tmp_path / "stage"
    install = tmp_path / "install"
    backup = tmp_path / package.UPDATE_BACKUP_DIR / "update-2"
    state_path = tmp_path / package.UPDATE_STATE_NAME
    source.mkdir()
    install.mkdir()
    (source / "xgent_mcs.py").write_text("new-agent", encoding="utf-8")
    (source / "config.py").write_text("new-file", encoding="utf-8")
    (install / "xgent_mcs.py").write_text("old-agent", encoding="utf-8")
    original_copy2 = package.shutil.copy2
    calls = 0

    def fail_while_installing_second_file(src, dst, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("simulated disk error")
        return original_copy2(src, dst, *args, **kwargs)

    monkeypatch.setattr(package.shutil, "copy2", fail_while_installing_second_file)
    with pytest.raises(OSError, match="simulated disk error"):
        package.install_agent_files(
            source, install, backup, ("xgent_mcs.py", "config.py"),
            transaction_path=state_path,
        )

    assert (install / "xgent_mcs.py").read_text(encoding="utf-8") == "old-agent"
    assert not (install / "config.py").exists()
    assert not state_path.exists()
    assert (backup / "xgent_mcs.py").read_text(encoding="utf-8") == "old-agent"


def test_agent_update_retries_then_rolls_back_when_mqtt_health_never_arrives(tmp_path):
    source = tmp_path / "stage"
    install = tmp_path / "install"
    backup = tmp_path / package.UPDATE_BACKUP_DIR / "update-health-timeout"
    state_path = tmp_path / package.UPDATE_STATE_NAME
    source.mkdir()
    install.mkdir()
    (source / "xgent_mcs.py").write_text("new-agent", encoding="utf-8")
    (source / "config.py").write_text("new-helper", encoding="utf-8")
    (install / "xgent_mcs.py").write_text("old-agent", encoding="utf-8")

    package.install_agent_files(
        source, install, backup, ("xgent_mcs.py", "config.py"),
        transaction_path=state_path,
    )

    assert package.prepare_agent_update_start(state_path, install) == "pending"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["start_attempts"] == 1
    assert package.prepare_agent_update_start(state_path, install) == "rolled_back"
    assert (install / "xgent_mcs.py").read_text(encoding="utf-8") == "old-agent"
    assert not (install / "config.py").exists()
    assert not state_path.exists()


def test_interrupted_multi_file_install_is_recovered_from_durable_journal(tmp_path):
    source = tmp_path / "stage"
    install = tmp_path / "install"
    backup = tmp_path / package.UPDATE_BACKUP_DIR / "update-interrupted"
    state_path = tmp_path / package.UPDATE_STATE_NAME
    source.mkdir()
    install.mkdir()
    (source / "xgent_mcs.py").write_text("new-agent", encoding="utf-8")
    (source / "config.py").write_text("new-helper", encoding="utf-8")
    (install / "xgent_mcs.py").write_text("old-agent", encoding="utf-8")

    package.install_agent_files(
        source, install, backup, ("xgent_mcs.py", "config.py"),
        transaction_path=state_path,
    )
    # Simulate power loss after file replacement but before the pending-health
    # marker was durably committed.
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["status"] = "installing"
    package._atomic_write_json(state_path, state)

    assert package.prepare_agent_update_start(state_path, install) == "recovered"
    assert (install / "xgent_mcs.py").read_text(encoding="utf-8") == "old-agent"
    assert not (install / "config.py").exists()
    assert not state_path.exists()


def test_update_recovery_rejects_a_journal_for_another_install_dir(tmp_path):
    state_root = tmp_path / "state"
    backup = state_root / package.UPDATE_BACKUP_DIR / "update-other"
    backup.mkdir(parents=True)
    install = tmp_path / "install"
    install.mkdir()
    other = tmp_path / "outside"
    other.mkdir()
    (backup / "xgent_mcs.py").write_text("old-agent", encoding="utf-8")
    (install / "xgent_mcs.py").write_text("new-agent", encoding="utf-8")
    state_path = state_root / package.UPDATE_STATE_NAME
    package._atomic_write_json(state_path, {
        "schema": 1,
        "status": "installing",
        "install_dir": str(other.resolve()),
        "backup_dir": str(backup.resolve()),
        "files": ["xgent_mcs.py"],
        "existing": ["xgent_mcs.py"],
        "start_attempts": 0,
    })

    with pytest.raises(ValueError, match="другой каталог агента"):
        package.recover_interrupted_install(state_path, install)
    assert (install / "xgent_mcs.py").read_text(encoding="utf-8") == "new-agent"
    assert (other / "xgent_mcs.py").exists() is False
