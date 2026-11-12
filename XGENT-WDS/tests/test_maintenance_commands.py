from xg_commands import maintenance


def test_clean_temp_preserves_success_response_contract():
    published = []
    cleanup_calls = []
    maintenance.clean_temp(
        {}, publish_response=lambda topic, response: published.append((topic, response)),
        device_id="device-4",
        cleanup_stale_media_temp=lambda: cleanup_calls.append(True) or {
            "state": "success", "deleted": "2", "skipped": 1, "failed": 0,
        },
    )

    assert cleanup_calls == [True]
    assert published == [("output", {
        "type": "sys_clean_temp", "device_id": "device-4", "ok": True,
        "state": "success", "deleted": 2, "skipped": 1, "failed": 0,
        "text": "🧹 Очистка временных файлов XIDER завершена.\n"
                "• Удалено просроченных файлов: 2\n"
                "• Пропущено (активные или неподходящие): 1",
    })]


def test_clean_temp_preserves_partial_and_unknown_state_responses():
    published = []
    cleanup_reports = iter((
        {"state": "partial", "deleted": 1, "skipped": 2, "failed": 3},
        {"state": "unexpected", "deleted": 0, "skipped": 0, "failed": 0},
    ))
    for _ in range(2):
        maintenance.clean_temp(
            {}, publish_response=lambda topic, response: published.append((topic, response)),
            device_id="device-4", cleanup_stale_media_temp=lambda: next(cleanup_reports),
        )

    assert published[0][1]["ok"] is False
    assert published[0][1]["state"] == "partial"
    assert "частично" in published[0][1]["text"]
    assert published[1][1]["ok"] is False
    assert published[1][1]["state"] == "error"
    assert "не очищены" in published[1][1]["text"]
