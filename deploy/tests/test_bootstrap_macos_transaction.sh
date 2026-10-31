#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BOOTSTRAP="$ROOT/deploy/bootstrap.sh"
FIXTURE="$(mktemp -d /tmp/xider-bootstrap-transaction.XXXXXX)"
trap 'if [[ "${XIDER_KEEP_TEST_FIXTURE:-0}" == 1 ]]; then echo "Fixture retained: $FIXTURE"; elif [[ "$FIXTURE" == /tmp/xider-bootstrap-transaction.* ]]; then rm -rf "$FIXTURE"; fi' EXIT

FAKE_BIN="$FIXTURE/bin"
SOURCE_ROOT="$FIXTURE/source"
ARCHIVE="$FIXTURE/source.zip"
mkdir -p "$FAKE_BIN" "$SOURCE_ROOT/XGENT-MCS" "$FIXTURE/home/Library/LaunchAgents"
REAL_PYTHON="$(command -v python3)"
export XIDER_TEST_REAL_PYTHON="$REAL_PYTHON"
export XIDER_TEST_PIP_LOG="$FIXTURE/pip-invocations.log"
cat > "$FAKE_BIN/python3" <<'PYTHON'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == -m && "${2:-}" == venv ]]; then
  mkdir -p "$3/bin"
  cp "$0" "$3/bin/python3"
  exit 0
fi
if [[ "${1:-}" == -m && "${2:-}" == pip ]]; then
  printf '%s\n' "$0" >> "$XIDER_TEST_PIP_LOG"
  [[ "${XIDER_FAIL_DEPENDENCIES:-0}" != 1 ]] || exit 19
  exit 0
fi
exec "$XIDER_TEST_REAL_PYTHON" "$@"
PYTHON
chmod +x "$FAKE_BIN/python3"
# The bootstrap prefers explicitly versioned interpreters. Route each supported
# name through the fixture so dependency failures are deterministic even on CI
# runners that already have python3.12/3.14 installed.
for version in 3.14 3.12 3.11; do
  ln -s python3 "$FAKE_BIN/python${version}"
done

cat > "$FAKE_BIN/launchctl" <<'LAUNCHCTL'
#!/usr/bin/env bash
if [[ "${1:-}" == print ]]; then
  case "${2:-}" in
    *com.xgent.agent) printf 'pid = 12345\n' ;;
    *com.xider.guardian) printf 'pid = 12346\n' ;;
    *com.xider.safari) printf 'pid = 12347\n' ;;
    *com.xider.siri) printf 'pid = 12348\n' ;;
  esac
fi
exit 0
LAUNCHCTL
chmod +x "$FAKE_BIN/launchctl"
cat > "$FAKE_BIN/ps" <<'PS'
#!/usr/bin/env bash
printf 'xider-fixture-process\n'
PS
chmod +x "$FAKE_BIN/ps"
cat > "$FAKE_BIN/write-health" <<'HEALTH'
#!/usr/bin/env bash
set -euo pipefail
component="$1" pid="$2" version="$3" state="${4:-running}"
mkdir -p "$HOME/.xgent"
connected=false
if [[ "$component" == agent || "$component" == guardian ]]; then connected=true; fi
printf '{"schema":1,"component":"%s","version":"%s","pid":%s,"updated_at":%s,"state":"%s","connected":%s}\n' \
  "$component" "$version" "$pid" "$(date +%s)" "$state" "$connected" \
  > "$HOME/.xgent/$component-health.json"
HEALTH
chmod +x "$FAKE_BIN/write-health"
cat > "$FAKE_BIN/sitecustomize.py" <<'PYTHON'
from pathlib import Path
import subprocess

_run = subprocess.run

def _fixture_run(args, *positional, **kwargs):
    values = list(args) if isinstance(args, (list, tuple)) else [str(args)]
    command = Path(str(values[0])).name if values else ""
    if command == "launchctl" and len(values) >= 3 and values[1] == "print":
        labels = {
            "com.xgent.agent": 12345,
            "com.xider.guardian": 12346,
            "com.xider.safari": 12347,
            "com.xider.siri": 12348,
        }
        pid = next((pid for label, pid in labels.items() if str(values[2]).endswith(label)), None)
        output = f"pid = {pid}\n" if pid else ""
        return subprocess.CompletedProcess(args, 0, stdout=output, stderr="")
    if command == "ps":
        return subprocess.CompletedProcess(args, 0, stdout="xider-fixture-process\n", stderr="")
    return _run(args, *positional, **kwargs)

