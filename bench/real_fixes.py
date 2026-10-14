"""Real bugs from the apps Doppel already runs: would it have flagged the change that let each one in?

The zero-setup experiment found four apps whose own test suites Doppel runs end to end with no setup (fava,
isso, privacyidea, prefect). Each of them has since fixed real bugs. This harness turns every fix into the PR
that brings the bug back, the way bench/bugsinpy.py does for BugsInPy:

    base  the fix commit, with the tests the fix added or changed put back as they were before it
    head  the commit just before the fix (the bug is there)

So both worlds run the test suite as it stood before anyone knew about the bug. A test written for the bug
would only show that the new test catches it; the question is whether the rest of the suite's traffic shows
the bug.

The fixes below, and the route where each bug shows (`expect`), were chosen from the apps' histories before
any of them ran (14 Oct 2026). Each fix ends in one of:
    caught        a difference judged a regression (or failing test) on the bug's route
    elsewhere     differences flagged, but none on the bug's route (reported, not counted as a catch)
    invisible     the suite ran on both and no request through the bug's route changed
    not runnable  a safeguard stopped, with the reason

    python bench/real_fixes.py list
    python bench/real_fixes.py run fava isso --backend nebius      # an app, a fix id, or all
    python bench/real_fixes.py summary
    python bench/real_fixes.py run --twin isso-stored-xss           # test mode and the guided twin together

Results: bench/results/fixes/<id>/ (check.json, report.md, doppel-check.html, result.json) and README.md;
with --twin, bench/results/fixes/twin/<id>/ and twin/README.md. Finished fixes are skipped (--force redoes them).
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import time
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

CACHE = HERE / ".cache" / "fixes"
RESULTS = HERE / "results" / "fixes"
TWIN = RESULTS / "twin"
REPOS = {"fava": "https://github.com/beancount/fava", "isso": "https://github.com/isso-comments/isso",
         "privacyidea": "https://github.com/privacyidea/privacyidea", "prefect": "https://github.com/PrefectHQ/prefect"}
FRAMEWORK = {"fava": "Flask", "isso": "Flask", "privacyidea": "Flask", "prefect": "FastAPI"}

# Chosen 14 Oct 2026 from each app's history: fixes that change server code reachable over HTTP. `bug` is what
# the head (pre-fix) code gets wrong, in the fix's own words; `expect` is where it shows (a regex on the request).
FIXES = [
    {"id": "fava-account-order", "app": "fava", "sha": "1a22736213887bcfa75102d4c8cc49a4fc6fe3d1",
     "date": "2025-10-13", "title": "fix account page date order (#2109)",
     "bug": "The account journal's entries are numbered and ordered wrongly when the report is paged.",
     "expect": r"account_report|/account/"},
    {"id": "fava-int-lineno-meta", "app": "fava", "sha": "7096e25c6dcc48ab0059643b5592197492ddd0dc",
     "date": "2026-09-11", "title": "fix deserialisation of int lineno metadata",
     "bug": "Entries whose metadata holds an integer (lineno) can't be deserialised when saved through the API.",
     "expect": r"PUT .*/api/|POST .*/api/"},
    {"id": "fava-posting-documents", "app": "fava", "sha": "9ec4975aadc67939ae02c6406c18e7eb35b68331",
     "date": "2026-05-17", "title": "fix document metadata links on transaction postings",
     "bug": "Document links attached to a posting miss entry_hash, and the backend can't resolve them.",
     "expect": r"journal|document|statement|/account"},
    {"id": "fava-import-dirs-spaces", "app": "fava", "sha": "2d2284195ac8b80b631939eb61b074806fba7f50",
     "date": "2026-01-10", "title": "fix: support spaces in import directory paths (#2181)",
     "bug": "An import directory option with spaces in its path is split into several directories.",
     "expect": r"/api/(options|imports)"},
    {"id": "isso-stored-xss", "app": "isso", "sha": "2e588895b6374be4d4b1d8cec8e2e263a35daaef",
     "date": "2026-02-19", "title": "Fix stored XSS in website and author fields",
     "bug": "Single quotes in website aren't escaped, and edits don't escape website or author at all (stored XSS).",
     "expect": r"POST /new|PUT /id/|/edit/"},
    {"id": "isso-no-cache", "app": "isso", "sha": "7e91d59a261d98f772d6ac5ccb25cbb9693d2ed6",
     "date": "2026-04-22", "title": "views: Add no-cache headers to fix stale comment display",
     "bug": "Comment listings are sent without no-cache headers, so browsers and proxies show stale comments.",
     "expect": r"GET /(\?|count|latest|id/|$)"},
    {"id": "privacyidea-userlist-scope", "app": "privacyidea", "sha": "2117297904d1daef8f900155ee5fbb8c4d0f96be",
     "date": "2026-09-22", "title": "Refuse a user listing that cannot be narrowed to the admin's realms",
     "bug": "An admin allowed to list one user or resolver can enumerate every user of the installation.",
     "expect": r"GET /user"},
    {"id": "privacyidea-every-realm", "app": "privacyidea", "sha": "c9cda3fd98288e33166d2087cd8edb78b4c47661",
     "date": "2026-09-21", "title": "Read a policy granting every realm as granting every realm",
     "bug": "A policy for every realm ('*') resolves to no realm: bulk revoke answers 200 and revokes nothing.",
     "expect": r"/client"},
    {"id": "prefect-zero-limit-pages", "app": "prefect", "sha": "1144d63fd38a83d06527f353fddc4febc357c0ee",
     "date": "2026-07-21", "title": "fix(server/api): consistent zero-limit behavior for core pagination endpoints",
     "bug": "Paginating flows, flow runs, task runs or deployments with limit=0 divides by zero.",
     "expect": r"POST /api/(flows|flow_runs|task_runs|deployments)/paginate"},
    {"id": "prefect-content-type", "app": "prefect", "sha": "98a02e4a09fea886f53833779c5fe1838fbb8212",
     "date": "2026-03-26", "title": "fix: default missing Content-Type to application/json for old clients",
     "bug": "Requests from old clients without a Content-Type header are rejected instead of read as JSON.",
     "expect": r"POST /api/"},
    {"id": "prefect-v2-trailing-slash", "app": "prefect", "sha": "431fb79a8600abe0adb696d59f22e40ee62b146c",
     "date": "2026-05-12", "title": "fix: preserve trailing slash when redirecting neutral entry points to V2 UI",
     "bug": "The redirect to the V2 UI drops the trailing slash and lands on the V1 app.",
     "expect": r"GET /(v2|prefect)?/?$|GET /\?"},
    {"id": "prefect-redirect-host", "app": "prefect", "sha": "5bbc9f513a41a6666809839555361663a8f0383b",
     "date": "2026-06-29", "title": "Fix UI version redirect leaking internal host behind reverse proxy",
     "bug": "The UI redirect's Location is absolute and names the internal host behind a reverse proxy.",
     "expect": r"GET /(v2|prefect)?/?$|GET /\?"},
    {"id": "prefect-pending-graph", "app": "prefect", "sha": "32ac27c4bd64fc5a12cb83144b3fc9166205ff34",
     "date": "2026-06-15", "title": "Fix pending task runs in flow run graph (#22287)",
     "bug": "The flow run graph leaves out task runs that are still pending.",
     "expect": r"GET /api/flow_runs/.+/graph"},
    {"id": "prefect-state-data-get", "app": "prefect", "sha": "d6258911934bc530344c5f808fef97b532a1b8e9",
     "date": "2026-05-15", "title": "fix(server): guard .get() calls on arbitrary State.data in result-data-loss rules",
     "bug": "Setting a state whose data isn't a dict crashes the orchestration rules (500).",
     "expect": r"POST /api/(flow_runs|task_runs)/.+/set_state"},
]

# what counts as a test: the fix's changes to these are put back in base, so no world runs a test written for the bug
TEST_PATH = re.compile(r"(^|/)(tests?|testing|__snapshots__)/|(^|/)(test_[^/]*|[^/]*_test)\.py$|(^|/)conftest\.py$")


def git(repo: Path, *args: str, binary: bool = False):
    p = subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True, text=not binary)
    return p.stdout


def repo_for(app: str) -> Path:
    """The app's history (no file contents until needed)."""
    repo = CACHE / "repos" / app
    if not (repo / ".git").exists():
        repo.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "-q", "--filter=blob:none", "--no-checkout", REPOS[app], str(repo)],
                       check=True)
    return repo


