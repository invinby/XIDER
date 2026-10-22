import pytest

from shell_dry_run import build_dry_run_command


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("git clean -fd", "git clean -n -fd"),
        ("git clean -fd -- build/cache", "git clean -n -fd -- build/cache"),
        ("rsync -a src/ backup/", "rsync --dry-run -a src/ backup/"),
        ("rsync --archive src/ backup/", "rsync --dry-run --archive src/ backup/"),
        ("rsync src/ backup/", "rsync --dry-run src/ backup/"),
    ],
)
def test_builds_native_dry_run_for_allowlisted_commands(command, expected):
    assert build_dry_run_command(command) == expected


@pytest.mark.parametrize(
    "command",
    [
        "git status --short",
        "git clean -nfd",
        "git clean -fd && echo done",
        "git clean -fd; echo done",
        "git clean -fd | cat",
        "git clean -fd 'folder with spaces'",
        "git clean --bogus -f",
        "git clean -fz",
        "rsync -n -a src/ backup/",
        "rsync --dry-run -a src/ backup/",
        "rsync -a src/ backup/; touch marker",
        "C:/Tools/rsync.exe -a src/ backup/",
    ],
)
def test_rejects_unsupported_or_already_dry_run_commands(command):
    assert build_dry_run_command(command) is None
