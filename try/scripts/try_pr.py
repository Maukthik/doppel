"""Try Doppel on any public pull request: the steps behind doppel-try's issue workflow.

    python scripts/try_pr.py start      # find the PR link in the issue, check the daily limit, reply "running"
    python scripts/try_pr.py prepare    # check out the PR's base and head, write its description
    python scripts/try_pr.py fallback   # a report for the reply when Doppel stopped before writing one
    python scripts/try_pr.py label CODE # label the issue with the outcome (0, 1 or 2, as `doppel check` exits)
    python scripts/try_pr.py crashed    # tell the issue's author this run broke (a bug here, not in their PR)

What the issue's author wrote is read from the environment and never pasted into a shell command. Standard
library only, so it runs before anything is installed.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PR_URL = re.compile(r"https://github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/([A-Za-z0-9_.-]{1,100})/pull/(\d{1,7})")
API = os.environ.get("GITHUB_API_URL", "https://api.github.com")
WORK = Path(".doppel")
OUT = WORK / "out"
LABELS = {0: ("no regressions", "2da44e"), 1: ("regression found", "d1242f"), 2: ("couldn't check", "8c959f"),
          3: ("not run", "d0d7de")}
HOW = ("Open an issue from the **Try Doppel** form and paste a public pull request link, such as "
       "`https://github.com/Maukthik/doppel-demo/pull/1`.")


def find_pr(text: str) -> tuple[str, str, int] | None:
    """The first GitHub pull request link in the text, as (owner, repo, number)."""
    m = PR_URL.search(text or "")
    if not m:
        return None
    return m.group(1), m.group(2).removesuffix(".git"), int(m.group(3))


def _request(method: str, path: str, body: dict | None = None):
    token = os.environ.get("GITHUB_TOKEN", "")
    req = urllib.request.Request(
        path if path.startswith("http") else f"{API}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                 "Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read() or b"null")


def output(**values) -> None:
    lines = [f"{k}={v}" for k, v in values.items()]
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


def reply(text: str) -> None:
    repo, issue = os.environ["GITHUB_REPOSITORY"], os.environ["ISSUE"]
    _request("POST", f"/repos/{repo}/issues/{issue}/comments", {"body": text})


def decline(text: str) -> int:
    """No run: say why, label the issue "not run" and close it, so the Try page's list shows only real runs."""
    reply(text)
    output(go="false")
    cmd_label(3)
    repo, issue = os.environ["GITHUB_REPOSITORY"], os.environ["ISSUE"]
    _request("PATCH", f"/repos/{repo}/issues/{issue}", {"state": "closed", "state_reason": "not_planned"})
    return 0


