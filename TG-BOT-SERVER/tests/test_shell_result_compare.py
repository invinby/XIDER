import importlib

import pytest

import shell_result_pager as pager
import shell_result_compare as compare


@pytest.fixture(autouse=True)
def fresh_stores():
    importlib.reload(pager)
    importlib.reload(compare)


def _result(text: str, *, label: str = "Laptop", now: int = 100):
    return pager.create(owner_id=20, device_label=label, text=text, footer="done", now=now)


def test_manual_selection_compares_only_redacted_results_and_is_single_use():
    first = _result("ready=true\npassword=first-secret")
    second = _result("ready=false\npassword=second-secret", now=101)

    selected = compare.select(first.token, owner_id=20, now=102)
    assert selected.state == "waiting_for_second"

    result = compare.select(second.token, owner_id=20, now=103)
    assert result.state == "compared"
    assert "-ready=true" in result.text and "+ready=false" in result.text
    assert "first-secret" not in result.text and "second-secret" not in result.text
    assert "password=[REDACTED]" in result.text
    assert compare.select(second.token, owner_id=20, now=104).state == "waiting_for_second"


def test_selection_is_owner_scoped_and_expired_result_cannot_be_compared():
    first = _result("one")
    assert compare.select(first.token, owner_id=21, now=101).state == "unavailable"
    assert compare.select(first.token, owner_id=20, now=100 + pager.TTL_SECONDS).state == "unavailable"


def test_compare_diff_is_bounded_and_unchanged_results_are_explicit():
    first = _result("same")
    second = _result("same", now=101)
    compare.select(first.token, owner_id=20, now=102)
    result = compare.select(second.token, owner_id=20, now=103)
    assert result.state == "compared"
    assert result.text == "Результаты совпадают после очистки чувствительных строк."


def test_html_escaped_diff_stays_within_telegram_message_budget():
    import html

    first = _result("\n".join("<x" for _ in range(1000)))
    second = _result("\n".join(">x" for _ in range(1000)), now=101)
    compare.select(first.token, owner_id=20, now=102)
    result = compare.select(second.token, owner_id=20, now=103)
    assert len(html.escape(result.text)) <= 3000


def test_comparison_requires_fresh_second_selection_and_rejects_oversized_data():
    first = _result("one")
    oversized = _result("x" * (pager.MAX_RESULT_CHARS + 1), now=101)
    assert oversized is None
    compare.select(first.token, owner_id=20, now=102)
    assert compare.select("invalid-token", owner_id=20, now=103).state == "unavailable"
