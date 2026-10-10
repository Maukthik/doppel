"""Real bugs, not planted ones: Doppel's test mode on BugsInPy's web-framework bugs.

BugsInPy (github.com/soarsmu/BugsInPy) is a research dataset of real bugs from popular Python projects: for
each, the commit with the bug, the commit that fixed it, the Python version and libraries of the time, and
the test that shows the bug. The web frameworks among them (FastAPI, Tornado, Sanic) have test suites that
send HTTP requests, which test mode records.

Each bug becomes one PR that brings the bug back:
    base  the fixed commit        (the known-good code)
    head  the buggy commit        (the change under review)
and the test written to catch this bug is hidden in every world: it was written after the bug was found, so
keeping it would only show that the old tests catch it. The question is whether the rest of the suite's
traffic shows the bug before anyone knew about it.

Each bug ends in one of:
    caught        test mode found a difference (judged, on Nebius, by Nemotron)
    invisible     the suite ran fine on both, and none of its requests went through the broken code
    not runnable  a safeguard stopped (install failed, tests don't run on that Python, ...), with the reason

    python bench/bugsinpy.py list                       # the bugs, and the revealing tests that get hidden
    python bench/bugsinpy.py run fastapi:3 tornado:15   # local (a uv venv per bug) or --backend nebius
    python bench/bugsinpy.py run all --backend nebius   # every web bug, Nemotron judge on differences
    python bench/bugsinpy.py summary

Results: bench/results/bugsinpy/<project>-<n>.json and README.md. Finished bugs are skipped (--force redoes).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

CACHE = HERE / ".cache" / "bugsinpy"
RESULTS = HERE / "results" / "bugsinpy"
DATASET = "https://github.com/soarsmu/BugsInPy.git"
WEB = ("fastapi", "tornado", "sanic")
REQS = "doppel-bugsinpy-requirements.txt"
# per project: where its tests live (always run with pytest, so the revealing tests can be deselected)
TESTS = {"fastapi": "tests", "tornado": "tornado/test", "sanic": "tests"}
OLD_ENV = ["setuptools<70", "wheel"]   # 2020 environments came with setuptools (pkg_resources) preinstalled


def sh(args, cwd=None, check=True):
    return subprocess.run(args, cwd=cwd, check=check, text=True, capture_output=True)


def dataset() -> Path:
    root = CACHE / "BugsInPy"
    if not (root / "projects").exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        sh(["git", "clone", "-q", "--depth", "1", DATASET, str(root)])
    return root / "projects"


def bugs(selection: list[str]) -> list[tuple[str, int]]:
    out = []
    for item in selection:
        if item == "all":
            out += [(p, n) for p in WEB for n in sorted(int(b.name) for b in (dataset() / p / "bugs").iterdir())]
        elif ":" in item:
            p, n = item.split(":")
            out.append((p, int(n)))
        else:
            out += [(item, n) for n in sorted(int(b.name) for b in (dataset() / item / "bugs").iterdir())]
    return out


def read_text(path: Path) -> str:
    """BugsInPy files are UTF-8 or UTF-16 (exported on Windows)."""
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    return raw.decode("utf-8", errors="replace")


def info(project: str, n: int) -> dict:
    folder = dataset() / project / "bugs" / str(n)
    fields = dict(re.findall(r'^(\w+)="([^"]*)"', read_text(folder / "bug.info"), re.M))
    url = re.search(r'github_url="([^"]+)"', read_text(dataset() / project / "project.info")).group(1)
    reqs = [ln.strip() for ln in read_text(folder / "requirements.txt").splitlines() if ln.strip()] \
        if (folder / "requirements.txt").exists() else []
    return {"project": project, "bug": n, "url": url, "python": ".".join(fields["python_version"].split(".")[:2]),
            "fixed": fields["fixed_commit_id"], "buggy": fields["buggy_commit_id"],
            "test_file": fields.get("test_file", ""), "reveal_cmds": read_text(folder / "run_test.sh").strip(),
            "requirements": reqs}


def revealing_tests(bug: dict, repo: Path) -> list[str]:
    """The tests BugsInPy runs to show this bug, as pytest node ids (unittest dotted ids converted)."""
    ids = []
    for line in bug["reveal_cmds"].splitlines():
        line = line.strip()
        if m := re.search(r"pytest\s+(\S+::\S+|\S+\.py)", line):
            ids.append(m.group(1))
        elif m := re.search(r"-m\s+unittest\s+(?:-\w+\s+)*([\w.]+)", line):
            ids.append(_unittest_to_node(m.group(1), repo))
    return [i for i in ids if i]


def _unittest_to_node(dotted: str, repo: Path) -> str:
    """tornado.test.web_test.StaticFileTest.test_x -> tornado/test/web_test.py::StaticFileTest::test_x"""
    parts = dotted.split(".")
    for k in range(len(parts), 0, -1):
        f = repo.joinpath(*parts[:k]).with_suffix(".py")
        if f.exists():
            return "::".join([f.relative_to(repo).as_posix(), *parts[k:]])
    return ""


def checkout(bug: dict) -> tuple[Path, Path]:
    """base = the fixed commit, head = the buggy one, as plain folders with the requirements of the time."""
    project_repo = CACHE / "repos" / bug["project"]
    if not (project_repo / ".git").exists():
        project_repo.parent.mkdir(parents=True, exist_ok=True)
        sh(["git", "clone", "-q", bug["url"], str(project_repo)])
    work = CACHE / "bugs" / f"{bug['project']}-{bug['bug']}"
    out = {}
    for side, commit in (("base", bug["fixed"]), ("head", bug["buggy"])):
        target = work / side
        if not target.exists():
            target.mkdir(parents=True)
            archive = subprocess.run(["git", "archive", commit], cwd=project_repo, capture_output=True, check=True)
            subprocess.run(["tar", "-x", "-C", str(target)], input=archive.stdout, check=True)
        # the project's own package is the code under test (this checkout), never a PyPI release: BugsInPy's
        # lists include it (fastapi==0.55.1), and its pins contradict the libraries this commit needs
        own = re.compile(rf"^{re.escape(bug['project'])}\s*(==|>=|<=|~=|$)", re.I)
        reqs = [windows_only(r) for r in bug["requirements"] if not own.match(r)] + OLD_ENV
        if not any(r.lower().startswith("pytest") for r in reqs):
            reqs.append("pytest<7" if bug["python"] in ("3.6", "3.7") else "pytest<8")
        (target / REQS).write_text("\n".join(reqs) + "\n", encoding="utf-8")   # same in both: not a change
        out[side] = target
    return out["base"], out["head"]


WINDOWS_ONLY = ("pywin32", "pypiwin32", "pywinpty", "wincertstore", "windows-curses", "winshell", "comtypes")


def windows_only(requirement: str) -> str:
    """Some BugsInPy lists were frozen on Windows (Sanic's has pywin32==227, which does not exist for Linux):
    mark such packages Windows-only, with pip's own environment marker."""
    name = re.split(r"[<>=!~;\s\[]", requirement, maxsplit=1)[0].strip().lower()
    if name in WINDOWS_ONLY and ";" not in requirement:
        return f'{requirement}; sys_platform == "win32"'
    return requirement


def local_python(bug: dict, base: Path) -> str:
    """A uv venv with this bug's Python and pinned libraries (local checks only; Nebius installs in the sandbox)."""
    venv = CACHE / "venvs" / f"{bug['project']}-{bug['bug']}"
    py = venv / "bin" / "python"
    if not py.exists():
        sh(["uv", "venv", "-q", "-p", bug["python"], str(venv)])
        r = subprocess.run(["uv", "pip", "install", "-q", "-r", str(base / REQS)], text=True, capture_output=True,
                           env={**__import__("os").environ, "VIRTUAL_ENV": str(venv)})
        if r.returncode:
            shutil.rmtree(venv, ignore_errors=True)
            raise RuntimeError("install failed:\n" + (r.stderr or r.stdout)[-1500:])
    return str(py)


def run_one(project: str, n: int, backend: str, judge: bool, force: bool) -> dict:
    out = RESULTS / f"{project}-{n}.json"
    if out.exists() and not force:
        done = json.loads(out.read_text("utf-8"))
        if done["status"] != "infra error" and "TransportError" not in done.get("reason", ""):
            return done             # an infrastructure error (or an old run's transport error) is retried
    from doppel.sandbox import InfraError
    from doppel.traffic import NotComparable, check, detect
    bug = info(project, n)
    t0 = time.perf_counter()
    result = {"project": project, "bug": n, "python": bug["python"], "fixed": bug["fixed"], "buggy": bug["buggy"],
              "backend": backend, "date": str(date.today())}
    try:
        base, head = checkout(bug)
        hidden = revealing_tests(bug, base)
        result["hidden_tests"] = hidden
        found = detect(base)
        settings = found["test"].split(";")[:-1] if found["test"] else []   # keep the project's export lines
        prefix = "; ".join(s.strip() for s in settings) + ("; " if settings else "")
        test = prefix + f"python -m pytest {TESTS[project]}"
        result["test_command"] = test
        if backend == "local":
            python, install = local_python(bug, base), ""
        else:
            python, install = bug["python"], f"pip install --quiet -r {REQS}"
        r = check(base, head, backend=backend, test=test, install=install, python=python, deselect=hidden,
                  log=lambda m: print(f"  {m}", file=sys.stderr))
        findings = r["findings"]
        verdicts = None
        if judge and findings:
            from doppel.judge import judge as run_judge
            from doppel.personas import code_diff
            pr = "Change in " + ", ".join(sorted({f for f in _changed(base, head)})[:6])
            verdicts = run_judge(findings, pr, code_diff(base, head))["verdicts"]
        rows = [{"kind": f.kind, "test": f.persona, "request": f.request, "detail": f.detail,
                 **({"label": v.label, "why": v.why} if verdicts else {})}
                for f, v in zip(findings, verdicts or [None] * len(findings), strict=True)]
        flagged = [x for x in rows if x.get("label", "regression") in ("regression", "needs_human")]
        result.update(status="caught" if flagged else "invisible", findings=rows[:60], finding_count=len(rows),
                      stats=r["stats"], noise_keys=r["noise_keys"], excluded=r["excluded"][:20])
    except InfraError as e:
        result.update(status="infra error", reason=str(e)[:2500])
    except NotComparable as e:
        result.update(status="not runnable", reason=str(e)[:2500])
    except Exception as e:  # noqa: BLE001 - one bug's failure must not stop the others
        result.update(status="not runnable", reason=f"{type(e).__name__}: {str(e)[:2000]}")
    result["seconds"] = round(time.perf_counter() - t0, 1)
    RESULTS.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1, default=str) + "\n", encoding="utf-8")
    return result