subprocess.run = _fixture_run
PYTHON
cat > "$FAKE_BIN/ssh" <<'SSH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$@" >> "${XIDER_TEST_SSH_LOG:-/dev/null}"
printf 'SHARED_KEY=remote-fixture-secret\n'
printf 'MQTT_BROKER=broker.example.invalid\n'
printf 'MQTT_PORT=%s\n' "${XIDER_TEST_MQTT_PORT:-8883}"
printf 'MQTT_TLS=%s\n' "${XIDER_TEST_MQTT_TLS:-true}"
if [[ "${XIDER_TEST_EMPTY_MQTT_USERNAME:-0}" == 1 ]]; then printf 'MQTT_USERNAME=\n'; else printf 'MQTT_USERNAME=test-user\n'; fi
if [[ "${XIDER_TEST_EMPTY_MQTT_PASSWORD:-0}" == 1 ]]; then printf 'MQTT_PASSWORD=\n'; else printf 'MQTT_PASSWORD=test-password-not-real\n'; fi
printf 'ENCRYPT_PAYLOAD=%s\n' "${XIDER_TEST_ENCRYPT_PAYLOAD:-true}"
if [[ "${XIDER_TEST_NO_PUBLIC_KEYSET:-0}" != 1 ]]; then
  printf 'XIDER_COMMAND_VERIFY_KEYSET_B64=eyIzMDk3ZTJkZWUyY2I0YTM0IjoiSVZMNDBadDVIU1JGTWtMaFh5NnJiTGZQLW50cVh0TUFsNVlPQnBpQjJ4SSJ9\n'
fi
printf 'SERVER_PRIVATE_SENTINEL=must-not-transfer\n'
if [[ "${XIDER_TEST_DUPLICATE_MQTT_TLS:-0}" == 1 ]]; then printf 'MQTT_TLS=true\nMQTT_TLS=false\n'; fi
if [[ "${XIDER_TEST_BLANK_PREFIX:-0}" == 1 ]]; then
  printf 'MQTT_PREFIX=\n'
fi
if [[ "${XIDER_TEST_BAD_PREFIX:-0}" == 1 ]]; then
  printf 'MQTT_PREFIX=not a valid prefix\n'
fi
if [[ "${XIDER_TEST_DUPLICATE_PREFIX:-0}" == 1 ]]; then
  printf 'MQTT_PREFIX=xgent/v1\nMQTT_PREFIX=xgent/v2\n'
fi
SSH
chmod +x "$FAKE_BIN/ssh"
cat > "$FAKE_BIN/unzip" <<'UNZIP'
#!/usr/bin/env python3
from pathlib import Path
from zipfile import ZipFile
import sys

args = sys.argv[1:]
archive = args[args.index("-q") + 1]
destination = args[args.index("-d") + 1]
with ZipFile(archive) as bundle:
    bundle.extractall(Path(destination))
UNZIP
chmod +x "$FAKE_BIN/unzip"

