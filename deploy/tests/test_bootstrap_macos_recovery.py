"""Offline signed-cache integration for the macOS bootstrap.

Real recovery/signature helpers run against an ephemeral publisher key and
fixture archives. curl, SSH, pip, launchctl and agent processes are local mocks.
Windows Git Bash may emulate the venv link; this suite verifies cache transaction
ordering, while native runtime-link behavior belongs to the shell/macOS fixture.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[2]


def _bash() -> str:
    if os.name == "nt":
        candidate = Path("C:/Program Files/Git/bin/bash.exe")
        if candidate.is_file():
            return str(candidate)
        pytest.skip("Git Bash is required for this offline Windows fixture.")
    candidate = shutil.which("bash")
    if not candidate:
        pytest.skip("bash is required for the macOS bootstrap fixture.")
    return candidate


def _script(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(0o755)


@pytest.fixture
def signed_bootstrap(tmp_path):
    agent_source = tmp_path / "source" / "XGENT-MCS"
    agent_source.mkdir(parents=True)
    for name in ("guardian_recovery.py", "release_signature.py", "update_package.py"):
        shutil.copyfile(ROOT / "XGENT-MCS" / name, agent_source / name)
    # Derive the inventory from the source without importing config or a worker.
    import ast
    tree = ast.parse((agent_source / "guardian_recovery.py").read_text(encoding="utf-8"))
    files = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == "RECOVERY_FILES" for target in node.targets))
    for name in files:
        if not (agent_source / name).exists():
            (agent_source / name).write_text("# local fixture source\n", encoding="utf-8")
    _script(agent_source / "start_agent.sh", """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == --status ]]; then
  echo agent-status >> "$XIDER_TEST_TRACE"
  [[ "${XIDER_TEST_FAIL_STATUS:-0}" != 1 ]] || exit 17
  exit 0
fi
echo agent-start >> "$XIDER_TEST_TRACE"
[[ "${XIDER_RUNTIME_PREPARED:-0}" == 1 ]] || exit 18
grep -q old-selection "$HOME/.xgent/recovery/current.json" || exit 19
mkdir -p "$HOME/jobs"
touch "$HOME/jobs/com.xgent.agent"
""")
    _script(agent_source / "start_guardian.sh", """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == --status ]]; then echo guardian-status >> "$XIDER_TEST_TRACE"; exit 0; fi
echo guardian-start >> "$XIDER_TEST_TRACE"
mkdir -p "$HOME/jobs"
touch "$HOME/jobs/com.xider.guardian"
""")
    _script(agent_source / "stop_agent.sh", "#!/usr/bin/env bash\nexit 0\n")
    (agent_source / "requirements.txt").write_text("# offline fixture dependencies\n", encoding="utf-8")
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    key_id = hashlib.sha256(public).hexdigest()[:16]
    with (agent_source / "release_signature.py").open("a", encoding="utf-8") as stream:
        stream.write(f'\nTRUSTED_RELEASE_KEYS = {{{key_id!r}: bytes.fromhex({public.hex()!r})}}\n')
    archive = tmp_path / "signed-source.zip"
    with zipfile.ZipFile(archive, "w") as output:
        for path in agent_source.iterdir():
            output.write(path, "XIDER-fixture/XGENT-MCS/" + path.name)
    spec = importlib.util.spec_from_file_location("_bootstrap_signatures", ROOT / "XGENT-MCS" / "release_signature.py")
    signatures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(signatures)
    private_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
    archive_bytes = archive.read_bytes()
    manifest = tmp_path / "release-manifest.json"
    manifest.write_text(json.dumps(signatures.sign_manifest({
        "schema": 1, "release": "v4.1.0", "assets": [{
            "name": "XIDER-source.zip", "component": "source", "platform": "all",
            "size": len(archive_bytes), "sha256": hashlib.sha256(archive_bytes).hexdigest(),
        }],
    }, private_pem)), encoding="utf-8")
    install_source = tmp_path / "install-source.zip"
    shutil.copyfile(archive, install_source)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _script(fake_bin / "python3", """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == -m && "${2:-}" == venv ]]; then
  mkdir -p "$3/bin"
  cp "$0" "$3/bin/python3"
  printf 'export PATH="%s/bin:$PATH"\n' "$3" > "$3/bin/activate"
  exit 0
fi
if [[ "${1:-}" == -m && "${2:-}" == pip ]]; then echo dependencies >> "$XIDER_TEST_TRACE"; exit 0; fi
if [[ "${1:-}" == - ]]; then
  source_code="$(cat)"
  if [[ "$source_code" == *"import stage_recovery_package"* ]]; then echo cache-stage >> "$XIDER_TEST_TRACE"; fi
  if [[ "$source_code" == *"import activate_recovery_package"* ]]; then echo cache-activate >> "$XIDER_TEST_TRACE"; fi
  printf '%s\n' "$source_code" | "$XIDER_TEST_REAL_PYTHON" "$@"
  exit "$?"
fi
exec "$XIDER_TEST_REAL_PYTHON" "$@"
""")
    _script(fake_bin / "curl", """#!/usr/bin/env bash
