"""Signed, bounded, release-pinned and path-safe agent update inputs.

The publisher signature authenticates the release inventory; the selected
source archive must also match its exact byte count and SHA-256 digest. Both
checks are required before path-safe extraction or any file replacement.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

from release_signature import verify_manifest_signature


MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
MAX_AGENT_BYTES = 32 * 1024 * 1024
MAX_MEMBER_COUNT = 20_000
DOWNLOAD_TIMEOUT_SECONDS = 30
MAX_MANIFEST_BYTES = 1024 * 1024
RELEASE_API = "https://api.github.com/repos/invinby/XIDER/releases"
RELEASE_BASE = "https://github.com/invinby/XIDER/releases/download"
SOURCE_ASSET = "XIDER-source.zip"
MANIFEST_ASSET = "release-manifest.json"
_RELEASE_TAG = re.compile(r"^v\d{1,6}\.\d{1,6}\.\d{1,6}$")
_ALLOWED_RELEASE_HOSTS = frozenset({
    "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com",
})
_WINDOWS_SHARED_UPDATE_FILES = frozenset({"release_signature.py", "update_package.py"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
UPDATE_STATE_NAME = "agent-update-state.json"
UPDATE_BACKUP_DIR = "agent-backups"
AGENT_UPDATE_FILES = frozenset({
    "xgent_mcs.py", "config.py", "crypto.py", "xgencrypto.py",
    "release_signature.py", "update_package.py", "xider_guardian.py",
    "requirements.txt", "setup_mac.py",
    "start_agent.sh", "stop_agent.sh", "start_guardian.sh",
    "xgent_wds.py", "xider_guardian_wds.py",
})


def _atomic_write_json(path: Path, data: dict) -> None:
    """Durably replace a small transaction marker without following symlinks."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("Отказ писать состояние обновления через symlink.")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        _fsync_directory(path.parent)
    finally:
        temporary_path.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    """Persist directory entries on POSIX; Windows lacks portable dir fsync."""
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(Path(path), flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_update_state(state_path: Path, expected_install_dir: Path) -> dict | None:
    state_path = Path(state_path)
    if state_path.is_symlink():
        raise ValueError("Состояние обновления не может быть symlink.")
    if not state_path.exists():
        return None
    if not state_path.is_file():
        raise ValueError("Состояние обновления не является обычным файлом.")
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Не удалось прочитать журнал обновления агента.") from exc
    if not isinstance(state, dict) or state.get("schema") != 1:
        raise ValueError("Неизвестная версия журнала обновления.")
    raw_install_dir = Path(str(state.get("install_dir", "")))
    install_dir = raw_install_dir.resolve()
    if install_dir != Path(expected_install_dir).resolve():
        raise ValueError("Журнал обновления указывает на другой каталог агента.")
    if raw_install_dir.resolve() != raw_install_dir.absolute():
        raise ValueError("Каталог агента из журнала проходит через symlink.")
    raw_backup_dir = Path(str(state.get("backup_dir", "")))
    backup_dir = raw_backup_dir.resolve()
    state_root = state_path.parent.resolve()
    backup_root = state_root / UPDATE_BACKUP_DIR
    try:
        relative_backup = raw_backup_dir.absolute().relative_to(backup_root.absolute())
    except ValueError as exc:
        raise ValueError("Резервная копия находится вне каталога agent-backups.") from exc
    if (
        not relative_backup.parts
        or ".." in relative_backup.parts
        or backup_root.is_symlink()
        or raw_backup_dir.is_symlink()
        or raw_backup_dir.resolve() != raw_backup_dir.absolute()
        or not backup_dir.is_dir()
    ):
        raise ValueError("Каталог резервной копии отсутствует или небезопасен.")
    files = state.get("files")
    existing = state.get("existing")
    if (
        not isinstance(files, list)
        or not files
        or any(not isinstance(name, str) or Path(name).name != name or name in {"", ".", ".."} for name in files)
        or len(set(files)) != len(files)
        or any(name not in AGENT_UPDATE_FILES for name in files)
        or not isinstance(existing, list)
        or any(name not in files for name in existing)
        or len(set(existing)) != len(existing)
    ):
        raise ValueError("Список файлов в журнале обновления некорректен.")
    if state.get("status") not in {"installing", "awaiting_health"}:
        raise ValueError("Некорректное состояние транзакции обновления.")
    for name in existing:
        backup = backup_dir / name
        if backup.is_symlink() or not backup.is_file():
            raise ValueError(f"Отсутствует безопасная резервная копия файла {name}.")
    return state


def _rollback_update(state_path: Path, expected_install_dir: Path) -> dict:
    state = _read_update_state(state_path, expected_install_dir)
    if state is None:
        return {"restored": [], "removed": []}
    install_dir = Path(state["install_dir"]).resolve()
    backup_dir = Path(state["backup_dir"]).resolve()
    files = state["files"]
    existing = set(state["existing"])
    restored: list[str] = []
    removed: list[str] = []
    problems: list[str] = []
    for name in reversed(files):
        target = install_dir / name
        if target.is_symlink():
            problems.append(f"{name}: установленный путь стал symlink")
            continue
        if name in existing:
            try:
                _atomic_copy(backup_dir / name, target)
                restored.append(name)
            except Exception as exc:
                problems.append(f"{name}: {exc}")
        else:
            try:
                target.unlink(missing_ok=True)
                removed.append(name)
            except Exception as exc:
                problems.append(f"{name}: {exc}")
    if problems:
        raise RuntimeError("Автоматический откат неполон: " + "; ".join(problems))
    state_path = Path(state_path)
    _fsync_directory(install_dir)
    state_path.unlink(missing_ok=True)
    _fsync_directory(state_path.parent)
    return {"restored": restored, "removed": removed}


def begin_agent_start(state_path: Path, expected_install_dir: Path) -> str:
    """Arm one health-check attempt; rollback on an incomplete prior start."""
    state = _read_update_state(state_path, expected_install_dir)
    if state is None:
        return "none"
    if state["status"] == "installing":
        _rollback_update(state_path, expected_install_dir)
        return "rolled_back"
    attempts = state.get("start_attempts", 0)
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
        raise ValueError("Счётчик запусков обновления некорректен.")
    if attempts >= 1:
        _rollback_update(state_path, expected_install_dir)
        return "rolled_back"
    state["start_attempts"] = attempts + 1
    _atomic_write_json(Path(state_path), state)
    return "pending"


def mark_agent_update_healthy(state_path: Path, expected_install_dir: Path) -> bool:
    """Commit the pending update after the new agent establishes MQTT."""
    state = _read_update_state(state_path, expected_install_dir)
    if state is None:
        return False
    if state["status"] != "awaiting_health":
        raise ValueError("Установка ещё не перешла к проверке здоровья.")
    state_path = Path(state_path)
    state_path.unlink()
    _fsync_directory(state_path.parent)
    return True


def rollback_unhealthy_agent(state_path: Path, expected_install_dir: Path) -> dict:
    """Restore the previous agent when the new version misses its health deadline."""
    return _rollback_update(state_path, expected_install_dir)


def recover_interrupted_install(state_path: Path, expected_install_dir: Path) -> bool:
    """Roll back a file swap interrupted before its new process could start."""
    state = _read_update_state(state_path, expected_install_dir)
    if state is None or state["status"] != "installing":
        return False
    _rollback_update(state_path, expected_install_dir)
    return True


def prepare_agent_update_start(state_path: Path, expected_install_dir: Path) -> str:
    """Recover or arm the health attempt before importing mutable agent files."""
    if recover_interrupted_install(state_path, expected_install_dir):
        return "recovered"
    return begin_agent_start(state_path, expected_install_dir)


def _release_tag(value: object) -> str:
    tag = str(value or "").strip()
    if not _RELEASE_TAG.fullmatch(tag):
        raise ValueError("Некорректный тег стабильного релиза XIDER.")
    return tag


def _check_https_host(url: str, allowed_hosts: frozenset[str]) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in allowed_hosts:
        raise ValueError("GitHub вернул недопустимый адрес релизного файла.")


def _fetch_release(release_tag: str | None) -> tuple[str, frozenset[str]]:
    requested_tag = _release_tag(release_tag) if release_tag else None
    if requested_tag:
        url = f"{RELEASE_API}/tags/{urllib.parse.quote(requested_tag, safe='')}"
    else:
        url = f"{RELEASE_API}/latest"
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "XIDER-macOS-Agent/4"},
    )
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
        final_url = response.geturl() if hasattr(response, "geturl") else url
        _check_https_host(final_url, frozenset({"api.github.com"}))
        raw = response.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("Ответ GitHub Release превышает лимит размера.")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("GitHub вернул некорректные данные релиза.") from exc
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        raise ValueError("Релиз отсутствует, является черновиком или prerelease.")
    tag = _release_tag(data.get("tag_name"))
    if requested_tag and tag != requested_tag:
        raise ValueError("GitHub вернул не тот тег релиза.")
    assets = data.get("assets")
    if not isinstance(assets, list):
        raise ValueError("В GitHub Release отсутствует список файлов.")
    names = [
        asset.get("name") for asset in assets
        if isinstance(asset, dict) and isinstance(asset.get("name"), str)
    ]
    if names.count(SOURCE_ASSET) != 1 or names.count(MANIFEST_ASSET) != 1:
        raise ValueError("В релизе нет единственной пары source ZIP и release manifest.")
    return tag, frozenset(names)


