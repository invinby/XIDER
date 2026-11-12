from xg_commands import filesystem


def test_path_open_expands_user_path_and_preserves_response():
    published = []
    opened = []
    filesystem.path_open(
        {"path": "  ~/Documents/report.txt  "},
        publish_response=lambda topic, response: published.append((topic, response)),
        device_id="device-4", expanduser=lambda path: path.replace("~", "C:/Users/test"),
        startfile=opened.append,
    )

    assert opened == ["C:/Users/test/Documents/report.txt"]
    assert published == [("path_open", {
        "type": "path_open", "device_id": "device-4", "ok": True,
        "path": "~/Documents/report.txt", "error": None,
    })]


def test_path_open_reports_missing_path_without_opening():
    published = []
    filesystem.path_open(
        {"path": "   "}, publish_response=lambda topic, response: published.append((topic, response)),
        device_id="device-4", expanduser=lambda path: path,
        startfile=lambda _path: (_ for _ in ()).throw(AssertionError("must not open")),
    )

    assert published == [("path_open", {
        "type": "path_open", "device_id": "device-4", "ok": False,
        "state": "failed", "error": "invalid_or_missing_path",
    })]


def test_path_open_returns_platform_error_without_changing_shape():
    published = []
    filesystem.path_open(
        {"path": "~/missing"}, publish_response=lambda topic, response: published.append((topic, response)),
        device_id="device-4", expanduser=lambda path: path,
        startfile=lambda _path: (_ for _ in ()).throw(AttributeError("not on this platform")),
    )

    assert published == [("path_open", {
        "type": "path_open", "device_id": "device-4", "ok": False,
        "path": "~/missing", "error": "not on this platform",
    })]