def export(repo: Path, commit: str, target: Path) -> None:
    """The files of one commit, as a plain folder."""
    target.mkdir(parents=True)
    data = git(repo, "archive", "--format=tar", commit, binary=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(target, filter="data")


def checkout(fix: dict) -> tuple[Path, Path, list[str]]:
    """base = the fix with its test changes undone, head = the commit before it. Returns (base, head, hidden)."""
    repo = repo_for(fix["app"])
    sha = fix["sha"]
    if subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=repo, capture_output=True).returncode:
        git(repo, "fetch", "-q", "origin", sha)
    parent = git(repo, "rev-parse", f"{sha}^").strip()
    work = CACHE / "work" / fix["id"]
    shutil.rmtree(work, ignore_errors=True)
    base, head = work / "base", work / "head"
    export(repo, sha, base)
    export(repo, parent, head)
    hidden = []
    for line in git(repo, "diff", "--no-renames", "--name-status", parent, sha).splitlines():
        status, path = line.split("\t", 1)
        if not TEST_PATH.search(path):
            continue
        hidden.append(path)
        target = base / path
        if status == "A":                    # a test the fix added: not in base either
            target.unlink(missing_ok=True)
        else:                                # changed or deleted by the fix: as it was before
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(git(repo, "show", f"{parent}:{path}", binary=True))
    return base, head, hidden