def _release_asset_url(tag: str, name: str) -> str:
    if name not in {SOURCE_ASSET, MANIFEST_ASSET}:
        raise ValueError("Запрошен неизвестный asset релиза XIDER.")
    tag = _release_tag(tag)
    return f"{RELEASE_BASE}/{urllib.parse.quote(tag, safe='')}/{name}"


def download_archive(
    url: str,
    destination: Path,
    *,
    timeout: int = DOWNLOAD_TIMEOUT_SECONDS,
    max_bytes: int = MAX_ARCHIVE_BYTES,
    allowed_redirect_hosts: frozenset[str] | None = None,
) -> int:
    """Download an archive with a timeout and compressed-size limit."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "XIDER-macOS-Agent/4", "Accept": "application/zip"},
    )
    total = 0
    created_destination = False
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if allowed_redirect_hosts is not None:
                final_url = response.geturl() if hasattr(response, "geturl") else url
                _check_https_host(final_url, allowed_redirect_hosts)
            length = response.headers.get("Content-Length")
            if length is not None:
                try:
                    declared = int(length)
                except (TypeError, ValueError) as exc:
                    raise ValueError("Некорректный размер ZIP-архива.") from exc
                if declared < 0 or declared > max_bytes:
                    raise ValueError("ZIP-архив превышает лимит размера.")

            with destination.open("xb") as output:
                created_destination = True
                while True:
                    block = response.read(64 * 1024)
                    if not block:
                        break
                    total += len(block)
                    if total > max_bytes:
                        raise ValueError("ZIP-архив превышает лимит размера.")
                    output.write(block)
    except Exception:
        if created_destination:
            destination.unlink(missing_ok=True)
        raise
    return total


def download_verified_source_archive(
    manifest_path: Path,
    archive_path: Path,
    *,
    release_tag: str | None = None,
) -> str:
    """Verify publisher signature and source ZIP inventory before extraction."""
    tag, _ = _fetch_release(release_tag)
    manifest_path = Path(manifest_path)
    archive_path = Path(archive_path)
    download_archive(
        _release_asset_url(tag, MANIFEST_ASSET), manifest_path,
        max_bytes=MAX_MANIFEST_BYTES,
        allowed_redirect_hosts=_ALLOWED_RELEASE_HOSTS,
    )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Файл release-manifest.json повреждён.") from exc
    verify_manifest_signature(manifest)
    if (
        not isinstance(manifest, dict)
        or type(manifest.get("schema")) is not int
        or manifest.get("schema") != 1
        or manifest.get("release") != tag
    ):
        raise ValueError("Manifest не соответствует выбранному тегу релиза.")
    assets = manifest.get("assets")
    if not isinstance(assets, list):
        raise ValueError("В manifest отсутствует список файлов.")
    matches = [
        item for item in assets
        if isinstance(item, dict) and item.get("name") == SOURCE_ASSET
    ]
    if len(matches) != 1:
        raise ValueError("В manifest нет единственной записи XIDER-source.zip.")
    item = matches[0]
    size = item.get("size")
    digest = item.get("sha256")
    if (
        item.get("component") != "source"
        or item.get("platform") != "all"
        or isinstance(size, bool)
        or not isinstance(size, int)
        or size <= 0
        or size > MAX_ARCHIVE_BYTES
        or not isinstance(digest, str)
        or not _SHA256.fullmatch(digest)
    ):
        raise ValueError("Запись XIDER-source.zip в manifest некорректна.")

    source_archive_created = False
    try:
        received_size = download_archive(
            _release_asset_url(tag, SOURCE_ASSET), archive_path,
            max_bytes=size,
            allowed_redirect_hosts=_ALLOWED_RELEASE_HOSTS,
        )
        source_archive_created = True
        if received_size != size:
            raise ValueError("Размер source ZIP не совпадает с release manifest.")
        actual_digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        if not hmac.compare_digest(actual_digest, digest):
            raise ValueError("SHA-256 source ZIP не совпадает с release manifest.")
    except Exception:
        if source_archive_created:
            archive_path.unlink(missing_ok=True)
        raise
    return tag


def extract_agent_files(
    archive_path: Path,
    destination: Path,
    files: tuple[str, ...],
    *,
    component_dir: str = "XGENT-MCS",
    max_uncompressed_bytes: int = MAX_UNCOMPRESSED_BYTES,
    max_agent_bytes: int = MAX_AGENT_BYTES,
) -> Path:
    """Copy only allowlisted files from one agent directory in a validated ZIP."""
    destination = Path(destination).resolve()
    if component_dir not in {"XGENT-MCS", "XGENT-WDS"}:
        raise ValueError("Неизвестный компонент агента для распаковки.")
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ValueError("Каталог подготовки обновления должен быть пустым.")

    expected = frozenset(files)
    if not expected or any(Path(name).name != name or name in {"", ".", ".."} for name in expected):
        raise ValueError("Некорректный список файлов обновления.")

    selected: dict[str, zipfile.ZipInfo] = {}
    roots: set[str] = set()
    seen: set[str] = set()
    total_uncompressed = 0
    total_agent = 0

    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        if len(members) > MAX_MEMBER_COUNT:
            raise ValueError("В ZIP-архиве слишком много записей.")

        for info in members:
            name = info.filename
            path = PurePosixPath(name)
            mode = (info.external_attr >> 16) & 0o170000
            if (
                not name
                or "\\" in name
                or "\x00" in name
                or path.is_absolute()
                or ".." in path.parts
                or (path.parts and ":" in path.parts[0])
                or mode == 0o120000
            ):
                raise ValueError(f"Небезопасный путь или symlink в ZIP: {name!r}")
            normalized = path.as_posix()
            if normalized in seen:
                raise ValueError(f"Дублирующийся путь в ZIP: {name!r}")
            seen.add(normalized)
            if info.flag_bits & 0x1:
                raise ValueError("Зашифрованные ZIP-записи не поддерживаются.")

            size = int(info.file_size)
            if size < 0:
                raise ValueError("Некорректный размер ZIP-записи.")
            total_uncompressed += size
            if total_uncompressed > max_uncompressed_bytes:
                raise ValueError("Распакованный ZIP превышает лимит размера.")
            if info.is_dir() or len(path.parts) < 3:
                continue

            filename = path.parts[-1]
            if filename not in expected:
                continue
            source_component = path.parts[-2]
            if source_component != component_dir and not (
                component_dir == "XGENT-WDS"
                and filename in _WINDOWS_SHARED_UPDATE_FILES
                and source_component == "XGENT-MCS"
            ):
                continue
            if filename in selected:
                raise ValueError(f"Файл агента повторяется в ZIP: {filename}")
            selected[filename] = info
            roots.add(path.parts[0])
            total_agent += size
            if total_agent > max_agent_bytes:
                raise ValueError("Файлы агента превышают лимит размера.")

        missing = expected - selected.keys()
        if missing:
            raise ValueError("В ZIP отсутствуют файлы агента: " + ", ".join(sorted(missing)))
        if len(roots) != 1:
            raise ValueError("В ZIP должен быть ровно один корневой каталог XIDER.")

        for filename, info in selected.items():
            target = destination / filename
            with archive.open(info) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)

    return destination


def _atomic_copy(source: Path, target: Path) -> None:
    """Replace one file atomically on its volume, keeping source metadata."""
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.xider-", dir=target.parent)
    os.close(descriptor)
    temporary_path = Path(temporary)
    try:
        shutil.copy2(source, temporary_path)
        with temporary_path.open("rb+") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary_path, target)
        _fsync_directory(target.parent)
    finally:
        temporary_path.unlink(missing_ok=True)


def install_agent_files(
    source: Path,
    install_dir: Path,
    backup_dir: Path,
    files: tuple[str, ...],
    *,
    transaction_path: Path | None = None,
) -> Path:
    """Back up, replace allowlisted files, and leave a restart-health journal.

    Each file replacement is atomic. A durable journal lets the next process
    restore an interrupted transaction, and the new process must establish MQTT
    before the journal is committed. This is not an A/B slot: a process kill can
    briefly leave a mixed tree until the OS restarts the agent and it rolls back.
    """
    source = Path(source).resolve()
    install_dir = Path(install_dir).resolve()
    backup_dir = Path(backup_dir).resolve()
    names = tuple(files)
    if (
        not names
        or len(set(names)) != len(names)
        or any(Path(name).name != name or name in {"", ".", ".."} for name in names)
        or any(name not in AGENT_UPDATE_FILES for name in names)
    ):
        raise ValueError("Некорректный список файлов для установки агента.")
    if backup_dir == install_dir or install_dir in backup_dir.parents:
        raise ValueError("Резервная копия не должна находиться внутри установленного агента.")
    if transaction_path is None:
        transaction_path = backup_dir.parent.parent / UPDATE_STATE_NAME
    transaction_path = Path(transaction_path)
    if transaction_path.is_symlink():
        raise ValueError("Отказ использовать symlink для журнала обновления.")
    transaction_path = transaction_path.resolve()
    state_root = transaction_path.parent
    if transaction_path.exists() or transaction_path.is_symlink():
        raise RuntimeError("Предыдущее обновление ещё не подтверждено; новое не запускалось.")
    try:
        backup_dir.relative_to(state_root)
    except ValueError as exc:
        raise ValueError("Резервная копия должна находиться рядом с журналом обновления.") from exc
    if backup_dir == state_root:
        raise ValueError("Каталог резервной копии не может совпадать с каталогом журнала.")
    source_paths = {name: source / name for name in names}
    if any(path.is_symlink() or not path.is_file() for path in source_paths.values()):
        raise ValueError("В подготовленном каталоге отсутствует обязательный обычный файл агента.")
    install_dir.mkdir(parents=True, exist_ok=True)
    existing: set[str] = set()
    for name in names:
        target = install_dir / name
        if target.is_symlink():
            raise ValueError(f"Отказ заменять ссылку в установленном агенте: {name}")
        if target.exists():
            if not target.is_file():
                raise ValueError(f"Путь установленного агента не является файлом: {name}")
            existing.add(name)

    backup_dir.parent.mkdir(parents=True, exist_ok=True)
    backup_dir.mkdir(exist_ok=False)
    # Complete and flush every backup before the first live file is touched.
    for name in names:
        if name in existing:
            _atomic_copy(install_dir / name, backup_dir / name)
    _fsync_directory(backup_dir)
    _fsync_directory(backup_dir.parent)
    _fsync_directory(state_root)

    state = {
        "schema": 1,
        "status": "installing",
        "install_dir": str(install_dir),
        "backup_dir": str(backup_dir),
        "files": list(names),
        "existing": [name for name in names if name in existing],
        "start_attempts": 0,
    }
    _atomic_write_json(transaction_path, state)

    try:
        for name in names:
            target = install_dir / name
            _atomic_copy(source_paths[name], target)
            if name.endswith(".sh"):
                target.chmod(target.stat().st_mode | 0o111)
        state["status"] = "awaiting_health"
        state["created_at"] = int(time.time())
        _atomic_write_json(transaction_path, state)
    except Exception as install_error:
        try:
            _rollback_update(transaction_path, install_dir)
        except Exception as rollback_error:
            raise RuntimeError(
                f"Ошибка установки; автоматический откат неполон: {rollback_error}"
            ) from install_error
        raise
    return backup_dir
