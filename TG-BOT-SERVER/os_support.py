"""Dated, offline OS support reference. Unknown records never become support claims."""

from __future__ import annotations

import re
from datetime import date


REFERENCE_AS_OF = date(2026, 10, 16)
REFERENCE_EXPIRES = date(2027, 1, 14)
WINDOWS_LIFECYCLE_URL = "https://learn.microsoft.com/en-us/lifecycle/products/windows-11-home-and-pro"
WINDOWS_10_LIFECYCLE_URL = "https://learn.microsoft.com/en-us/lifecycle/products/windows-10-home-and-pro"
APPLE_RELEASES_URL = "https://support.apple.com/en-us/100100"

_WINDOWS_END_DATES = {
    ("10", "22H2", "Home"): date(2025, 10, 14),
    ("10", "22H2", "Pro"): date(2025, 10, 14),
    ("11", "23H2", "Home"): date(2025, 11, 11),
    ("11", "23H2", "Pro"): date(2025, 11, 11),
    ("11", "24H2", "Home"): date(2026, 10, 13),
    ("11", "24H2", "Pro"): date(2026, 10, 13),
    ("11", "25H2", "Home"): date(2027, 10, 12),
    ("11", "25H2", "Pro"): date(2027, 10, 12),
    ("11", "26H1", "Home"): date(2028, 3, 14),
    ("11", "26H1", "Pro"): date(2028, 3, 14),
    ("11", "26H2", "Home"): date(2028, 10, 9),
    ("11", "26H2", "Pro"): date(2028, 10, 9),
}
_MACOS_LAST_SECURITY_RELEASE = {
    "26": date(2026, 9, 14),
    "15": date(2026, 8, 6),
    "14": date(2026, 8, 6),
    "13": date(2024, 10, 28),
}


def _result(status: str, *, today: date, **fields) -> dict:
    return {
        "status": status,
        "as_of": REFERENCE_AS_OF.isoformat(),
        "expires_on": REFERENCE_EXPIRES.isoformat(),
        "reference_stale": today > REFERENCE_EXPIRES,
        **fields,
    }


def classify_os_support(family: str, release: str, edition: str | None = None, *, today: date | None = None) -> dict:
    """Classify only exact records in the embedded, dated reference."""
    today = today or date.today()
    if today > REFERENCE_EXPIRES:
        return _result("reference_expired", today=today, source_url=None, support_end=None,
                       last_security_update=None, formal_end_date=None, caveat="reference_expired")
    if not isinstance(release, str) or len(release) > 32:
        return _result("unknown", today=today, source_url=None, support_end=None,
                       last_security_update=None, formal_end_date=None, caveat="release_unknown")
    family = str(family or "").strip().lower()
    if family == "windows":
        win_edition = {"Core": "Home", "CoreSingleLanguage": "Home", "Professional": "Pro"}.get(edition, edition)
        match = re.fullmatch(r"(10|11)(?:\s|\.0)?", release.strip())
        version = match.group(1) if match else ""
        end_date = _WINDOWS_END_DATES.get((version, release.strip().upper().replace(" ", ""), win_edition))
        # The runtime supplies the release channel/version separately in release as VERSION|RELEASE.
        if "|" in release:
            version_text, release_text = release.split("|", 1)
            version_match = re.fullmatch(r"10|11", version_text.strip())
            version = version_match.group(0) if version_match else ""
            end_date = _WINDOWS_END_DATES.get((version, release_text.strip().upper(), win_edition))
        if end_date is None:
            return _result("unknown", today=today, source_url=WINDOWS_10_LIFECYCLE_URL if version == "10" else WINDOWS_LIFECYCLE_URL,
                           support_end=None, last_security_update=None, formal_end_date=None,
                           caveat="release_or_edition_not_in_reference")
        source_url = WINDOWS_10_LIFECYCLE_URL if version == "10" else WINDOWS_LIFECYCLE_URL
        status = "ended" if today >= end_date else "supported"
        return _result(status, today=today, source_url=source_url, support_end=end_date.isoformat(),
                       last_security_update=None, formal_end_date=end_date.isoformat(),
                       caveat="edition_specific_lifecycle_date")
    if family == "macos":
        match = re.match(r"^(\d{1,2})\.", release.strip())
        major = match.group(1) if match else ""
        last_update = _MACOS_LAST_SECURITY_RELEASE.get(major)
        if last_update is None:
            return _result("unknown", today=today, source_url=APPLE_RELEASES_URL, support_end=None,
                           last_security_update=None, formal_end_date=None,
                           caveat="no_matching_security_release_in_reference")
        status = "security_update_observed" if (today - last_update).days <= 365 else "no_recent_update_observed"
        return _result(status, today=today, source_url=APPLE_RELEASES_URL, support_end=None,
                       last_security_update=last_update.isoformat(), formal_end_date=None,
                       caveat="Apple does not publish a fixed OS end date here; this is only a release-history observation.")
    return _result("unknown", today=today, source_url=None, support_end=None,
                   last_security_update=None, formal_end_date=None, caveat="platform_not_in_reference")