def pr_text(fix: dict, base: Path, head: Path) -> str:
    """What a reviewer would see of the bug-bringing PR: which files it touches, nothing about the bug."""
    from doppel.traffic import _changed_files
    changed, removed = _changed_files(base, head)
    files = sorted(set(changed + removed))
    return "Change in " + ", ".join(files[:8]) + (f" and {len(files) - 8} more files" if len(files) > 8 else "")


def run_one(fix: dict, backend: str, force: bool) -> dict:
    out_dir = RESULTS / fix["id"]
    done = out_dir / "result.json"
    if done.exists() and not force:
        return json.loads(done.read_text("utf-8"))
    from doppel import cli
    from doppel.checkview import write_check_view
    from doppel.errors import head_and_tail
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    t0 = time.perf_counter()
    result = {**fix, "framework": FRAMEWORK[fix["app"]], "backend": backend, "ran_on": str(date.today())}
    try:
        base, head, hidden = checkout(fix)
        result["hidden_tests"] = hidden
        (out_dir / "PR.md").write_text(pr_text(fix, base, head), encoding="utf-8")
        args = ["check", "--base", str(base), "--head", str(head), "--backend", backend, "--no-twin",
                "--app", fix["app"], "--pr", str(out_dir / "PR.md"),
                "--json", str(out_dir / "check.json"), "--out", str(out_dir / "report.md")]
        if backend == "local":
            from fresh_apps import local_python
            python, error = local_python({"name": fix["app"]}, base)
            if error:
                raise RuntimeError(error)
            args += ["--python", python]
        cli.main(args)
        data = json.loads((out_dir / "check.json").read_text("utf-8"))
        tests = data.get("tests") or {}
        if not tests.get("ran"):
            result.update(status="not runnable", reason=head_and_tail(tests.get("reason", ""), 4000))
        else:
            st = tests.get("stats") or {}
            result.update(score(fix, data), tests=st.get("tests"), requests=st.get("recorded_requests"),
                          test_command=tests.get("test"))
        write_check_view(out_dir / "check.json", out_dir / "doppel-check.html", public=True)
    except Exception as e:  # noqa: BLE001 - one fix's failure must not stop the others
        result.update(status="not runnable", reason=head_and_tail(f"{type(e).__name__}: {e}", 4000))
    result["seconds"] = round(time.perf_counter() - t0, 1)
    done.write_text(json.dumps(result, indent=1, default=str) + "\n", encoding="utf-8")
    return result


def score(fix: dict, data: dict) -> dict:
    """Caught (on the bug's route, or a test the change breaks), flagged elsewhere, or invisible."""
    expect = re.compile(fix["expect"])
    rows = data.get("rows") or []
    flagged = [r for r in rows if r.get("label") in ("regression", "needs_human", "unjudged")]
    hit = [r for r in flagged if r.get("kind") == "test" or "test fails on this" in r.get("detail", "")
           or expect.search(r.get("request", ""))]
    return {"status": "caught" if hit else "elsewhere" if flagged else "invisible", "differences": len(rows),
            "caught_by": sorted({r.get("source", "") for r in hit}),
            "on_route": [{k: r.get(k) for k in ("source", "request", "detail", "label", "why")} for r in hit][:10],
            "other": [{k: r.get(k) for k in ("source", "request", "detail", "label")}
                      for r in flagged if r not in hit][:10]}