set -euo pipefail
destination=''; url=''
while (($#)); do
  case "$1" in -o) shift; destination="$1" ;; https://*) url="$1" ;; esac
  shift
done
case "$url" in
  */release-manifest.json) cp "$XIDER_TEST_MANIFEST" "$destination" ;;
  */XIDER-source.zip) cp "$XIDER_TEST_SIGNED_ARCHIVE" "$destination" ;;
  *) echo 'Unexpected network URL in offline fixture' >&2; exit 90 ;;
esac
""")
    _script(fake_bin / "ssh", "#!/usr/bin/env bash\necho 'Unexpected SSH call in offline fixture' >&2\nexit 91\n")
    _script(fake_bin / "unzip", """#!/usr/bin/env bash
set -euo pipefail
archive=''; destination=''
while (($#)); do case "$1" in -q) shift; archive="$1" ;; -d) shift; destination="$1" ;; esac; shift; done
"$XIDER_TEST_REAL_PYTHON" -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' "$archive" "$destination"
""")
    _script(fake_bin / "launchctl", """#!/usr/bin/env bash
set -euo pipefail
command="$1"; shift
label="${!#}"; label="${label##*/}"; label="${label%.plist}"
if [[ "$command" == print ]]; then
  [[ -f "$HOME/jobs/$label" ]] || exit 1
  echo 'pid = 777'; exit 0
fi
if [[ "$command" == bootout ]]; then
  echo "bootout:$label" >> "$XIDER_TEST_TRACE"
  if [[ "$label" == com.xider.guardian && "${XIDER_TEST_FAIL_GUARDIAN_BOOTOUT:-0}" == 1 ]]; then exit 92; fi
  if [[ "$label" == com.xider.guardian && "${XIDER_TEST_FAIL_ROLLBACK_GUARDIAN_BOOTOUT:-0}" == 1 ]]; then
    [[ ! -f "$HOME/guardian-stopped-once" ]] || exit 92
    touch "$HOME/guardian-stopped-once"
  fi
  rm -f "$HOME/jobs/$label"; exit 0
fi
exit 93
""")
    install = tmp_path / "install"
    old_agent = install / "git-ver" / "XGENT-MCS"
    old_agent.mkdir(parents=True)
    (old_agent / "old.marker").write_text("old source\n", encoding="utf-8")
    for name in ("start_agent.sh", "start_guardian.sh"):
        _script(old_agent / name, f'#!/usr/bin/env bash\necho old-{name} >> "$XIDER_TEST_TRACE"\nexit 0\n')
    (old_agent / ".env").write_text(
        "SHARED_KEY=offline-test-only-secret\nMQTT_BROKER=broker.example.invalid\nMQTT_PORT=8883\n"
        "MQTT_PREFIX=xgent/v1\nMQTT_TLS=true\nMQTT_USERNAME=test-user\n"
        "MQTT_PASSWORD=test-password-not-real\nENCRYPT_PAYLOAD=true\n", encoding="utf-8")
    home = tmp_path / "home"
    cache = home / ".xgent" / "recovery"
    cache.mkdir(parents=True)
    (cache / "current.json").write_text('{"old-selection": true}\n', encoding="utf-8")
    (home / "jobs").mkdir()
    for label in ("com.xider.guardian", "com.xgent.agent"):
        (home / "jobs" / label).touch()
    trace = tmp_path / "trace.log"
    env = dict(os.environ)
    env.update({
        "HOME": str(home), "USERPROFILE": str(home), "PATH": str(fake_bin) + os.pathsep + env.get("PATH", ""),
        "XIDER_INSTALL_ROOT": str(install), "XIDER_ENV_ROOT": str(home / "no-env"),
        "XIDER_SOURCE_ARCHIVE": str(install_source), "XIDER_RELEASE_TAG": "v4.1.0",
        "XIDER_TEST_TRACE": str(trace), "XIDER_TEST_REAL_PYTHON": Path(sys.executable).as_posix(),
        "XIDER_TEST_FAKE_BIN": fake_bin.as_posix(),
        "XIDER_TEST_MANIFEST": str(manifest), "XIDER_TEST_SIGNED_ARCHIVE": str(archive),
        "PYTHONUTF8": "1",
    })

    def run(**overrides):
        current_env = dict(env)
        current_env.update(overrides)
        # Set the POSIX PATH inside Bash: Windows PATH translation can otherwise
        # place Git's real curl before the fixture wrappers at process startup.
        prefix = 'fixture_bin="$XIDER_TEST_FAKE_BIN"; '
        if os.name == "nt":
            prefix += 'fixture_bin="$(cygpath -u "$fixture_bin")"; '
        command = prefix + ('export PATH="$fixture_bin:$PATH"; '
                            '[[ "$(command -v curl)" == "$fixture_bin/curl" && '
                            '"$(command -v ssh)" == "$fixture_bin/ssh" ]] || exit 94; '
                            'exec bash "$1"')
        return subprocess.run([_bash(), "-c", command, "xider-offline-fixture", (ROOT / "deploy" / "bootstrap.sh").as_posix()], env=current_env,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45)

    return {"run": run, "install": install, "archive": archive, "install_source": install_source,
            "manifest": manifest, "cache": cache, "trace": trace}


def test_signed_cache_stages_before_swap_and_activates_after_both_status_checks(signed_bootstrap):
    fixture = signed_bootstrap
    result = fixture["run"]()
    assert result.returncode == 0, result.stdout + result.stderr
    events = fixture["trace"].read_text(encoding="utf-8").splitlines()
    expected = ["dependencies", "cache-stage", "bootout:com.xider.guardian", "bootout:com.xgent.agent",
                "agent-start", "guardian-start", "agent-status", "guardian-status", "cache-activate"]
    assert [events.index(event) for event in expected] == sorted(events.index(event) for event in expected)
    pointer = json.loads((fixture["cache"] / "current.json").read_text(encoding="utf-8"))
    assert pointer["slot"].startswith("v4.1.0-")
    assert list(fixture["install"].glob("git-ver.previous.*/XGENT-MCS/old.marker"))


@pytest.mark.parametrize("failure", ["release-tag", "source", "manifest", "archive"])
def test_signed_cache_rejection_preserves_live_source_and_selection(signed_bootstrap, failure):
    fixture = signed_bootstrap
    overrides = {}
    if failure == "release-tag":
        overrides["XIDER_RELEASE_TAG"] = "v4.1.1"
    elif failure == "source":
        # The candidate checkout differs, while the signed backup remains intact.
        with zipfile.ZipFile(fixture["install_source"], "a") as output:
            output.writestr("XIDER-fixture/XGENT-MCS/unexpected.py", "# unsigned candidate addition\n")
        # Change an allowlisted member by rebuilding its archive once.
        with zipfile.ZipFile(fixture["install_source"]) as source:
            entries = {item.filename: source.read(item) for item in source.infolist()}
        entries["XIDER-fixture/XGENT-MCS/config.py"] = b"# mismatched allowlisted source\n"
        with zipfile.ZipFile(fixture["install_source"], "w") as output:
            for name, data in entries.items():
                output.writestr(name, data)
    elif failure == "manifest":
        value = json.loads(fixture["manifest"].read_text(encoding="utf-8"))
        value["release"] = "v4.1.1"
        fixture["manifest"].write_text(json.dumps(value), encoding="utf-8")
    else:
        with fixture["archive"].open("ab") as stream:
            stream.write(b"tampered signed cache archive")
    result = fixture["run"](**overrides)
    assert result.returncode != 0
    assert (fixture["install"] / "git-ver" / "XGENT-MCS" / "old.marker").is_file()
    assert json.loads((fixture["cache"] / "current.json").read_text(encoding="utf-8")) == {"old-selection": True}
    events = fixture["trace"].read_text(encoding="utf-8").splitlines()
    assert "cache-stage" in events
    assert not any(event.startswith("bootout:") for event in events)
    assert "cache-activate" not in events


def test_failed_process_status_rolls_back_without_activating_new_recovery(signed_bootstrap):
    fixture = signed_bootstrap
    result = fixture["run"](XIDER_TEST_FAIL_STATUS="1")
    assert result.returncode == 6, result.stdout + result.stderr
    assert (fixture["install"] / "git-ver" / "XGENT-MCS" / "old.marker").is_file()
    assert json.loads((fixture["cache"] / "current.json").read_text(encoding="utf-8")) == {"old-selection": True}
    events = fixture["trace"].read_text(encoding="utf-8").splitlines()
    assert "cache-stage" in events and "agent-status" in events
    assert "cache-activate" not in events


def test_guardian_unload_failure_aborts_before_directory_swap(signed_bootstrap):
    fixture = signed_bootstrap
    result = fixture["run"](XIDER_TEST_FAIL_GUARDIAN_BOOTOUT="1")
    assert result.returncode == 5, result.stdout + result.stderr
    assert (fixture["install"] / "git-ver" / "XGENT-MCS" / "old.marker").is_file()
    assert not list(fixture["install"].glob("git-ver.previous.*"))
    assert json.loads((fixture["cache"] / "current.json").read_text(encoding="utf-8")) == {"old-selection": True}
    assert "cache-activate" not in fixture["trace"].read_text(encoding="utf-8")


def test_rollback_unload_failure_preserves_both_source_trees_and_old_cache(signed_bootstrap):
    fixture = signed_bootstrap
    result = fixture["run"](XIDER_TEST_FAIL_STATUS="1", XIDER_TEST_FAIL_ROLLBACK_GUARDIAN_BOOTOUT="1")
    assert result.returncode == 6, result.stdout + result.stderr
    # The new loaded coordinator must retain its directory; the old source is
    # still recoverable as its backup, and the prior cache remains selected.
    assert not (fixture["install"] / "git-ver" / "XGENT-MCS" / "old.marker").exists()
    assert list(fixture["install"].glob("git-ver.previous.*/XGENT-MCS/old.marker"))
    assert not list(fixture["install"].glob("git-ver.failed.*"))
    assert json.loads((fixture["cache"] / "current.json").read_text(encoding="utf-8")) == {"old-selection": True}
    assert "cache-activate" not in fixture["trace"].read_text(encoding="utf-8")
