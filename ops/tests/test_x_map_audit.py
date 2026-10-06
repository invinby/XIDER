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
        '_CAPABILITY_LABELS = {"screenshot": "Screen", "geolocation": "Location"}\n'
        '_CAPABILITY_STATES = {"supported": "Supported", "permission_unverified": "Permission", "approximate": "Approximate"}\n'
        'CALLBACK = "cmd:check_update"\n'
        'OTHER = "cmd:status"\n'
        'publish_tracked("check_update")\n'
        'transport.publish_command("device", "status")\n'
        'simple_command(cq, "guardian", "shield", "Keeper")\n'
        'def publish(target, action, **kwargs):\n'
        '    return transport.publish_command(target, action, **kwargs)\n'
        'def on_repeat(target, action, **kwargs):\n'
        '    return transport.publish_command(target, action, **kwargs)\n'
        'async def _simple_command_unlocked(action):\n'
        '    return publish_tracked(action)\n',
        encoding="utf-8",
    )
    (tmp_path / "XGENT-WDS" / "xgent_wds.py").write_text(
        'SUPPORTED_COMMANDS = {"agent_update", "status", "windows_only"}\n'
        'handlers = {"agent_update": self._do_update, "status": self._do_status}\n'
        'def _detect_feature_status():\n'
        '    return {"screenshot": "permission_unverified", "geolocation": "approximate"}\n',
        encoding="utf-8",
    )
    (tmp_path / "XGENT-MCS" / "xgent_mcs.py").write_text(
        'SUPPORTED_COMMANDS = {"agent_update", "status", "mac_only"}\n'
        'handlers = {"agent_update": self._do_update, "status": self._do_status}\n'
        'def _detect_feature_status():\n'
        '    return {"screenshot": "permission_unverified", "geolocation": "approximate"}\n',
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
    assert report["feature_status"]["expected_features"] == ["geolocation", "screenshot"]
    assert report["feature_status"]["allowed_states"] == ["approximate", "permission_unverified", "supported"]
    for platform in ("windows", "macos"):
        status = report["feature_status"]["platforms"][platform]
        assert status["keys"] == ["geolocation", "screenshot"]
        assert status["states"] == ["approximate", "permission_unverified"]
        assert status["missing_features"] == []
        assert status["unexpected_features"] == []
        assert status["unrecognized_states"] == []
    assert report["dynamic_action_calls_not_classified"] == 0
    assert [item["classification"] for item in report["dynamic_action_wrappers"]] == [
        "shared_publish_dispatch", "history_replay", "simple_command_dispatch",
    ]
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


def test_report_identifies_capability_schema_drift(tmp_path):
    _project(tmp_path)
    path = tmp_path / "XGENT-WDS" / "xgent_wds.py"
    source = path.read_text(encoding="utf-8").replace(
        '{"screenshot": "permission_unverified", "geolocation": "approximate"}',
        '{"screenshot": "permission_denied", "legacy_camera": "other"}',
    )
    path.write_text(source, encoding="utf-8")

    status = build_report(tmp_path)["feature_status"]["platforms"]["windows"]

    assert status["missing_features"] == ["geolocation"]
    assert status["unexpected_features"] == ["legacy_camera"]
    assert status["unrecognized_states"] == ["other", "permission_denied"]


def test_report_keeps_unknown_dynamic_dispatch_visible(tmp_path):
    _project(tmp_path)
    path = tmp_path / "TG-BOT-SERVER" / "bot.py"
    with path.open("a", encoding="utf-8") as stream:
        stream.write('def plugin_dispatch(action):\n    publish_tracked(action)\n')

    report = build_report(tmp_path)

    assert report["dynamic_action_calls_not_classified"] == 1
    assert report["dynamic_action_sites"][0]["function"] == "plugin_dispatch"


def test_cli_writes_json_report(tmp_path, monkeypatch):
    _project(tmp_path)
    output = tmp_path / "report" / "x-map.json"
    monkeypatch.setattr(sys, "argv", ["x-map-audit", "--root", str(tmp_path), "--output", str(output)])

    assert main() == 0
    assert json.loads(output.read_text(encoding="utf-8"))["schema"] == "x-map-static-audit-v5"


def test_cli_writes_matrix_only(tmp_path, monkeypatch):
    _project(tmp_path)
    output = tmp_path / "report" / "matrix.json"
    monkeypatch.setattr(
        sys, "argv", ["x-map-audit", "--root", str(tmp_path), "--matrix-only", "--output", str(output)]
    )

    assert main() == 0
    matrix = json.loads(output.read_text(encoding="utf-8"))
    assert matrix["worker_command_count"] == 4
    assert "schema" not in matrix


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
    assert report["dynamic_action_calls_not_classified"] == 0
    assert report["dynamic_action_sites"] == []
    assert [item["classification"] for item in report["dynamic_action_wrappers"]] == [
        "shared_publish_dispatch", "history_replay",
        "prompted_text_prank_dispatch", "simple_command_dispatch", "xlex_prank_dispatch",
    ]
    assert report["feature_status"]["expected_features"] == sorted({
        "screenshot", "webcam", "microphone", "geolocation", "battery", "clipboard", "shell", "open_app",
    })
    for platform in ("windows", "macos"):
        assert report["feature_status"]["platforms"][platform]["keys"] == report["feature_status"]["expected_features"]
        assert report["feature_status"]["platforms"][platform]["missing_features"] == []
        assert report["feature_status"]["platforms"][platform]["unexpected_features"] == []
        assert report["feature_status"]["platforms"][platform]["unrecognized_states"] == []


def test_command_matrix_records_source_support_permissions_and_stability_for_every_command():
    report = build_report(ROOT)
    matrix = report["command_matrix"]
    expected_worker = (
        (set(report["bot_actions"]) - {"guardian"})
        | set(report["platforms"]["windows"]["supported"])
        | set(report["platforms"]["windows"]["handlers"])
        | set(report["platforms"]["macos"]["supported"])
        | set(report["platforms"]["macos"]["handlers"])
    )
    rows = {row["command"]: row for row in matrix["worker_commands"]}

    assert matrix["source_only"] is True
    assert matrix["worker_command_count"] == len(expected_worker) == len(rows)
    assert set(rows) == expected_worker
    assert matrix["guardian_command_count"] == 5
    assert matrix["permission_profile_gaps"] == []
    for row in (*matrix["worker_commands"], *matrix["guardian_commands"]):
        assert set(row["platforms"]) == {"windows", "macos"}
        for platform_row in row["platforms"].values():
            assert platform_row["permission_profile_ids"]
            assert set(platform_row["permission_profile_ids"]) <= set(matrix["permission_profiles"])
            assert platform_row["stability"]["state"] in {
                "source_only_unverified", "reported_issue_not_retested",
                "known_source_limited", "dependency_gated_unverified", "source_gap",
            }
            assert platform_row["source_behavior"]["state"]
            assert platform_row["source_behavior"]["evidence"]
            assert platform_row["stability"]["evidence"]

    assert "screen_capture" in rows["screenshot"]["platforms"]["macos"]["permission_profile_ids"]
    assert "filesystem_delete" in rows["file_del"]["platforms"]["windows"]["permission_profile_ids"]
    assert "command_execution" in rows["shell"]["platforms"]["macos"]["permission_profile_ids"]
    assert "accessibility_input" in rows["type_text"]["platforms"]["macos"]["permission_profile_ids"]
    assert "app_automation" in rows["type_text"]["platforms"]["macos"]["permission_profile_ids"]
    assert rows["screenshot"]["platforms"]["macos"]["stability"]["state"] == "reported_issue_not_retested"
    assert rows["geo_location"]["platforms"]["macos"]["stability"]["state"] == "reported_issue_not_retested"
    assert rows["display_night_light"]["platforms"]["macos"]["source_behavior"]["state"] == "explicitly_unsupported"
    assert rows["display_night_light"]["platforms"]["macos"]["stability"]["state"] == "known_source_limited"
    assert rows["prank_random_clicks"]["platforms"]["macos"]["stability"]["state"] == "known_source_limited"
    assert rows["prank_invert_screen"]["platforms"]["macos"]["source_behavior"]["state"] == "fallback_only"
    assert rows["display_brightness"]["platforms"]["macos"]["stability"]["state"] == "dependency_gated_unverified"

    guardian_rows = {row["command"]: row for row in matrix["guardian_commands"]}
    assert set(guardian_rows) == {"auto_restart", "restart", "start", "status", "stop"}
    assert guardian_rows["stop"]["platforms"]["windows"]["source_support"] == "source_branch_present"


def test_source_limitation_overrides_still_match_mac_handlers():
    source = (ROOT / "XGENT-MCS" / "xgent_mcs.py").read_text(encoding="utf-8")

    for marker in (
        '"brightness"',
        '"displayplacer"',
        "Ночной свет macOS не имеет стабильного публичного CLI",
        "Для движения курсора нужен PyObjC Quartz и разрешение Accessibility",
        "Реальная инверсия экрана не применена",
        "Случайные клики не выполняются: функция не реализована безопасно",
        "Смена кнопок мыши отключена",
        "Глючный курсор отключён",
        "Кружение курсора недоступно в текущей сборке macOS",
    ):
        assert marker in source


def test_committed_command_matrix_snapshot_matches_current_sources():
    snapshot = ROOT / "docs" / "x-map-command-matrix.json"

    assert json.loads(snapshot.read_text(encoding="utf-8")) == build_report(ROOT)["command_matrix"]
