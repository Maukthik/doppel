"""Generality benchmark: does Doppel work on Python apps it was never tuned for?

Every result comes from the product as shipped, on the sponsor stack:
  setup      `doppel init` (Nemotron 3 Ultra drafts, Nebius sandboxes verify), no hand edits
  noise      A/A: the same code as base and head; Nemotron 3.5 Lightning invents the users, the twin
             runs on Nebius; every difference is a false alarm
  detection  planted PRs (bench/apps/<app>/prs, written before any run): the Stage 5 arms unchanged,
             so the numbers compare with bench/results:
               doppel  Lightning users + Ultra suspicions and probes -> Nebius twin -> Ultra judge
               review  Nemotron 3 Ultra reads source + diff + description (an AI code reviewer)
               tests   the app's own test suite on the PR's code, where the app has one

    python bench/generality.py fetch flaskr          # clone at the pinned commit (no credits)
    python bench/generality.py init flaskr           # doppel init on Nebius; saves the setup it found
    python bench/generality.py aa flaskr             # noise: same code twice
    python bench/generality.py prs flaskr            # planted PRs, all arms
    python bench/generality.py all flaskr            # init, aa, prs in order
    python bench/generality.py summary               # bench/results/generality/README.md

Results land in bench/results/generality/<app>/; finished steps are skipped on rerun (--force redoes
them), so an interrupted run resumes without spending credits twice. An app whose init fails stops
there and is reported as a setup failure: the benchmark never substitutes a hand-written setup.

`selfcheck` replays hand-written users with the local backend and no models, only to test this
harness and the planted patches. It never writes to bench/results.
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
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))

APPS = HERE / "apps"
CACHE = HERE / ".cache" / "generality"
RESULTS = HERE / "results" / "generality"
TWIN_FILES = ("doppel.toml", "doppel_seed.py")


def sh(args, cwd=None, check=True, **kw):
    return subprocess.run(args, cwd=cwd, check=check, text=True, capture_output=True, **kw)


def app_info(name: str) -> dict:
    path = APPS / name / "app.json"
    if not path.exists():
        sys.exit(f"unknown app {name}; known: {', '.join(sorted(p.name for p in APPS.iterdir() if p.is_dir()))}")
    return {"name": name, **json.loads(path.read_text("utf-8"))}


def save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def load(path: Path) -> dict | None:
    return json.loads(path.read_text("utf-8")) if path.exists() else None


# ---------------------------------------------------------------- code

def pristine(app: dict) -> Path:
    """The app at its pinned commit, exactly as upstream has it (no Doppel files)."""
    src = CACHE / app["name"] / "src"
    if not (src / ".git").exists():
        src.parent.mkdir(parents=True, exist_ok=True)
        sh(["git", "clone", "-q", "-c", "core.autocrlf=false", app["repo"], str(src)])
    sh(["git", "checkout", "-q", app["pin"]], cwd=src)
    out = CACHE / app["name"] / "pristine"
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(src / app.get("subdir", "."), out,
                    ignore=shutil.ignore_patterns(".git", "__pycache__", "*.sqlite", "*.sqlite3", "*.db", "instance"))
    for f in TWIN_FILES:  # an app that already ships a Doppel setup would not be a fair test
        if (out / f).exists():
            sys.exit(f"{app['name']} already contains {f}; pick another pin")
    return out


def with_setup(app: dict, setup: Path, tag: str) -> Path:
    """Pristine app + the twin files in `setup`, as a Doppel user would commit them."""
    base = CACHE / app["name"] / tag
    if base.exists():
        shutil.rmtree(base)
    shutil.copytree(pristine(app), base)
    for f in TWIN_FILES:
        shutil.copy(setup / f, base / f)
    return base


def apply_pr(app: dict, base: Path, pr: dict) -> Path:
    from doppel.personas import code_diff
    head = base.parent / "heads" / pr["id"]
    if head.exists():
        shutil.rmtree(head)
    shutil.copytree(base, head)
    sh(["git", "init", "-q"], cwd=head)  # its own repo, or git apply resolves paths from this one
    sh(["git", "apply", "--whitespace=nowarn", str(APPS / app["name"] / "prs" / f"{pr['id']}.patch")], cwd=head)
    shutil.rmtree(head / ".git", ignore_errors=True)
    if not code_diff(base, head).strip():
        raise RuntimeError(f"{pr['id']}: the patch changed nothing; refusing to benchmark an empty PR")
    return head


def prs(app: dict) -> list[dict]:
    return json.loads((APPS / app["name"] / "prs" / "index.json").read_text("utf-8"))


# ---------------------------------------------------------------- steps

def preflight() -> None:
    """Refuse to start unless the sponsor stack is reachable: a missing key or package must never be
    recorded as an app's setup failure."""
    import importlib.util
    import os
    missing = [v for v in ("NEBIUS_API_KEY", "NEBIUS_PROJECT_ID") if not os.getenv(v, "").strip()]
    missing += [f"python package {m}" for m in ("openai", "contree_sdk") if importlib.util.find_spec(m) is None]
    if missing:
        sys.exit("generality: not starting, missing " + ", ".join(missing) +
                 '. Put the keys in .env and run: pip install -e ".[nebius]"')
    from doppel.personas import nemotron_chat
    try:  # one tiny call: proves the key and Token Factory work before any app is judged
        nemotron_chat(os.getenv("PERSONA_MODEL", "nvidia/Nemotron-3_5-Lightning"), "Reply with OK.", temperature=0)
    except Exception as e:
        sys.exit(f"generality: Token Factory call failed, nothing recorded: {type(e).__name__}: {str(e)[:300]}")


