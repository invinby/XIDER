"""Verify public GitHub Pages deployment evidence without changing repository state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin
from urllib.request import Request, urlopen


DEFAULT_REPOSITORY = "invinby/XIDER"
DEFAULT_BASE_URL = "https://invinby.github.io/XIDER/"
ASSETS = ("index.html", "ru.html", "styles.css")
FetchBytes = Callable[[str], bytes]


def _fetch_bytes(url: str) -> bytes:
    headers = {"User-Agent": "XIDER-pages-status/1.0", "Accept": "application/vnd.github+json"}
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers)
    with urlopen(request, timeout=20) as response:
        return response.read()


def _json(fetch_bytes: FetchBytes, url: str) -> object:
    return json.loads(fetch_bytes(url).decode("utf-8"))


def _asset_record(path: str, expected_url: str, live_url: str, fetch_bytes: FetchBytes) -> dict[str, str]:
    record = {"path": path, "status": "unknown", "expected_sha256": "", "live_sha256": ""}
    try:
        expected = fetch_bytes(expected_url)
        live = fetch_bytes(live_url)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        return record
    expected_hash = hashlib.sha256(expected).hexdigest()
    live_hash = hashlib.sha256(live).hexdigest()
    record.update(
        status="match" if expected_hash == live_hash else "mismatch",
        expected_sha256=expected_hash,
        live_sha256=live_hash,
    )
    return record


def verify_pages_status(
    repository: str = DEFAULT_REPOSITORY,
    *,
    base_url: str = DEFAULT_BASE_URL,
    fetch_bytes: FetchBytes = _fetch_bytes,
) -> dict[str, object]:
    """Compare the latest Pages deployment, matching workflow run, and public files.

    `unknown` means the public APIs did not provide enough evidence. A local test
    result is deliberately not treated as proof of a published deployment.
    """
    repo_path = quote(repository, safe="/")
    api_root = f"https://api.github.com/repos/{repo_path}"
    deployment_url = api_root + "/deployments?" + urlencode({"environment": "github-pages", "per_page": 1})
    workflow_url = api_root + "/actions/workflows/pages.yml/runs?" + urlencode(
        {"branch": "main", "event": "push", "per_page": 20}
    )
    report: dict[str, object] = {
        "repository": repository,
        "status": "unknown",
        "deployment_sha": "",
        "deployment_status": "unknown",
        "deployment_url": "",
        "workflow": {"status": "unknown", "conclusion": "", "url": ""},
        "assets": [],
        "errors": [],
    }
    errors: list[str] = report["errors"]  # type: ignore[assignment]
    try:
        deployments = _json(fetch_bytes, deployment_url)
        if not isinstance(deployments, list) or not deployments:
            errors.append("GitHub returned no Pages deployment record")
            return report
        deployment = deployments[0]
        if not isinstance(deployment, dict) or not deployment.get("id") or not deployment.get("sha"):
            errors.append("Latest deployment record is incomplete")
            return report
        deployment_id = deployment["id"]
        deployment_sha = str(deployment["sha"]).lower()
        report["deployment_sha"] = deployment_sha
        status_data = _json(fetch_bytes, f"{api_root}/deployments/{deployment_id}/statuses?per_page=1")
        if not isinstance(status_data, list) or not status_data or not isinstance(status_data[0], dict):
            errors.append("Latest deployment has no readable status")
            return report
        deployment_status = str(status_data[0].get("state") or "unknown").lower()
        report["deployment_status"] = deployment_status
        status_page_url = status_data[0].get("environment_url")
        page_url = str(status_page_url or base_url)
        if not page_url.endswith("/"):
            page_url += "/"
        report["deployment_url"] = page_url
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, TypeError, KeyError) as exc:
        errors.append(f"GitHub deployment evidence unavailable: {type(exc).__name__}: {str(exc)[:160]}")
        return report

    workflow: dict[str, str] = {"status": "unknown", "conclusion": "", "url": ""}
    report["workflow"] = workflow
    try:
        run_data = _json(fetch_bytes, workflow_url)
        runs = run_data.get("workflow_runs") if isinstance(run_data, dict) else None
        if not isinstance(runs, list):
            errors.append("GitHub Pages workflow response is incomplete")
        else:
            matching = [run for run in runs if isinstance(run, dict) and str(run.get("head_sha", "")).lower() == report["deployment_sha"]]
            if not matching:
                workflow["status"] = "missing-success-for-deployment-commit"
            else:
                run = matching[0]
                workflow["conclusion"] = str(run.get("conclusion") or "")
                workflow["url"] = str(run.get("html_url") or "")
                if run.get("status") != "completed":
                    workflow["status"] = "in-progress"
                elif run.get("conclusion") == "success":
                    workflow["status"] = "success"
                else:
                    workflow["status"] = "failed"
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, TypeError) as exc:
        errors.append(f"GitHub workflow evidence unavailable: {type(exc).__name__}: {str(exc)[:160]}")

    asset_results = []
    for path in ASSETS:
        source_url = f"https://raw.githubusercontent.com/{repo_path}/{report['deployment_sha']}/site/{path}"
        live_url = urljoin(str(report["deployment_url"]), path)
        asset_results.append(_asset_record(path, source_url, live_url, fetch_bytes))
    report["assets"] = asset_results

    evidence_states = [str(item["status"]) for item in asset_results]
    if report["deployment_status"] in ("failure", "error", "inactive"):
        report["status"] = "mismatch"
    elif workflow["status"] in ("failed", "missing-success-for-deployment-commit"):
        report["status"] = "mismatch"
    elif "mismatch" in evidence_states:
        report["status"] = "mismatch"
    elif report["deployment_status"] == "success" and workflow["status"] == "success" and all(state == "match" for state in evidence_states):
        report["status"] = "verified"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY, help="GitHub owner/repository")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Published Pages base URL")
    args = parser.parse_args(argv)
    report = verify_pages_status(args.repository, base_url=args.base_url)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return {"verified": 0, "mismatch": 1, "unknown": 2}[str(report["status"])]


if __name__ == "__main__":
    raise SystemExit(main())