cat > "$FAKE_BIN/curl" <<'CURL'
#!/usr/bin/env bash
set -euo pipefail
destination=''
url=''
while (($#)); do
  case "$1" in
    -o) shift; destination="$1" ;;
    https://*) url="$1" ;;
  esac
  shift
done
printf '%s\n' "$url" >> "${XIDER_TEST_CURL_LOG:-/dev/null}"
if [[ "${XIDER_TEST_CURL_TRUNCATE_FIRST:-0}" == 1 ]]; then
  attempts=0
  if [[ -f "${XIDER_TEST_CURL_ATTEMPT_FILE:?}" ]]; then
    attempts="$(<"${XIDER_TEST_CURL_ATTEMPT_FILE:?}")"
  fi
  attempts=$((attempts + 1))
  printf '%s\n' "$attempts" > "${XIDER_TEST_CURL_ATTEMPT_FILE:?}"
  if ((attempts == 1)); then
    printf 'truncated archive\n' > "$destination"
    exit 0
  fi
fi
cp "${XIDER_TEST_ARCHIVE_PATH:?}" "$destination"
CURL
chmod +x "$FAKE_BIN/curl"

cat > "$SOURCE_ROOT/XGENT-MCS/start_agent.sh" <<'AGENT'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == --status ]]; then exit 0; fi
if [[ -f new-version.marker && "${XIDER_FAIL_NEW_AGENT:-0}" == 1 ]]; then exit 17; fi
write-health agent 12345 4.3.8
exit 0
AGENT
cat > "$SOURCE_ROOT/XGENT-MCS/start_guardian.sh" <<'KEEPER'
#!/usr/bin/env bash
write-health guardian 12346 4.3.8
exit 0
KEEPER
cat > "$SOURCE_ROOT/XGENT-MCS/start_safari.sh" <<'SAFARI'
#!/usr/bin/env bash
write-health safari 12347 0.1.0
exit 0
SAFARI
cat > "$SOURCE_ROOT/XGENT-MCS/start_siri.sh" <<'SIRI'
#!/usr/bin/env bash
write-health siri 12348 0.1.0
exit 0
SIRI
printf '#!/usr/bin/env bash\nexit 0\n' > "$SOURCE_ROOT/XGENT-MCS/stop_agent.sh"
printf 'new\n' > "$SOURCE_ROOT/XGENT-MCS/new-version.marker"
printf '# fixture dependencies\n' > "$SOURCE_ROOT/XGENT-MCS/requirements.txt"
printf 'VERSION = "4.3.8"\n' > "$SOURCE_ROOT/XGENT-MCS/config.py"
printf 'VERSION = "0.1.0"\n' > "$SOURCE_ROOT/XGENT-MCS/xider_layers.py"
mkdir -p "$SOURCE_ROOT/deploy"
cp "$ROOT/deploy/install_report.py" "$SOURCE_ROOT/deploy/install_report.py"
chmod +x "$SOURCE_ROOT/XGENT-MCS/start_agent.sh" \
  "$SOURCE_ROOT/XGENT-MCS/start_guardian.sh" \
  "$SOURCE_ROOT/XGENT-MCS/start_safari.sh" \
  "$SOURCE_ROOT/XGENT-MCS/start_siri.sh" \
  "$SOURCE_ROOT/XGENT-MCS/stop_agent.sh"

python3 - "$SOURCE_ROOT" "$ARCHIVE" <<'PY'
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
import sys

source, archive = Path(sys.argv[1]), Path(sys.argv[2])
with ZipFile(archive, "w", ZIP_DEFLATED) as bundle:
    for path in source.rglob("*"):
        if path.is_file():
            bundle.write(path, Path("XIDER-fixture") / path.relative_to(source))
PY

make_old_install() {
  local install_root="$1"
  mkdir -p "$install_root/git-ver/XGENT-MCS"
  printf 'old\n' > "$install_root/git-ver/XGENT-MCS/old-version.marker"
  mkdir -p "$install_root/runtimes/old/bin"
  cp "$FAKE_BIN/python3" "$install_root/runtimes/old/bin/python3"
  ln -s "$install_root/runtimes/old" "$install_root/git-ver/XGENT-MCS/venv"
  cat > "$install_root/git-ver/XGENT-MCS/start_agent.sh" <<'OLD_AGENT'
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
[[ "${1:-}" == --status ]] && exit 0
if [[ "${XIDER_RUNTIME_PREPARED:-0}" != 1 ]]; then
  ./venv/bin/python3 -m pip install -r requirements.txt
fi
touch old-worker-restarted.marker
OLD_AGENT
  printf '#!/usr/bin/env bash\nexit 0\n' > "$install_root/git-ver/XGENT-MCS/start_guardian.sh"
  printf '#!/usr/bin/env bash\nexit 0\n' > "$install_root/git-ver/XGENT-MCS/stop_agent.sh"
  printf 'SHARED_KEY=old-local-secret\nMQTT_BROKER=broker.example.invalid\nMQTT_PORT=8883\nMQTT_PREFIX=xgent/v1\nMQTT_TLS=true\nMQTT_USERNAME=test-user\nMQTT_PASSWORD=test-password-not-real\nENCRYPT_PAYLOAD=true\nXIDER_UPDATE_BRANCH=main\n' > "$install_root/git-ver/XGENT-MCS/.env"
  printf 'DEVICE_ID=local-device\nUNRELATED=preserve-setting\n' >> "$install_root/git-ver/XGENT-MCS/.env"
  chmod +x "$install_root/git-ver/XGENT-MCS/start_agent.sh" \
    "$install_root/git-ver/XGENT-MCS/start_guardian.sh" \
    "$install_root/git-ver/XGENT-MCS/stop_agent.sh"
}

