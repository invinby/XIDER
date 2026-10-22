import importlib

import pytest

import shell_result_pager as pager


@pytest.fixture(autouse=True)
def fresh_store():
    importlib.reload(pager)


def test_result_pages_are_bounded_owner_scoped_and_expire():
    first = pager.create(owner_id=20, device_label="Laptop", text="A" * 2201, footer="status", now=100)

    assert first.device_label == "Laptop"
    assert first.page == 0 and first.total_pages == 2 and first.text == "A" * 2200
    second = pager.get(first.token, owner_id=20, page=1, now=101)
    assert second.page == 1 and second.text == "A"
    assert pager.get(first.token, owner_id=21, page=1, now=101) is None
    assert pager.get(first.token, owner_id=20, page=2, now=101) is None
    assert pager.get(first.token, owner_id=20, page=1, now=400) is None


@pytest.mark.parametrize("text", ["", "X" * 16385])
def test_result_pager_rejects_empty_or_oversized_output(text):
    assert pager.create(owner_id=20, device_label="Laptop", text=text, footer="status", now=100) is None


def test_result_pager_rejects_invalid_owner_or_time():
    assert pager.create(owner_id=True, device_label="Laptop", text="long enough", footer="status", now=100) is None
    assert pager.create(owner_id=20, device_label="Laptop", text="long enough", footer="status", now=True) is None


def test_result_pager_keeps_redacted_source_and_structured_view_owner_scoped():
    created = pager.create(owner_id=20, device_label="Laptop", text='{"ready":true}',
                           structured_text='ready = true', footer="status", now=100)

    assert created is not None and created.has_structured and created.view == "source"
    structured = pager.get(created.token, owner_id=20, page=0, view="structured", now=101)
    assert structured.text == "ready = true" and structured.view == "structured"
    assert pager.get(created.token, owner_id=21, page=0, view="structured", now=101) is None
    assert pager.get(created.token, owner_id=20, page=0, view="unknown", now=101) is None


def test_json_fields_are_flattened_only_after_redaction_and_bounded():
    import shell_json_view

    fields = shell_json_view.format_json_fields('{"service":{"ready":true},"items":["a",2]}')
    assert fields == 'Структурированные поля JSON\nservice.ready = true\nitems[0] = "a"\nitems[1] = 2'
    assert shell_json_view.format_json_fields('{"password"=[REDACTED]}') is None
    assert shell_json_view.format_json_fields('not json') is None
    assert shell_json_view.format_json_fields('{"value":NaN}') is None