def run_twin(fix: dict, backend: str, force: bool) -> dict:
    """The same PR through `doppel check` with the guided twin as well as test mode. The twin's setup is written
    by `doppel init` on the base and copied unchanged to the head: it is setup, not part of the PR. Nothing is
    told about the bug: the reviewer sees the diff and the same neutral description as the judge."""
    out_dir = TWIN / fix["id"]
    done = out_dir / "result.json"
    if done.exists() and not force:
        return json.loads(done.read_text("utf-8"))
    from doppel import cli
    from doppel.checkview import write_check_view
    from doppel.errors import head_and_tail
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    t0 = time.perf_counter()
    result = {**fix, "framework": FRAMEWORK[fix["app"]], "backend": backend, "ran_on": str(date.today()),
              "mode": "test mode + guided twin"}
    try:
        base, head, hidden = checkout(fix)
        result["hidden_tests"] = hidden
        before = {p.relative_to(base) for p in base.rglob("*") if p.is_file()}
        with contextlib.redirect_stdout(sys.stderr):
            code = cli.main(["init", str(base), "--backend", backend])
        after = {p.relative_to(base) for p in base.rglob("*") if p.is_file()}
        added = sorted(after - before)
        if code != 0 or not (base / "doppel.toml").exists():
            raise RuntimeError(f"doppel init could not set up the twin for {fix['app']} (exit {code}); the log "
                               "above says why")
        for rel in added:
            for target in (head / rel, out_dir / "setup" / rel):
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(base / rel, target)
        result["twin_setup"] = [rel.as_posix() for rel in added]
        (out_dir / "PR.md").write_text(pr_text(fix, base, head), encoding="utf-8")
        cli.main(["check", "--base", str(base), "--head", str(head), "--backend", backend, "--app", fix["app"],
                  "--pr", str(out_dir / "PR.md"), "--json", str(out_dir / "check.json"),
                  "--out", str(out_dir / "report.md"), "--twin-json", str(out_dir / "twin.json")])
        data = json.loads((out_dir / "check.json").read_text("utf-8"))
        tests, twin = data.get("tests") or {}, data.get("twin") or {}
        result["modes"] = {"tests": {"ran": tests.get("ran"), "reason": (tests.get("reason") or "")[:500]},
                           "twin": {k: twin.get(k) for k in ("ran", "reason", "coverage", "personas", "source")}}
        result["suspicions"] = twin.get("suspicions")
        if not tests.get("ran") and not twin.get("ran"):
            result.update(status="not runnable", reason="neither mode ran: " + (twin.get("reason") or ""))
        else:
            result.update(score(fix, data))
        write_check_view(out_dir / "check.json", out_dir / "doppel-check.html", public=True)
    except Exception as e:  # noqa: BLE001 - one fix's failure must not stop the others
        result.update(status="not runnable", reason=head_and_tail(f"{type(e).__name__}: {e}", 4000))
    result["seconds"] = round(time.perf_counter() - t0, 1)
    done.write_text(json.dumps(result, indent=1, default=str) + "\n", encoding="utf-8")
    return result