run_bootstrap() {
  local install_root="$1" fail_new="$2" env_root="${3:-$FIXTURE/home/XIDER}"
  HOME="$FIXTURE/home" \
  USERPROFILE="$FIXTURE/home" \
  PATH="$FAKE_BIN:$PATH" \
  PYTHONPATH="$FAKE_BIN" \
  XIDER_INSTALL_ROOT="$install_root" \
  XIDER_ENV_ROOT="$env_root" \
  XIDER_SOURCE_ARCHIVE="$ARCHIVE" \
  XIDER_SSH_KEY="${XIDER_TEST_SSH_KEY:-}" \
  XIDER_TEST_SSH_LOG="$FIXTURE/ssh-args.log" \
  XIDER_FAIL_NEW_AGENT="$fail_new" \
    bash "$BOOTSTRAP"
}

run_network_bootstrap() {
  local install_root="$1" ref="$2"
  HOME="$FIXTURE/home" \
  USERPROFILE="$FIXTURE/home" \
  PATH="$FAKE_BIN:$PATH" \
  PYTHONPATH="$FAKE_BIN" \
  XIDER_INSTALL_ROOT="$install_root" \
  XIDER_ENV_ROOT="$FIXTURE/home/XIDER" \
  XIDER_SOURCE_ARCHIVE='' \
  XIDER_REF="$ref" \
  XIDER_TEST_ARCHIVE_PATH="$ARCHIVE" \
  XIDER_TEST_CURL_LOG="$FIXTURE/curl-urls.log" \
    bash "$BOOTSTRAP"
}

reject_remote_config() {
  local label="$1" root="$FIXTURE/reject-$1"
  shift
  mkdir -p "$root/git-ver"
  printf 'old-checkout\n' > "$root/git-ver/old-marker.txt"
  if (
    export "$@"
    run_bootstrap "$root" 0 "$FIXTURE/no-local-env" > "$FIXTURE/reject-$label.log" 2>&1
  ); then
    echo "Unsafe MQTT configuration unexpectedly passed: $label" >&2
    exit 1
  fi
  [[ -f "$root/git-ver/old-marker.txt" ]] || {
    echo "Unsafe MQTT configuration changed the active checkout: $label" >&2
    exit 1
  }
}

MALICIOUS_ROOT="$FIXTURE/malicious-install"
mkdir -p "$MALICIOUS_ROOT/git-ver/XGENT-MCS"
printf 'old-checkout\n' > "$MALICIOUS_ROOT/git-ver/old-marker.txt"
MALICIOUS_ARCHIVE="$FIXTURE/traversal.zip"
ESCAPE_NAME="xider-macos-bootstrap-escape-$RANDOM-$$.txt"
python3 - "$MALICIOUS_ARCHIVE" "$ESCAPE_NAME" <<'PY'
from zipfile import ZipFile
import sys

with ZipFile(sys.argv[1], "w") as archive:
    archive.writestr("../" + sys.argv[2], "must not escape staging")
PY
SAFE_ARCHIVE="$ARCHIVE"
ARCHIVE="$MALICIOUS_ARCHIVE"
if run_bootstrap "$MALICIOUS_ROOT" 0 > "$FIXTURE/traversal.log" 2>&1; then
  echo 'The macOS bootstrap unexpectedly accepted a traversal archive.' >&2
  exit 1
fi
[[ -f "$MALICIOUS_ROOT/git-ver/old-marker.txt" && ! -e "$MALICIOUS_ROOT/$ESCAPE_NAME" ]] || {
  echo 'The macOS bootstrap changed the active checkout or extracted outside staging.' >&2
  cat "$FIXTURE/traversal.log" >&2
  exit 1
}
ARCHIVE="$SAFE_ARCHIVE"

ROLLBACK_ROOT="$FIXTURE/rollback-install"
make_old_install "$ROLLBACK_ROOT"
: > "$XIDER_TEST_PIP_LOG"
if run_bootstrap "$ROLLBACK_ROOT" 1 > "$FIXTURE/rollback.log" 2>&1; then
  echo 'The simulated Mac activation error unexpectedly succeeded.' >&2
  exit 1
