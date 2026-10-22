import power_preview


def test_power_preview_explains_shutdown_impact_without_claiming_unsaved_work_detection():
    body = power_preview.render("shutdown", "Mac & Air", platform="macos")

    assert "Mac &amp; Air" in body
    assert "выключ" in body.lower()
    assert "агент" in body.lower()
    assert "несохран" in body.lower()
    assert "не может надёжно определить" in body.lower()


def test_power_preview_distinguishes_sleep_from_shutdown():
    sleep = power_preview.render("sleep", "Laptop", platform="windows")
    shutdown = power_preview.render("shutdown", "Laptop", platform="windows")

    assert "сон" in sleep.lower()
    assert "временно" in sleep.lower()
    assert "полностью выключит устройство" in shutdown.lower()
    assert sleep != shutdown


def test_power_preview_rejects_unknown_actions_and_platforms():
    assert power_preview.render("format", "Laptop", platform="windows") is None
    assert power_preview.render("shutdown", "Laptop", platform="linux") is None
