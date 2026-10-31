import json
from pathlib import Path

import pytest

from deploy.install_report import write_install_report


def _report():
    component = {
        "status": "installed",
        "version": "4.3.8",
        "process": {"running": True, "pid": 1234, "name": "XIDER Agent"},
        "bot_response": {
            "status": "not_verified",
            "detail": "MQTT connection alone does not prove bot receipt.",
        },
        "error": None,
    }
    return {
        "schema": 1,
        "install_id": "install-123",
        "installed_at": "2026-10-31T00:00:00Z",
        "platform": "windows",
        "requested_version": "4.3.8",
        "outcome": "succeeded",
        "components": {
            "agent": component,
            "guardian": component,
            "safari": {**component, "status": "not_applicable", "process": {"running": False, "pid": None, "name": None}},
            "siri": {**component, "status": "not_applicable", "process": {"running": False, "pid": None, "name": None}},
        },
    }


def test_write_install_report_keeps_four_component_results(tmp_path):
    report_path = tmp_path / "logs" / "install-report.json"

    write_install_report(report_path, _report())

    result = json.loads(report_path.read_text(encoding="utf-8"))
    assert set(result["components"]) == {"agent", "guardian", "safari", "siri"}
    assert result["components"]["agent"]["process"]["pid"] == 1234
    assert result["components"]["agent"]["bot_response"]["status"] == "not_verified"
    assert not list(report_path.parent.glob("*.tmp"))


def test_install_report_rejects_missing_component_without_replacing_previous_file(tmp_path):
    report_path = tmp_path / "install-report.json"
    report_path.write_text("previous", encoding="utf-8")
    report = _report()
    del report["components"]["siri"]

    with pytest.raises(ValueError, match="four required components"):
        write_install_report(report_path, report)

    assert report_path.read_text(encoding="utf-8") == "previous"