def twin_summary() -> Path:
    rows = [json.loads((TWIN / f["id"] / "result.json").read_text("utf-8")) for f in FIXES
            if (TWIN / f["id"] / "result.json").exists()]
    lines = ["# Real fixes, with the guided twin", "",
             "The same PRs as ../README.md (base = the fix, head = the commit before it, the fix's tests put back), "
             "now through `doppel check` with both modes: the app's own tests, and Nemotron's invented users "
             "guided by an AI reviewer's suspicions. The twin's setup was written by `doppel init` on the base "
             "and copied to the head unchanged. The reviewer sees the diff, so a bug whose fix added a guard "
             "shows up in the diff as a removed guard: the twin's part is the proof, a recorded request.", "",
             "| Fix | Bug | Result | Caught by | Reviewer's suspicions | Detail |", "|---|---|---|---|---|---|"]
    for r in rows:
        sus = r.get("suspicions") or []
        said = "; ".join(f"{s.get('status', '?')}: {str(s.get('claim') or s.get('suspicion') or s)[:60]}"
                         for s in sus[:3]) if isinstance(sus, list) else ""
        first = (r.get("on_route") or r.get("other") or [{}])[0]
        detail = f"{first.get('request', '')}: {first.get('detail', '')}" if first else ""
        if not detail.strip(": ") and r.get("reason"):
            detail = r["reason"].splitlines()[0]
        lines.append(f"| [{r['id']}]({r['id']}/doppel-check.html) | {r['bug']} | {r['status']} | "
                     f"{', '.join(r.get('caught_by') or [])} | {said.replace('|', '/')} | "
                     f"{detail[:110].replace('|', '/')} |")
    out = TWIN / "README.md"
    TWIN.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return out


def summary() -> Path:
    rows = [json.loads((RESULTS / f["id"] / "result.json").read_text("utf-8")) for f in FIXES
            if (RESULTS / f["id"] / "result.json").exists()]
    count = {s: sum(r["status"] == s for r in rows) for s in ("caught", "elsewhere", "invisible", "not runnable")}
    lines = ["# Real fixes: would Doppel have flagged the change that let each bug in?", "",
             "Each fix becomes the PR that brings its bug back (base = the fix, head = the commit before it). The "
             "tests the fix added or changed are put back as they were, so both worlds run the suite as it stood "
             "before anyone knew about the bug. Fixes and their routes were chosen before any ran.", "",
             f"**{count['caught']} caught · {count['elsewhere']} flagged elsewhere · {count['invisible']} invisible "
             f"· {count['not runnable']} not runnable** — of {len(rows)} run.", "",
             "| Fix | App | Fixed on | Bug | Result | Tests | Requests | Detail |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if r.get("on_route"):
            detail = f"{r['on_route'][0]['request']}: {r['on_route'][0]['detail']}"
        elif r.get("other"):
            detail = f"{r['other'][0]['request']}: {r['other'][0]['detail']}"
        else:
            detail = (r.get("reason") or "").splitlines()[0] if r.get("reason") else ""
        lines.append(f"| [{r['id']}]({r['id']}/doppel-check.html) | {r['app']} | {r['date']} | {r['bug']} | "
                     f"{r['status']} | {r.get('tests', '')} | {r.get('requests', '')} | "
                     f"{detail[:110].replace('|', '/')} |")
    out = RESULTS / "README.md"
    RESULTS.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return out


def selected(names: list[str]) -> list[dict]:
    if not names or names == ["all"]:
        return FIXES
    unknown = [n for n in names if not any(n in (f["id"], f["app"]) for f in FIXES)]
    if unknown:
        raise SystemExit(f"unknown fix or app: {', '.join(unknown)} (see: python bench/real_fixes.py list)")
    return [f for f in FIXES if f["id"] in names or f["app"] in names]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("step", choices=["list", "run", "summary"])
    ap.add_argument("fixes", nargs="*", help="fix ids or app names (default: all)")
    ap.add_argument("--backend", choices=["local", "nebius"], default="nebius")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--twin", action="store_true", help="the guided twin as well as test mode (runs doppel init)")
    a = ap.parse_args(argv)
    if a.step == "summary":
        twin_summary() if a.twin else summary()
        return 0
    for fix in selected(a.fixes):
        if a.step == "list":
            print(f"{fix['id']:28} {fix['app']:12} {fix['date']}  {fix['title']}")
            continue
        if a.backend == "nebius":
            from dotenv import load_dotenv
            load_dotenv()
        label = fix["id"] + (" (twin)" if a.twin else "")
        print(f"[fixes] {label}", file=sys.stderr)
        r = run_twin(fix, a.backend, a.force) if a.twin else run_one(fix, a.backend, a.force)
        why = (r.get("reason") or "").splitlines()[0][:140] if r.get("reason") else ""
        by = f" by {', '.join(r['caught_by'])}" if r.get("caught_by") else ""
        print(f"[fixes] {label} -> {r['status']}{by}" + (f": {why}" if why else ""))
    if a.step == "run":
        twin_summary() if a.twin else summary()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
