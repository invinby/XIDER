from __future__ import annotations

import json

from pages_status import verify_pages_status


REPO = "invinby/XIDER"
SHA = "a" * 40
BASE = "https://invinby.github.io/XIDER/"
ASSETS = ("index.html", "ru.html", "styles.css")
CONTENT = {name: f"published:{name}".encode() for name in ASSETS}


def _fixture_responses(*, deployment_sha: str = SHA, workflow_sha: str = SHA,
                       workflow_conclusion: str = "success", live_overrides: dict[str, bytes] | None = None) -> dict[str, object]:
    live_overrides = live_overrides or {}
    deployment_id = 77
    responses: dict[str, object] = {
        f"https://api.github.com/repos/{REPO}/deployments?environment=github-pages&per_page=1": [
            {"id": deployment_id, "sha": deployment_sha, "environment": "github-pages"}
        ],
        f"https://api.github.com/repos/{REPO}/deployments/{deployment_id}/statuses?per_page=1": [
            {"state": "success", "environment_url": BASE}
        ],
        f"https://api.github.com/repos/{REPO}/actions/workflows/pages.yml/runs?branch=main&event=push&per_page=20": {
            "workflow_runs": [{"head_sha": workflow_sha, "status": "completed",
                               "conclusion": workflow_conclusion, "html_url": "https://github.com/run/1"}]
        },
    }
    for name in ASSETS:
        responses[f"https://raw.githubusercontent.com/{REPO}/{deployment_sha}/site/{name}"] = CONTENT[name]
        responses[BASE + name] = live_overrides.get(name, CONTENT[name])
    return responses


def _fetcher(responses: dict[str, object]):
    def fetch(url: str) -> bytes:
        if url not in responses:
            raise OSError(f"not found in fixture: {url}")
        value = responses[url]
        if isinstance(value, bytes):
            return value
        return json.dumps(value).encode("utf-8")
    return fetch


def test_verified_requires_matching_successful_deployment_workflow_and_live_asset_hashes() -> None:
    report = verify_pages_status(REPO, fetch_bytes=_fetcher(_fixture_responses()))

    assert report["status"] == "verified"
    assert report["deployment_sha"] == SHA
    assert report["workflow"]["conclusion"] == "success"
    assert all(asset["status"] == "match" for asset in report["assets"])


def test_known_live_asset_hash_mismatch_is_reported_as_mismatch() -> None:
    responses = _fixture_responses(live_overrides={"ru.html": b"stale content"})

    report = verify_pages_status(REPO, fetch_bytes=_fetcher(responses))

    assert report["status"] == "mismatch"
    assert next(asset for asset in report["assets"] if asset["path"] == "ru.html")["status"] == "mismatch"


def test_deployment_commit_without_successful_pages_run_is_not_verified() -> None:
    responses = _fixture_responses(workflow_sha="b" * 40, workflow_conclusion="failure")

    report = verify_pages_status(REPO, fetch_bytes=_fetcher(responses))

    assert report["status"] == "mismatch"
    assert report["workflow"]["status"] == "missing-success-for-deployment-commit"


def test_missing_api_or_live_evidence_is_unknown_not_success() -> None:
    responses = _fixture_responses()
    responses.pop(f"{BASE}styles.css")

    report = verify_pages_status(REPO, fetch_bytes=_fetcher(responses))

    assert report["status"] == "unknown"
    assert next(asset for asset in report["assets"] if asset["path"] == "styles.css")["status"] == "unknown"


def test_latest_deployment_failure_is_a_mismatch() -> None:
    responses = _fixture_responses()
    responses[f"https://api.github.com/repos/{REPO}/deployments/77/statuses?per_page=1"] = [
        {"state": "failure", "environment_url": BASE}
    ]

    report = verify_pages_status(REPO, fetch_bytes=_fetcher(responses))

    assert report["status"] == "mismatch"
    assert report["deployment_status"] == "failure"


def test_pending_deployment_is_unknown_until_github_reports_a_terminal_state() -> None:
    responses = _fixture_responses()
    responses[f"https://api.github.com/repos/{REPO}/deployments/77/statuses?per_page=1"] = [
        {"state": "in_progress", "environment_url": BASE}
    ]

    report = verify_pages_status(REPO, fetch_bytes=_fetcher(responses))

    assert report["status"] == "unknown"
    assert report["deployment_status"] == "in_progress"