fi
[[ -f "$ROLLBACK_ROOT/git-ver/XGENT-MCS/old-version.marker" ]] || {
  echo 'The old macOS checkout was not restored.' >&2; exit 1;
}
[[ -f "$ROLLBACK_ROOT/git-ver/XGENT-MCS/old-worker-restarted.marker" ]] || {
  echo 'The old worker was not restarted after activation rollback.' >&2
  cat "$FIXTURE/rollback.log" >&2
  find "$ROLLBACK_ROOT" -maxdepth 5 -print >&2
  exit 1
}
if grep -Fq '/venv/bin/python3' "$XIDER_TEST_PIP_LOG" ||
   grep -Fq '/runtimes/old/' "$XIDER_TEST_PIP_LOG"; then
  echo 'Rollback invoked pip in the previously working runtime.' >&2; exit 1;
fi
grep -qx 'SHARED_KEY=old-local-secret' "$ROLLBACK_ROOT/git-ver/XGENT-MCS/.env" &&
grep -qx 'MQTT_PREFIX=xgent/v1' "$ROLLBACK_ROOT/git-ver/XGENT-MCS/.env" || {
  echo 'The previous macOS configuration was not restored.' >&2; exit 1;
}
failed_dirs=("$ROLLBACK_ROOT"/git-ver.failed.*)
[[ -d "${failed_dirs[0]}" && ! -e "${failed_dirs[0]}/XGENT-MCS/.env" ]] || {
  echo 'Failed Mac checkout was not retained without its copied .env.' >&2
  cat "$FIXTURE/rollback.log" >&2
  find "$ROLLBACK_ROOT" -maxdepth 4 -print >&2
  exit 1
}

SUCCESS_ROOT="$FIXTURE/success-install"
make_old_install "$SUCCESS_ROOT"
run_bootstrap "$SUCCESS_ROOT" 0 > "$FIXTURE/success.log" 2>&1
[[ -f "$SUCCESS_ROOT/git-ver/XGENT-MCS/new-version.marker" ]] || {
  echo 'The new macOS version was not activated.' >&2; exit 1;
}
grep -qx 'SHARED_KEY=old-local-secret' "$SUCCESS_ROOT/git-ver/XGENT-MCS/.env" || {
  echo 'The current macOS configuration was not carried forward.' >&2; exit 1;
}
"$REAL_PYTHON" - "$FIXTURE/home/.xgent/logs/install-report.json" <<'PY'
import json, sys
from pathlib import Path
report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert report["outcome"] == "succeeded", report
assert report["components"]["agent"]["version"] == "4.3.8", report
assert report["components"]["guardian"]["version"] == "4.3.8", report
assert report["components"]["safari"]["version"] == "0.1.0", report
assert report["components"]["siri"]["version"] == "0.1.0", report
assert all(report["components"][name]["process"]["running"] for name in ("agent", "guardian", "safari", "siri")), report
assert all(report["components"][name]["bot_response"]["status"] == "not_verified" for name in report["components"]), report
PY
grep -qx 'XIDER_COMMAND_VERIFY_KEYSET_B64=eyIzMDk3ZTJkZWUyY2I0YTM0IjoiSVZMNDBadDVIU1JGTWtMaFh5NnJiTGZQLW50cVh0TUFsNVlPQnBpQjJ4SSJ9' "$SUCCESS_ROOT/git-ver/XGENT-MCS/.env" || {
  echo 'Existing macOS install did not refresh only its public keyset.' >&2; exit 1;
}
grep -qx 'DEVICE_ID=local-device' "$SUCCESS_ROOT/git-ver/XGENT-MCS/.env" &&
grep -qx 'UNRELATED=preserve-setting' "$SUCCESS_ROOT/git-ver/XGENT-MCS/.env" &&
grep -qx 'MQTT_PASSWORD=test-password-not-real' "$SUCCESS_ROOT/git-ver/XGENT-MCS/.env" || {
  echo 'Public keyset refresh changed existing device secrets/settings.' >&2; exit 1;
}
if grep -q '^XIDER_UPDATE_BRANCH=' "$SUCCESS_ROOT/git-ver/XGENT-MCS/.env"; then
  echo 'The obsolete mutable update-branch setting was recorded in the active configuration.' >&2; exit 1;
fi
backups=("$SUCCESS_ROOT"/git-ver.previous.*)
[[ -d "${backups[0]}" && -f "${backups[0]}/XGENT-MCS/old-version.marker" ]] || {
  echo 'The previous macOS version was not kept as a rollback copy.' >&2; exit 1;
}
if [[ -L "$SUCCESS_ROOT/git-ver/XGENT-MCS/venv" ]]; then
  [[ -x "$SUCCESS_ROOT/git-ver/XGENT-MCS/venv/bin/python3" ]] || {
    echo 'The retained macOS runtime link is broken.' >&2; exit 1;
  }
