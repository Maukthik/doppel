"""Post (or update) Doppel's report as one comment on the pull request. Standard library only.

Runs inside GitHub Actions: reads GITHUB_TOKEN, GITHUB_REPOSITORY and the PR number from the
event payload. The comment carries a hidden marker so later runs edit it instead of adding more.
"""
from __future__ import annotations

import json
import os
import urllib.request

MARKER = "<!-- doppel-report -->"
API = os.environ.get("GITHUB_API_URL", "https://api.github.com")


def _request(method: str, url: str, token: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read() or b"null")


def pr_number_from_event(path: str | None = None) -> int:
    with open(path or os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as f:
        event = json.load(f)
    number = (event.get("pull_request") or {}).get("number") or (event.get("issue") or {}).get("number")
    if not number:
        raise RuntimeError("This event has no pull request (run Doppel on pull_request events).")
    return int(number)


def upsert_comment(body: str, repo: str, pr: int, token: str, request=_request) -> str:
    """Edit Doppel's existing comment on the PR, or create it. Returns "updated" or "created"."""
    body = f"{MARKER}\n{body}"
    page = 1
    while True:
        comments = request("GET", f"{API}/repos/{repo}/issues/{pr}/comments?per_page=100&page={page}", token)
        for c in comments or []:
            if MARKER in (c.get("body") or ""):
                request("PATCH", f"{API}/repos/{repo}/issues/comments/{c['id']}", token, {"body": body})
                return "updated"
        if not comments or len(comments) < 100:
            break
        page += 1
    request("POST", f"{API}/repos/{repo}/issues/{pr}/comments", token, {"body": body})
    return "created"