def runs_today(repo: str, now: dt.datetime) -> int:
    """Runs started in this repo during the last 24 hours (this one included). Issues declined without a run
    (labelled "not run": no link, a private PR, a repo too big) don't count, so they can't use up the day."""
    since = (now - dt.timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    query = urllib.parse.quote(f'repo:{repo} is:issue created:>={since} -label:"{LABELS[3][0]}"')
    return int(_request("GET", f"/search/issues?q={query}&per_page=1").get("total_count", 0))


def cmd_start() -> int:
    found = find_pr(os.environ.get("ISSUE_BODY", "")) or find_pr(os.environ.get("ISSUE_TITLE", ""))
    if not found:
        return decline(f"I couldn't find a pull request link in this issue. {HOW}")
    owner, name, number = found
    url = f"https://github.com/{owner}/{name}/pull/{number}"
    try:
        pull = _request("GET", f"/repos/{owner}/{name}/pulls/{number}")
    except urllib.error.HTTPError as e:
        return decline(f"I couldn't open {url} (GitHub answered {e.code}). Doppel can only try **public** pull "
                       "requests.")
    size_mb = int((pull.get("base", {}).get("repo") or {}).get("size", 0)) // 1024
    if size_mb > int(os.environ.get("MAX_REPO_MB", "500")):
        return decline(f"{owner}/{name} is about {size_mb} MB, more than this public demo clones. Doppel itself "
                       "has no such limit: https://github.com/Maukthik/doppel#put-it-on-your-pull-requests")
    limit = int(os.environ.get("DAILY_LIMIT", "25"))
    try:
        today = runs_today(os.environ["GITHUB_REPOSITORY"], dt.datetime.now(dt.UTC))
    except urllib.error.URLError:
        today = 0   # the search API had a hiccup: one run more is cheaper than turning someone away
    if today > limit:
        return decline(f"Doppel has already run {limit} times in the last 24 hours, the limit for this public "
                       "demo (each run uses real sandboxes). Please try again tomorrow, or put Doppel on your own "
                       "repo: https://github.com/Maukthik/doppel#put-it-on-your-pull-requests")
    run = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{os.environ['GITHUB_REPOSITORY']}" \
          f"/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"
    reply(f"Running Doppel on [{owner}/{name}#{number}]({url}): \"{pull.get('title', '')[:120]}\".\n\n"
          "It installs the app and runs its own tests on the code before and after the PR, in Nebius "
          f"sandboxes. That usually takes 1 to 5 minutes. [Watch it live]({run}). The result will appear here.")
    output(go="true", url=url, owner=owner, repo=name, number=str(number))
    return 0


def git(*args: str, cwd: Path | None = None) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return done.stdout.strip()


def cmd_prepare() -> int:
    owner, name, number = os.environ["OWNER"], os.environ["REPO"], int(os.environ["NUMBER"])
    pull = _request("GET", f"/repos/{owner}/{name}/pulls/{number}")
    src = WORK / "src"
    OUT.mkdir(parents=True, exist_ok=True)
    # blobs come on demand, so a large repo clones in seconds; refs/pull/N/head exists for forks too
    git("clone", "--quiet", "--filter=blob:none", "--no-checkout", f"https://github.com/{owner}/{name}.git", str(src))
    git("fetch", "--quiet", "origin", f"+refs/heads/{pull['base']['ref']}:refs/doppel/base",
        f"+refs/pull/{number}/head:refs/doppel/head", cwd=src)
    head = git("rev-parse", "refs/doppel/head", cwd=src)
    try:   # the commit the PR started from, so the comparison is the PR's own change and nothing merged since
        base = git("merge-base", pull["base"]["sha"], head, cwd=src)
    except subprocess.CalledProcessError:
        base = git("merge-base", "refs/doppel/base", head, cwd=src)
    for side, sha in (("base", base), ("head", head)):
        git("worktree", "add", "--quiet", "--detach", str((WORK / side).resolve()), sha, cwd=src)
    (OUT / "PR.md").write_text(f"# {pull.get('title', '')}\n\n{pull.get('body') or ''}\n", encoding="utf-8")
    output(base=base, head=head, base_short=base[:7], head_short=head[:7])
    return 0


def cmd_fallback() -> int:
    log = OUT / "doppel.log"
    text = log.read_text("utf-8", "replace") if log.exists() else ""
    for secret in ("NEBIUS_API_KEY", "NEBIUS_PROJECT_ID"):   # a comment isn't masked the way the job log is
        if os.environ.get(secret):
            text = text.replace(os.environ[secret], "***")
    tail = "\n".join(text.splitlines()[-25:]).replace("`" * 3, "'" * 3) or "(no output)"
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.md").write_text(
        "## Doppel: couldn't check this PR\n\nDoppel stopped before it could compare the two versions. "
        f"The end of its log:\n\n{'`' * 3}\n{tail}\n{'`' * 3}\n", encoding="utf-8")
    return 0


def cmd_label(code: int) -> int:
    repo, issue = os.environ["GITHUB_REPOSITORY"], os.environ["ISSUE"]
    name, color = LABELS.get(code, LABELS[2])
    try:
        _request("POST", f"/repos/{repo}/labels", {"name": name, "color": color})
    except urllib.error.HTTPError as e:
        if e.code != 422:   # 422: the label exists already
            raise
    _request("POST", f"/repos/{repo}/issues/{issue}/labels", {"labels": [name]})
    return 0


def cmd_crashed() -> int:
    run = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{os.environ['GITHUB_REPOSITORY']}" \
          f"/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"
    reply(f"This run broke before Doppel could report (the [run log]({run}) says where). That's a problem on this "
          "side, not in your pull request. Opening a new issue with the same link tries again.")
    return cmd_label(2)


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    if cmd == "start":
        return cmd_start()
    if cmd == "prepare":
        return cmd_prepare()
    if cmd == "fallback":
        return cmd_fallback()
    if cmd == "crashed":
        return cmd_crashed()
    if cmd == "label":
        return cmd_label(int(argv[1]) if len(argv) > 1 and argv[1].isdigit() else 2)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