def step_init(app: dict, force: bool) -> dict:
    out = RESULTS / app["name"] / "init.json"
    if out.exists() and not force:
        print(f"[{app['name']}] init: done earlier (--force to redo)")
        return load(out)
    from doppel.bootstrap import init
    work = CACHE / app["name"] / "init-work"
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(pristine(app), work)
    trace = CACHE / app["name"] / "init-trace"
    if trace.exists():
        shutil.rmtree(trace)
    print(f"[{app['name']}] init: doppel init on Nebius (Nemotron 3 Ultra drafts, the twin checks)")
    t0 = time.perf_counter()
    r = init(work, backend="nebius", resume=False, trace=trace)
    if r.get("infra"):  # the sandbox API dropped out: the setup was never tried, so record nothing
        sys.exit(f"[{app['name']}] init: the sandbox API timed out or dropped the connection; nothing recorded. "
                 f"Rerun (raise DOPPEL_HTTP_TIMEOUT if uploads are slow).\n{r.get('error', '')[-500:]}")
    result = {"ok": bool(r.get("ok")), "attempts": r.get("attempts"), "personas": r.get("personas"),
              "coverage": r.get("coverage"), "noise": r.get("noise", []), "flaky": r.get("flaky", []),
              "error": (r.get("error") or "")[-1500:], "infra": bool(r.get("infra")),
              "notes": r.get("notes", ""), "seconds": round(time.perf_counter() - t0, 1),
              "date": str(date.today())}
    if result["ok"]:
        gen = APPS / app["name"] / "generated"
        gen.mkdir(parents=True, exist_ok=True)
        for name, text in r["files"].items():
            (gen / name).write_text(text, encoding="utf-8")
        result["files"] = sorted(r["files"])
    save(out, result)
    cov = f"{result['coverage']:.0%}" if isinstance(result["coverage"], (int, float)) else "-"
    print(f"[{app['name']}] init: {'OK' if result['ok'] else 'FAILED'} after {result['attempts']} attempt(s), "
          f"served {cov}, {result['seconds']} s" + ("" if result["ok"] else f"\n{result['error']}"))
    return result


def generated_setup(app: dict) -> Path:
    gen = APPS / app["name"] / "generated"
    init = load(RESULTS / app["name"] / "init.json")
    if not init or not init.get("ok") or not all((gen / f).exists() for f in TWIN_FILES):
        sys.exit(f"[{app['name']}] no working setup from doppel init yet: run `init` first. "
                 "The benchmark never substitutes a hand-written setup.")
    return gen