elif [[ "$(uname -s)" == MINGW* && -x "$SUCCESS_ROOT/git-ver/XGENT-MCS/venv/bin/python3" ]]; then
  retained_runtime_found=0
  while IFS= read -r retained_runtime; do
    if cmp -s "$SUCCESS_ROOT/git-ver/XGENT-MCS/venv/bin/python3" "$retained_runtime"; then
      retained_runtime_found=1
      break
    fi
  done < <(find "$SUCCESS_ROOT/runtimes" -maxdepth 3 -type f -path '*/bin/python3' -print)
  [[ "$retained_runtime_found" == 1 ]] || {
    echo 'The Windows Bash fixture did not preserve its non-link runtime copy.' >&2; exit 1;
  }
else
  echo 'The prepared runtime was not retained at a stable path.' >&2; exit 1;
fi

DEPENDENCY_ROOT="$FIXTURE/dependency-failure"
make_old_install "$DEPENDENCY_ROOT"
if XIDER_FAIL_DEPENDENCIES=1 run_bootstrap "$DEPENDENCY_ROOT" 0 > "$FIXTURE/dependency.log" 2>&1; then
  echo 'Dependency preflight failure unexpectedly activated a new Mac checkout.' >&2; exit 1;
fi
[[ -f "$DEPENDENCY_ROOT/git-ver/XGENT-MCS/old-version.marker" &&
   ! -e "$DEPENDENCY_ROOT/git-ver/XGENT-MCS/new-version.marker" ]] || {
  echo 'Dependency preflight failure changed the working installation.' >&2; exit 1;
}
if compgen -G "$DEPENDENCY_ROOT/git-ver.previous.*" >/dev/null; then
  echo 'The working checkout was moved before dependencies passed.' >&2; exit 1;
fi

PINNED_ROOT="$FIXTURE/pinned-install"
PINNED_REF="$(printf 'c%.0s' {1..40})"
make_old_install "$PINNED_ROOT"
: > "$FIXTURE/curl-urls.log"
run_network_bootstrap "$PINNED_ROOT" "$PINNED_REF" > "$FIXTURE/pinned.log" 2>&1
grep -Fx "https://github.com/invinby/XIDER/archive/${PINNED_REF}.zip" "$FIXTURE/curl-urls.log" >/dev/null || {
  echo 'macOS bootstrap did not request the exact pinned commit archive.' >&2
  cat "$FIXTURE/pinned.log" >&2
  exit 1
}
[[ -f "$PINNED_ROOT/git-ver/XGENT-MCS/new-version.marker" ]] || {
  echo 'The pinned macOS archive was not activated.' >&2; exit 1;
}

RETRY_ROOT="$FIXTURE/retry-source-download"
RETRY_REF="$(printf 'd%.0s' {1..40})"
RETRY_ATTEMPTS="$FIXTURE/retry-curl-attempts"
make_old_install "$RETRY_ROOT"
: > "$FIXTURE/curl-urls.log"
XIDER_TEST_CURL_TRUNCATE_FIRST=1 \
XIDER_TEST_CURL_ATTEMPT_FILE="$RETRY_ATTEMPTS" \
  run_network_bootstrap "$RETRY_ROOT" "$RETRY_REF" > "$FIXTURE/retry-download.log" 2>&1
[[ "$(<"$RETRY_ATTEMPTS")" == 2 ]] || {
  echo 'The macOS bootstrap did not retry a truncated but successful HTTP response.' >&2
  cat "$FIXTURE/retry-download.log" >&2
  exit 1
}
[[ -f "$RETRY_ROOT/git-ver/XGENT-MCS/new-version.marker" ]] || {
  echo 'The verified retry did not activate the new macOS checkout.' >&2
  cat "$FIXTURE/retry-download.log" >&2
  exit 1
}

INVALID_REF_ROOT="$FIXTURE/invalid-ref-install"
make_old_install "$INVALID_REF_ROOT"
if run_network_bootstrap "$INVALID_REF_ROOT" main > "$FIXTURE/invalid-ref.log" 2>&1; then
  echo 'A mutable branch was unexpectedly accepted as XIDER_REF.' >&2
  exit 1