def _changed(base: Path, head: Path) -> list[str]:
    from doppel.traffic import _changed_files
    changed, removed = _changed_files(base, head)
    return [c for c in changed + removed if c != REQS]


def summary() -> Path:
    rows = [json.loads(f.read_text("utf-8")) for f in sorted(RESULTS.glob("*.json"))]
    rows.sort(key=lambda r: (WEB.index(r["project"]) if r["project"] in WEB else 9, r["bug"]))
    count = {s: sum(r["status"] == s for r in rows) for s in ("caught", "invisible", "not runnable", "infra error")}
    lines = ["# Real bugs: Doppel test mode on BugsInPy's web-framework bugs", "",
             "Each bug is a PR that brings a real, fixed bug back (base = the fix, head = the buggy commit), with the "
             "test written after the bug was found hidden. Python and libraries are the bug's own (BugsInPy).", "",
             f"**{count['caught']} caught · {count['invisible']} invisible (the suite never sent a request through "
             f"the bug) · {count['not runnable']} not runnable · {count['infra error']} infrastructure "
             f"errors (rerun)** — of {len(rows)} run.", "",
             "Unconfirmed = differences seen once but not when their tests were rerun (dropped as noise by "
             "safeguard S5, kept in each bug's JSON under stats.confirmation.unconfirmed for audit).", "",
             "| Bug | Python | Result | Tests run | Requests | Unconfirmed | Detail |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        st = r.get("stats") or {}
        detail = (r["findings"][0]["request"] + ": " + r["findings"][0]["detail"])[:90] if r.get("findings") else \
            (r.get("reason", "") or "").splitlines()[0][:90] if r.get("reason") else ""
        unconfirmed = (st.get("confirmation") or {}).get("dropped", "")
        lines.append(f"| {r['project']} #{r['bug']} | {r['python']} | {r['status']} | {st.get('passed', '')} | "
                     f"{st.get('recorded_requests', '')} | {unconfirmed or ''} | {detail.replace('|', '/')} |")
    out = RESULTS / "README.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("step", choices=["list", "run", "summary"])
    ap.add_argument("bugs", nargs="*", default=["all"], help="all, a project (fastapi), or project:n")
    ap.add_argument("--backend", choices=["local", "nebius"], default="local")
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    if a.step == "summary":
        summary()
        return 0
    for project, n in bugs(a.bugs):
        if a.step == "list":
            bug = info(project, n)
            print(f"{project}:{n} py{bug['python']} hides: " + " ".join(
                re.findall(r"(?:pytest\s+|unittest\s+(?:-\w+\s+)*)(\S+)", bug["reveal_cmds"])))
            continue
        if a.backend == "nebius":
            from dotenv import load_dotenv
            load_dotenv()
        print(f"[bugsinpy] {project}:{n}", file=sys.stderr)
        r = run_one(project, n, a.backend, judge=a.backend == "nebius" and not a.no_judge, force=a.force)
        why = r.get("reason", "").splitlines()[0][:120] if r.get("reason") else ""
        print(f"[bugsinpy] {project}:{n} -> {r['status']}" + (f" ({r.get('finding_count')} differences)"
              if r["status"] == "caught" else "") + (f": {why}" if why else ""))
    if a.step == "run":
        summary()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