def step_aa(app: dict, force: bool, n: int) -> dict:
    out = RESULTS / app["name"] / "aa.json"
    if out.exists() and not force:
        print(f"[{app['name']}] A/A: done earlier (--force to redo)")
        return load(out)
    import run_bench
    base = with_setup(app, generated_setup(app), "base")
    twin = with_setup(app, generated_setup(app), "base-again")
    pr = {"id": "aa", "title": "No change", "body": "This pull request changes nothing.", "expect": ""}
    print(f"[{app['name']}] A/A: Lightning users, Nebius twin, same code in both worlds")
    r = run_bench.arm_doppel(base, twin, pr, "nebius", n, None, use_judge=False, rehearse=True)
    rows = r["findings"]
    result = {"false_differences": len(rows), "findings": rows, "coverage": r.get("coverage"),
              "coverage_before_rehearsal": r.get("coverage_before_rehearsal"),
              "personas": r.get("persona_count"), "failed_personas": r.get("failed_personas"),
              "usd": r.get("usd"), "seconds": r.get("seconds"), "date": str(date.today())}
    save(out, result)
    print(f"[{app['name']}] A/A: {len(rows)} false difference(s), {served(result['coverage'])}")
    return result


def step_prs(app: dict, force: bool, n: int, arms: set[str], only: set[str] | None) -> list[dict]:
    import run_bench
    base = with_setup(app, generated_setup(app), "base")
    done = []
    for pr in prs(app):
        if only and pr["id"] not in only:
            continue
        out = RESULTS / app["name"] / f"{pr['id']}.json"
        result = load(out) or {"pr": pr}
        head = apply_pr(app, base, pr)
        for arm in ("tests", "doppel", "review"):
            if arm not in arms or (arm in result and not force):
                continue
            if arm == "tests" and not app.get("tests"):
                continue
            print(f"[{app['name']}] {pr['id']}: {arm}")
            try:
                if arm == "tests":
                    result[arm] = arm_tests(app, head)
                elif arm == "doppel":
                    result[arm] = run_bench.arm_doppel(base, head, pr, "nebius", n, None, guided=True)
                else:
                    result[arm] = run_bench.arm_review(base, head, pr)
            except Exception as e:  # one broken arm must not lose the others; rerun picks it up
                print(f"  {arm} failed: {type(e).__name__}: {str(e)[:300]}")
                continue
            save(out, {**result, "pr": pr})
        marks = " ".join(f"{k}={'CAUGHT' if result[k]['caught'] else '-'}"
                         for k in ("tests", "doppel", "review") if k in result)
        print(f"[{app['name']}] {pr['id']} ({pr['kind']}): {marks}")
        done.append(result)
    return done


def arm_tests(app: dict, head: Path) -> dict:
    t0 = time.perf_counter()
    p = subprocess.run(app["tests"], shell=True, cwd=head, text=True, capture_output=True, timeout=900)
    tail = (p.stdout.strip().splitlines() or [""])[-1]
    if p.returncode not in (0, 1):  # 2+ = pytest could not run (missing deps): not a verdict
        return {"caught": False, "unavailable": True, "summary": tail or p.stderr.strip()[-300:],
                "seconds": round(time.perf_counter() - t0, 1)}
    return {"caught": p.returncode == 1, "summary": tail,
            "failed": sorted(set(re.findall(r"^FAILED (\S+)", p.stdout, re.M))),
            "seconds": round(time.perf_counter() - t0, 1)}


def served(cov: dict | None) -> str:
    if not cov or not cov.get("requests"):
        return "nothing served"
    return f"{cov['served']}/{cov['requests']} requests served ({cov['served'] / cov['requests']:.0%})"


# ---------------------------------------------------------------- summary

