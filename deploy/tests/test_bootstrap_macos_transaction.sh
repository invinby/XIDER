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

printf '#!/usr/bin/env bash\nexit 0\n' > "$FAKE_BIN/launchctl"
chmod +x "$FAKE_BIN/launchctl"
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

cat > "$SOURCE_ROOT/XGENT-MCS/start_agent.sh" <<'AGENT'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == --status ]]; then exit 0; fi
if [[ -f new-version.marker && "${XIDER_FAIL_NEW_AGENT:-0}" == 1 ]]; then exit 17; fi
exit 0
AGENT
cat > "$SOURCE_ROOT/XGENT-MCS/start_guardian.sh" <<'KEEPER'
#!/usr/bin/env bash
exit 0
KEEPER
printf '#!/usr/bin/env bash\nexit 0\n' > "$SOURCE_ROOT/XGENT-MCS/stop_agent.sh"
printf 'new\n' > "$SOURCE_ROOT/XGENT-MCS/new-version.marker"
chmod +x "$SOURCE_ROOT/XGENT-MCS/start_agent.sh" \
  "$SOURCE_ROOT/XGENT-MCS/start_guardian.sh" "$SOURCE_ROOT/XGENT-MCS/stop_agent.sh"

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
  printf '#!/usr/bin/env bash\n[[ "${1:-}" == --status ]] && exit 0\nexit 0\n' > "$install_root/git-ver/XGENT-MCS/start_agent.sh"
  printf '#!/usr/bin/env bash\nexit 0\n' > "$install_root/git-ver/XGENT-MCS/start_guardian.sh"
  printf '#!/usr/bin/env bash\nexit 0\n' > "$install_root/git-ver/XGENT-MCS/stop_agent.sh"
  printf 'SHARED_KEY=old-local-secret\n' > "$install_root/git-ver/XGENT-MCS/.env"
  chmod +x "$install_root/git-ver/XGENT-MCS/start_agent.sh" \
    "$install_root/git-ver/XGENT-MCS/start_guardian.sh" \
    "$install_root/git-ver/XGENT-MCS/stop_agent.sh"
}

run_bootstrap() {
  local install_root="$1" fail_new="$2" env_root="${3:-$FIXTURE/home/XIDER}"
  HOME="$FIXTURE/home" \
  PATH="$FAKE_BIN:$PATH" \
  XIDER_INSTALL_ROOT="$install_root" \
  XIDER_ENV_ROOT="$env_root" \
  XIDER_SOURCE_ARCHIVE="$ARCHIVE" \
  XIDER_FAIL_NEW_AGENT="$fail_new" \
    bash "$BOOTSTRAP"
}

ROLLBACK_ROOT="$FIXTURE/rollback-install"
make_old_install "$ROLLBACK_ROOT"
if run_bootstrap "$ROLLBACK_ROOT" 1 > "$FIXTURE/rollback.log" 2>&1; then
  echo 'The simulated Mac activation error unexpectedly succeeded.' >&2
  exit 1
fi
[[ -f "$ROLLBACK_ROOT/git-ver/XGENT-MCS/old-version.marker" ]] || {
  echo 'The old macOS checkout was not restored.' >&2; exit 1;
}
[[ "$(cat "$ROLLBACK_ROOT/git-ver/XGENT-MCS/.env")" == 'SHARED_KEY=old-local-secret' ]] || {
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
grep -qx 'XIDER_UPDATE_BRANCH=main' "$SUCCESS_ROOT/git-ver/XGENT-MCS/.env" || {
  echo 'The macOS update branch was not recorded in the active configuration.' >&2; exit 1;
}
backups=("$SUCCESS_ROOT"/git-ver.previous.*)
[[ -d "${backups[0]}" && -f "${backups[0]}/XGENT-MCS/old-version.marker" ]] || {
  echo 'The previous macOS version was not kept as a rollback copy.' >&2; exit 1;
}

FIRST_ROOT="$FIXTURE/first-install"
CONFIG_ROOT="$FIXTURE/config"
mkdir -p "$CONFIG_ROOT/XGENT-MCS"
printf 'SHARED_KEY=first-install-secret\n' > "$CONFIG_ROOT/XGENT-MCS/.env"
if run_bootstrap "$FIRST_ROOT" 1 "$CONFIG_ROOT" > "$FIXTURE/first-install.log" 2>&1; then
  echo 'The simulated failed first Mac install unexpectedly succeeded.' >&2
  exit 1
fi
partial=("$FIRST_ROOT"/git-ver.failed.*)
[[ -d "${partial[0]}" && ! -e "${partial[0]}/XGENT-MCS/.env" ]] || {
  echo 'Failed first Mac install retained its copied .env.' >&2; exit 1;
}

echo 'macOS bootstrap activation, config preservation, and rollback fixture passed.'
