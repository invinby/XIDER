from datetime import date

from os_support import classify_os_support


def test_windows_lifecycle_is_exact_release_edition_and_reference_dated():
    current = classify_os_support("windows", "11|26H2", "Pro", today=date(2026, 10, 16))
    expired = classify_os_support("windows", "11|24H2", "Home", today=date(2026, 10, 16))
    wrong_edition = classify_os_support("windows", "11|24H2", "Enterprise", today=date(2026, 10, 16))

    assert current["status"] == "supported"
    assert current["support_end"] == "2028-10-09"
    assert current["as_of"] == "2026-10-16"
    assert expired["status"] == "ended"
    assert expired["support_end"] == "2026-10-13"
    assert wrong_edition["status"] == "unknown"
    assert wrong_edition["support_end"] is None


def test_macos_reference_reports_observed_update_without_claiming_formal_support():
    recent = classify_os_support("macos", "15.7.9", None, today=date(2026, 10, 16))
    old = classify_os_support("macos", "13.7.1", None, today=date(2026, 10, 16))

    assert recent["status"] == "security_update_observed"
    assert recent["formal_end_date"] is None
    assert recent["last_security_update"] == "2026-08-06"
    assert old["status"] == "no_recent_update_observed"
    assert "does not publish a fixed OS end date" in old["caveat"]


def test_support_reference_fails_closed_for_unknown_or_expired_entries():
    unknown = classify_os_support("windows", "11|mystery", "Pro", today=date(2026, 10, 16))
    expired_reference = classify_os_support("windows", "11|26H2", "Pro", today=date(2027, 1, 15))

    assert unknown["status"] == "unknown"
    assert expired_reference["status"] == "reference_expired"