def summary() -> Path:
    rows, notes = [], []
    for app_dir in sorted(p for p in APPS.iterdir() if (p / "app.json").exists()):
        app = app_info(app_dir.name)
        init = load(RESULTS / app["name"] / "init.json")
        aa = load(RESULTS / app["name"] / "aa.json")
        results = [load(RESULTS / app["name"] / f"{pr['id']}.json") for pr in prs(app)]
        results = [r for r in results if r]
        def score(arm, kind, results=results):
            got = [r for r in results if arm in r and r["pr"]["kind"] == kind and not r[arm].get("unavailable")]
            return f"{sum(r[arm]['caught'] for r in got)}/{len(got)}" if got else "-"
        setup = "-" if not init else (f"✅ {init['attempts']} try" + ("" if init["attempts"] == 1 else "s")
                                      if init["ok"] else "❌ failed")
        rows.append([app["name"], app["framework"], app["login"], app["database"], setup,
                     served(aa["coverage"]).split(" (")[-1].rstrip(")") if aa else "-",
                     str(aa["false_differences"]) if aa else "-",
                     score("doppel", "regression"), score("doppel", "safe"),
                     score("review", "regression"), score("tests", "regression")])
        if init and not init["ok"]:
            notes.append(f"- **{app['name']}** setup failed: {init['error'].strip().splitlines()[0][:200]}")
    head = ["App", "Framework", "Login", "Database", "`doppel init`", "Served (A/A)", "False diffs (A/A)",
            "Doppel caught", "Doppel false alarms", "AI review caught", "Tests caught"]
    md = ["# Generality benchmark", "",
          "Python apps Doppel was never tuned for, each set up by `doppel init` with no hand edits.",
          "Everything runs on Nebius sandboxes with NVIDIA Nemotron models via Token Factory.", "",
          "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    md += ["| " + " | ".join(r) + " |" for r in rows]
    md += ["", "Served = share of the invented users' requests the app answered (not 4xx/5xx) on the A/A run.",
           "False diffs = differences between two runs of the same code: every one is noise.",
           "Doppel false alarms are counted on the safe PRs (a regression verdict on a safe change)."]
    if notes:
        md += ["", "## Setup failures", "", *notes]
    out = RESULTS / "README.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    return out


# ---------------------------------------------------------------- selfcheck (harness only)

def selfcheck(app: dict) -> int:
    """Hand-written setup and users, local backend, no models: proves the patches and the harness,
    never writes to bench/results."""
    import run_bench
    kit = APPS / app["name"] / "selfcheck"
    base = with_setup(app, kit, "selfcheck-base")
    personas = str(kit / "personas.json")
    twin = with_setup(app, kit, "selfcheck-again")
    aa = run_bench.arm_doppel(base, twin, {"id": "aa", "title": "", "body": "", "expect": ""}, "local", 0,
                              personas, use_judge=False, rehearse=False)
    print(f"[selfcheck] A/A: {len(aa['findings'])} difference(s), {served(aa['coverage'])}")
    for f in aa["findings"]:
        print(f"    {f['persona']}: {f['request']} {f['detail']}")
    bad = len(aa["findings"])
    for pr in prs(app):
        head = apply_pr(app, base, pr)
        r = run_bench.arm_doppel(base, head, pr, "local", 0, personas, use_judge=False, rehearse=False)
        on_target = [f for f in r["findings"] if f["on_target"]]
        ok = bool(on_target) if pr["kind"] == "regression" else not r["findings"]
        bad += not ok
        print(f"[selfcheck] {pr['id']} ({pr['kind']}): {len(r['findings'])} difference(s), "
              f"{len(on_target)} on the expected route -> {'OK' if ok else 'PROBLEM'}")
        for f in r["findings"][:4]:
            print(f"    {f['persona']}: {f['request']} {f['detail']}")
    return 1 if bad else 0


def main(argv=None) -> int:
    from dotenv import load_dotenv
    load_dotenv()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("step", choices=["fetch", "init", "aa", "prs", "all", "summary", "selfcheck"])
    ap.add_argument("apps", nargs="*", help="app names under bench/apps (default for summary: all)")
    ap.add_argument("--force", action="store_true", help="redo steps that already have results")
    ap.add_argument("-n", type=int, default=12, help="invented users per run")
    ap.add_argument("--arms", default="tests,doppel,review")
    ap.add_argument("--only", help="comma-separated PR ids")
    a = ap.parse_args(argv)
    if a.step == "summary":
        summary()
        return 0
    if not a.apps:
        ap.error("name at least one app")
    code = 0
    for name in a.apps:
        app = app_info(name)
        if a.step == "fetch":
            print(f"[{name}] {pristine(app)}")
        elif a.step == "selfcheck":
            code |= selfcheck(app)
        else:
            if name == a.apps[0]:
                preflight()
            if a.step in ("init", "all") and not step_init(app, a.force)["ok"]:
                code = 1
                continue
            if a.step in ("aa", "all"):
                step_aa(app, a.force, a.n)
            if a.step in ("prs", "all"):
                step_prs(app, a.force, a.n, set(a.arms.split(",")), set(a.only.split(",")) if a.only else None)
    if a.step != "fetch" and a.step != "selfcheck":
        summary()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