fi
[[ -f "$INVALID_REF_ROOT/git-ver/XGENT-MCS/old-version.marker" ]] || {
  echo 'An invalid XIDER_REF changed the active macOS checkout.' >&2; exit 1;
}

FIRST_ROOT="$FIXTURE/first-install"
CONFIG_ROOT="$FIXTURE/config"
mkdir -p "$CONFIG_ROOT/XGENT-MCS"
printf 'SHARED_KEY=first-install-secret\nMQTT_BROKER=broker.example.invalid\nMQTT_PORT=8883\nMQTT_PREFIX=xgent/v1\nMQTT_TLS=true\nMQTT_USERNAME=test-user\nMQTT_PASSWORD=test-password-not-real\nENCRYPT_PAYLOAD=true\n' > "$CONFIG_ROOT/XGENT-MCS/.env"
if run_bootstrap "$FIRST_ROOT" 1 "$CONFIG_ROOT" > "$FIXTURE/first-install.log" 2>&1; then
  echo 'The simulated failed first Mac install unexpectedly succeeded.' >&2
  exit 1
fi
partial=("$FIRST_ROOT"/git-ver.failed.*)
[[ -d "${partial[0]}" && ! -e "${partial[0]}/XGENT-MCS/.env" ]] || {
  echo 'Failed first Mac install retained its copied .env.' >&2; exit 1;
}

REMOTE_ROOT="$FIXTURE/remote-config-install"
mkdir -p "$FIXTURE/home/.ssh"
printf 'not-a-real-private-key\n' > "$FIXTURE/home/.ssh/xider"
XIDER_TEST_SSH_KEY="$FIXTURE/home/.ssh/xider" run_bootstrap "$REMOTE_ROOT" 0 "$FIXTURE/no-local-env" > "$FIXTURE/remote-config.log" 2>&1
remote_env="$REMOTE_ROOT/git-ver/XGENT-MCS/.env"
grep -qx 'XIDER_COMMAND_VERIFY_KEYSET_B64=eyIzMDk3ZTJkZWUyY2I0YTM0IjoiSVZMNDBadDVIU1JGTWtMaFh5NnJiTGZQLW50cVh0TUFsNVlPQnBpQjJ4SSJ9' "$remote_env" || {
  echo 'Public command keyset did not reach the macOS agent.' >&2; exit 1;
}
if grep -q 'SERVER_PRIVATE_SENTINEL\|PRIVATE KEY' "$remote_env" ||
   grep -q 'command-signing-private' "$FIXTURE/ssh-args.log"; then
  echo 'Server private material was requested or transferred.' >&2; exit 1;
fi
NO_KEY_ROOT="$FIXTURE/no-public-keyset-install"
XIDER_TEST_NO_PUBLIC_KEYSET=1 run_bootstrap "$NO_KEY_ROOT" 0 "$FIXTURE/no-local-env" > "$FIXTURE/no-public.log" 2>&1
[[ -f "$NO_KEY_ROOT/git-ver/XGENT-MCS/new-version.marker" ]] &&
! grep -q '^XIDER_COMMAND_VERIFY_KEYSET_B64=' "$NO_KEY_ROOT/git-ver/XGENT-MCS/.env" || {
  echo 'Missing public keyset broke ordinary install or enabled shell.' >&2; exit 1;
}
grep -qx 'MQTT_PREFIX=xgent/v1' "$remote_env" || {
  echo 'Missing server prefix was not normalized to the shared default.' >&2
  cat "$FIXTURE/remote-config.log" >&2
  exit 1
}
if [[ "$(uname -s)" != MINGW* && "$(stat -c '%a' "$remote_env")" != 600 ]]; then
  echo 'Fetched macOS .env is not private (0600).' >&2; exit 1;
fi
grep -Fx -- '-i' "$FIXTURE/ssh-args.log" >/dev/null &&
grep -Fx -- "$FIXTURE/home/.ssh/xider" "$FIXTURE/ssh-args.log" >/dev/null &&
grep -Fx -- 'IdentitiesOnly=yes' "$FIXTURE/ssh-args.log" >/dev/null || {
  echo 'macOS bootstrap did not pass the configured SSH key to OpenSSH.' >&2; exit 1;
}
grep -Fx -- 'BatchMode=yes' "$FIXTURE/ssh-args.log" >/dev/null || {
  echo 'macOS bootstrap must never prompt for a VPS password.' >&2; exit 1;
}
grep -Fx -- 'PreferredAuthentications=publickey' "$FIXTURE/ssh-args.log" >/dev/null &&
grep -Fx -- 'PasswordAuthentication=no' "$FIXTURE/ssh-args.log" >/dev/null &&
grep -Fx -- 'KbdInteractiveAuthentication=no' "$FIXTURE/ssh-args.log" >/dev/null || {
  echo 'macOS bootstrap must allow only pre-authorized public-key authentication.' >&2; exit 1;
}
if grep -Eq '^(PubkeyAuthentication=no|PreferredAuthentications=password)$' "$FIXTURE/ssh-args.log"; then
  echo 'macOS bootstrap disabled SSH public-key authentication.' >&2; exit 1;
