import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.x_map_audit import build_report, main


def _project(tmp_path):
    (tmp_path / "TG-BOT-SERVER").mkdir()
    (tmp_path / "XGENT-WDS").mkdir()
    (tmp_path / "XGENT-MCS").mkdir()
    (tmp_path / "TG-BOT-SERVER" / "bot.py").write_text(
        'CALLBACK = "cmd:check_update"\n'
        'OTHER = "cmd:status"\n'
        'publish_tracked("check_update")\n'
        'transport.publish_command("device", "status")\n'
        'simple_command(cq, "guardian", "shield", "Keeper")\n',
        encoding="utf-8",
    )
    (tmp_path / "XGENT-WDS" / "xgent_wds.py").write_text(
        'SUPPORTED_COMMANDS = {"agent_update", "status", "windows_only"}\n'
        'handlers = {"agent_update": self._do_update, "status": self._do_status}\n',
        encoding="utf-8",
    )
    (tmp_path / "XGENT-MCS" / "xgent_mcs.py").write_text(
        'SUPPORTED_COMMANDS = {"agent_update", "status", "mac_only"}\n'
        'handlers = {"agent_update": self._do_update, "status": self._do_status}\n',
        encoding="utf-8",
    )
    guardian_source = (
        'def handle(self, payload):\n'
        '    command = payload["command"]\n'
        '    if command == "status": pass\n'
        '    elif command == "start": pass\n'
        '    elif command == "stop": pass\n'
    )
    (tmp_path / "XGENT-WDS" / "xider_guardian_wds.py").write_text(guardian_source, encoding="utf-8")
    (tmp_path / "XGENT-MCS" / "xider_guardian.py").write_text(guardian_source, encoding="utf-8")


def test_report_maps_alias_and_exposes_platform_gaps(tmp_path):
    _project(tmp_path)

    report = build_report(tmp_path)

    coverage = report["action_agent_coverage"]
    assert coverage["covered_on_both"] == ["agent_update", "status"]
    assert report["callback_count"] == 2
    assert report["platform_parity"] == {
        "windows_only": ["windows_only"],
        "macos_only": ["mac_only"],
        "shared_count": 2,
    }
    assert report["guardian_commands"]["windows"] == ["start", "status", "stop"]
    assert report["guardian_coverage"] == {
        "required": True,
        "missing_on_windows": [],
        "missing_on_macos": [],
    }
    assert report["source_only"] is True


def test_report_identifies_declared_commands_without_handlers(tmp_path):
    _project(tmp_path)
    path = tmp_path / "XGENT-WDS" / "xgent_wds.py"
    source = path.read_text(encoding="utf-8").replace(
        '"windows_only"}', '"windows_only", "orphan"}'
    )
    source = source.replace('"status": self._do_status}', '"status": self._do_status, "windows_only": self._do_platform}')
    path.write_text(source, encoding="utf-8")

    assert build_report(tmp_path)["platforms"]["windows"]["declared_without_handler"] == ["orphan"]


def test_cli_writes_json_report(tmp_path, monkeypatch):
    _project(tmp_path)
    output = tmp_path / "report" / "x-map.json"
    monkeypatch.setattr(sys, "argv", ["x-map-audit", "--root", str(tmp_path), "--output", str(output)])

    assert main() == 0
    assert json.loads(output.read_text(encoding="utf-8"))["schema"] == "x-map-static-audit-v2"


def test_current_repo_has_no_declared_worker_action_gaps():
    report = build_report(ROOT)

    assert report["action_agent_coverage"]["missing_on_windows"] == []
    assert report["action_agent_coverage"]["missing_on_macos"] == []
    for platform in ("windows", "macos"):
        assert report["platforms"][platform]["declared_without_handler"] == []
        assert report["platforms"][platform]["handler_without_declaration"] == []
    assert report["guardian_coverage"] == {
        "required": True,
        "missing_on_windows": [],
        "missing_on_macos": [],
    }