fi

DEFAULT_KEY_ROOT="$FIXTURE/default-key-install"
: > "$FIXTURE/ssh-args.log"
run_bootstrap "$DEFAULT_KEY_ROOT" 0 "$FIXTURE/no-local-env" > "$FIXTURE/default-key.log" 2>&1
grep -Fx -- '-i' "$FIXTURE/ssh-args.log" >/dev/null &&
grep -Fx -- "$FIXTURE/home/.ssh/xider" "$FIXTURE/ssh-args.log" >/dev/null || {
  echo 'macOS bootstrap did not discover ~/.ssh/xider by default.' >&2; exit 1;
}

reject_remote_config insecure-tls XIDER_TEST_MQTT_TLS=false
reject_remote_config clear-payload XIDER_TEST_ENCRYPT_PAYLOAD=false
reject_remote_config no-mqtt-user XIDER_TEST_EMPTY_MQTT_USERNAME=1
reject_remote_config no-mqtt-password XIDER_TEST_EMPTY_MQTT_PASSWORD=1
reject_remote_config duplicate-tls XIDER_TEST_DUPLICATE_MQTT_TLS=1
reject_remote_config invalid-port XIDER_TEST_MQTT_PORT=70000

BLANK_ROOT="$FIXTURE/blank-prefix-install"
mkdir -p "$BLANK_ROOT/git-ver/XGENT-MCS"
printf 'old-checkout\n' > "$BLANK_ROOT/git-ver/old-marker.txt"
if XIDER_TEST_BLANK_PREFIX=1 run_bootstrap "$BLANK_ROOT" 0 "$FIXTURE/no-local-env" > "$FIXTURE/blank-prefix.log" 2>&1; then
  echo 'An explicitly blank MQTT_PREFIX unexpectedly passed bootstrap.' >&2
  exit 1
fi
[[ -f "$BLANK_ROOT/git-ver/old-marker.txt" ]] || {
  echo 'Blank-prefix rejection changed the active checkout.' >&2; exit 1;
}

BAD_ROOT="$FIXTURE/invalid-prefix-install"
mkdir -p "$BAD_ROOT/git-ver/XGENT-MCS"
printf 'old-checkout\n' > "$BAD_ROOT/git-ver/old-marker.txt"
if XIDER_TEST_BAD_PREFIX=1 run_bootstrap "$BAD_ROOT" 0 "$FIXTURE/no-local-env" > "$FIXTURE/invalid-prefix.log" 2>&1; then
  echo 'An MQTT_PREFIX containing whitespace unexpectedly passed bootstrap.' >&2
  exit 1
fi
[[ -f "$BAD_ROOT/git-ver/old-marker.txt" ]] || {
  echo 'Invalid-prefix rejection changed the active checkout.' >&2; exit 1;
}

DUPLICATE_ROOT="$FIXTURE/duplicate-prefix-install"
mkdir -p "$DUPLICATE_ROOT/git-ver/XGENT-MCS"
printf 'old-checkout\n' > "$DUPLICATE_ROOT/git-ver/old-marker.txt"
if XIDER_TEST_DUPLICATE_PREFIX=1 run_bootstrap "$DUPLICATE_ROOT" 0 "$FIXTURE/no-local-env" > "$FIXTURE/duplicate-prefix.log" 2>&1; then
  echo 'Duplicate MQTT_PREFIX values unexpectedly passed bootstrap.' >&2
  exit 1
fi
[[ -f "$DUPLICATE_ROOT/git-ver/old-marker.txt" ]] || {
  echo 'Duplicate-prefix rejection changed the active checkout.' >&2; exit 1;
}

echo 'macOS bootstrap activation, config preservation, prefix validation, and rollback fixture passed.'
