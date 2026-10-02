"""Stage 5 benchmark: does Doppel catch regressions that a real app's own test suite misses?

Target app: miguelgrinberg/microblog-api (Flask + SQLAlchemy, 44 upstream tests), pinned below.
30 pull requests in bench/microblog/prs: 20 carry a regression (3 are upstream bug fixes
reverted, 17 are planted), 10 are safe (refactors, or behavior changes their description announces).

Three arms per PR, each scored the same way:
  tests   the app's own pytest suite, run on the PR's code          caught = any test fails
  doppel  Nemotron personas -> twin (base vs head) -> Nemotron judge
          caught = a change judged "regression" on a request matching the PR's expected route
  review  Nemotron 3 Ultra reads the app source, the diff and the PR description, and says
          "regression" or "safe" (an AI code reviewer)                  caught = says regression
On safe PRs, any "regression" from doppel or review is a false alarm.

    python bench/run_bench.py                         # all arms, nebius twin (needs .env)
    python bench/run_bench.py --arms tests            # offline: just the app's own tests
    python bench/run_bench.py --backend local --only r05-feed-own-posts
    python bench/summarize.py                         # results/*.json -> results.md + chart.svg

Results are saved per PR in bench/results/<id>.json; finished arms are skipped on rerun
(use --force to redo), so an interrupted run resumes without spending credits twice.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from doppel.diff import compare
from doppel.judge import Verdict, judge
from doppel.personas import code_diff, cost_usd, generate, nemotron_chat, parse_json, read_source
from doppel.rehearsal import coverage, repair
from doppel.spec import TwinSpec
from doppel.suspects import check, suspect

HERE = Path(__file__).resolve().parent
UPSTREAM = "https://github.com/miguelgrinberg/microblog-api.git"
PIN = "e250d71"  # upstream main on 7 Jul 2024, the latest commit when the benchmark was written
APP = HERE / "microblog"
CACHE = HERE / ".cache"
RESULTS = HERE / "results"
REVIEW_MODEL = os.getenv("REVIEW_MODEL", "nvidia/Nemotron-3-Ultra-550b-a55b")

REVIEW_PROMPT = """You are a senior engineer reviewing a pull request to the web API below.
Decide whether the PR introduces a regression: a behavior change that users or API clients would
notice and that the PR description does not announce (wrong data, lost validation, new errors,
removed or renamed fields, security holes). Changes the description announces are not regressions.

Return ONLY JSON: {{"verdict": "regression" | "safe", "why": "one or two sentences"}}

PR description:
{pr}

PR diff:
{diff}

Full source of the app before the PR:
{source}
"""


def sh(args, cwd=None, check=True, **kw):
    return subprocess.run(args, cwd=cwd, check=check, text=True, capture_output=True, **kw)


def upstream() -> Path:
    """Pristine microblog-api at the pinned commit, plus the twin files a Doppel user would add."""
    src = CACHE / "microblog-api"
    if not (src / ".git").exists():
        CACHE.mkdir(exist_ok=True)
        sh(["git", "clone", "-q", "-c", "core.autocrlf=false", UPSTREAM, str(src)])  # LF, like the patches
    sh(["git", "checkout", "-q", PIN], cwd=src)
    base = CACHE / "base"
    if base.exists():
        shutil.rmtree(base)
    shutil.copytree(src, base, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.sqlite"))
    for f in (APP / "twin").iterdir():
        shutil.copy(f, base / f.name)
    return base


def head_for(base: Path, pr: dict) -> Path:
    head = CACHE / "heads" / pr["id"]
    if head.exists():
        shutil.rmtree(head)
    shutil.copytree(base, head)
    # its own repo: inside another git repo (this one), `git apply` resolves paths from that repo's
    # root and silently skips them, which left every head identical to base in the first runs
    sh(["git", "init", "-q"], cwd=head)
    sh(["git", "apply", "--whitespace=nowarn", str(APP / "prs" / f"{pr['id']}.patch")], cwd=head)
    shutil.rmtree(head / ".git", ignore_errors=True)
    if not code_diff(base, head).strip():
        raise RuntimeError(f"{pr['id']}: the patch changed nothing; refusing to benchmark an empty PR")
    return head


def pr_text(pr: dict) -> str:
    return f"# {pr['title']}\n\n{pr['body']}\n"


# ---------------------------------------------------------------- arms

def arm_tests(app_dir: Path, python: str) -> dict:
    t0 = time.perf_counter()
    p = sh([python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:logging"], cwd=app_dir, check=False,
           timeout=900)
    failed = sorted(set(re.findall(r"^FAILED (\S+)", p.stdout, re.M)))
    errors = sorted(set(re.findall(r"^ERROR (\S+)", p.stdout, re.M)))
    last = (p.stdout.strip().splitlines() or [""])[-1]
    return {"caught": p.returncode != 0, "failed": failed + errors, "summary": last,
            "seconds": round(time.perf_counter() - t0, 1)}


def arm_doppel(base: Path, head: Path, pr: dict, backend: str, n: int, personas_file: str | None,
               use_judge: bool = True, rehearse: bool = True, guided: bool = False) -> dict:
    t0 = time.perf_counter()
    spec = TwinSpec.load(head / "doppel.toml")
    sus = {"suspicions": [], "usd": 0.0}
    if guided:  # the AI reviewer suspects; probe personas are written for each suspicion
        sus = suspect(pr_text(pr), code_diff(base, head), read_source(base))
    if personas_file:
        personas = json.loads(Path(personas_file).read_text(encoding="utf-8"))
        gen = {"model": "fixed file", "usd": 0.0, "tokens": [0, 0]}
    else:
        gen = generate(base, n=n, head_dir=head, guide=spec.persona_guide, suspicions=sus["suspicions"])
        personas = gen["personas"]
    t1 = time.perf_counter()
    if backend == "local":
        from doppel.backends.local import run_twin
    else:
        from doppel.backends.nebius import run_twin
    fix = {"repaired": [], "usd": 0.0}

    def refine(ps, recordings):
        fix.update(repair(ps, recordings, base, guide=spec.persona_guide, login=spec.login))
        return fix["personas"]
    worlds = run_twin(base, head, spec, personas, refine=refine if rehearse else None)
    personas = worlds.get("personas", personas)
    cov = coverage(worlds["base"])
    before = coverage(worlds["rehearsal"]) if worlds.get("rehearsal") is not None else None
    t2 = time.perf_counter()
    findings = compare(worlds["base"], worlds["head"], spec.ignore, spec.slow_ratio, spec.slow_min_ms)
    if findings and use_judge:
        judged = judge(findings, pr_text(pr), code_diff(base, head))
    else:  # no judge: every change counts, which is what a plain diff tool would report
        judged = {"verdicts": [Verdict("unjudged", "") for _ in findings], "summary": "", "usd": 0.0}
    chk = check(sus["suspicions"], findings, judged["verdicts"], personas, worlds["base"], worlds["head"]) \
        if guided else {"checks": {}, "usd": 0.0}
    t3 = time.perf_counter()
    expect = re.compile(pr["expect"]) if pr.get("expect") else None
    rows = [{"kind": f.kind, "persona": f.persona, "request": f.request, "detail": f.detail,
             "label": v.label, "why": v.why, "on_target": bool(expect and expect.search(f.request))}
            for f, v in zip(findings, judged["verdicts"], strict=True)]
    regressions = [r for r in rows if r["label"] in ("regression", "unjudged")]
    return {
        "detected": any(r["on_target"] for r in rows) if expect else bool(rows),
        "caught": any(r["on_target"] for r in regressions) if expect else bool(regressions),
        "any_regression": bool(regressions),
        "needs_human": sum(r["label"] == "needs_human" for r in rows),
        "findings": rows, "summary": judged.get("summary", ""),
        "personas": personas, "persona_count": len(worlds["base"]), "failed_personas": worlds.get("failed", []),
        "coverage": cov, "coverage_before_rehearsal": before, "repaired": fix["repaired"],
        "recordings": {w: [{"persona": r["persona"], "steps": [
            {"request": st["request"], "status": st["status"],
             "body": json.dumps(st["body"], ensure_ascii=False, default=str)[:600]} for st in r["steps"]]}
            for r in worlds[w]] for w in ("base", "head")},
        "steps": sum(len(r["steps"]) for r in worlds["base"]),
        "suspicions": [{**x, **chk["checks"].get(x["id"], {})} for x in sus["suspicions"]],
        "probes": sum(bool(p.get("probe")) for p in personas),
        "usd": {"suspect": sus["usd"], "check": chk["usd"],
                "personas": gen["usd"], "rehearsal": fix["usd"], "judge": judged.get("usd", 0.0),
                "sandbox_reported": worlds.get("sandbox_cost", 0.0)},
        "seconds": {"personas": round(t1 - t0, 1), "twin": round(t2 - t1, 1), "judge": round(t3 - t2, 1)},
    }


def arm_review(base: Path, head: Path, pr: dict) -> dict:
    t0 = time.perf_counter()
    prompt = REVIEW_PROMPT.format(pr=pr_text(pr), diff=code_diff(base, head), source=read_source(base))
    text, tin, tout = nemotron_chat(REVIEW_MODEL, prompt, temperature=0.1)
    raw = parse_json(text, key="verdict") or {}
    verdict = str(raw.get("verdict", "")).lower() if isinstance(raw, dict) else ""
    return {"caught": verdict == "regression", "verdict": verdict or "unparsed",
            "why": str(raw.get("why", ""))[:600] if isinstance(raw, dict) else text[:600],
            "usd": cost_usd(REVIEW_MODEL, tin, tout), "seconds": round(time.perf_counter() - t0, 1)}


# ---------------------------------------------------------------- main

def main(argv=None) -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--arms", default="tests,doppel,review")
    ap.add_argument("--backend", choices=["local", "nebius"], default="nebius")
    ap.add_argument("--only", help="comma-separated PR ids")
    ap.add_argument("-n", type=int, default=12, help="personas per PR")
    ap.add_argument("--personas", help="use this persona file for every PR instead of generating")
    ap.add_argument("--guided", action="store_true",
                    help="review-guided twin: Nemotron Ultra suspects, probe personas prove or refute (doppel_guided)")
    ap.add_argument("--no-judge", action="store_true", help="skip the judge (offline plumbing check)")
    ap.add_argument("--no-rehearse", action="store_true", help="don't rehearse and repair personas on base first")
    ap.add_argument("--app-python", default=sys.executable, help="interpreter with the app's requirements")
    ap.add_argument("--out", default=str(RESULTS))
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    arms = [x.strip() for x in a.arms.split(",") if x.strip()]
    prs = json.loads((APP / "prs" / "index.json").read_text(encoding="utf-8"))
    if a.only:
        wanted = set(a.only.split(","))
        prs = [p for p in prs if p["id"] in wanted]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    base = upstream()
    if "tests" in arms:
        check = arm_tests(base, a.app_python)
        print(f"[bench] upstream suite on base: {check['summary']}")
        if check["caught"]:
            print("[bench] the base code must pass its own tests first", file=sys.stderr)
            return 2

    for pr in prs:
        path = out / f"{pr['id']}.json"
        result = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        result.update({k: pr[k] for k in ("id", "kind", "title", "source", "bug", "expect")})
        head = head_for(base, pr)
        for arm in arms:
            key = arm
            if arm == "doppel":
                key += "_guided" if a.guided else ""
                key += "_fixed" if a.personas else ""
                key += "_nojudge" if a.no_judge else ""
            if key in result and not a.force:
                continue
            try:
                if arm == "tests":
                    result[key] = arm_tests(head, a.app_python)
                elif arm == "doppel":
                    result[key] = arm_doppel(base, head, pr, a.backend, a.n, a.personas, not a.no_judge,
                                             not a.no_rehearse and not a.personas, a.guided)
                    result[key]["backend"] = a.backend
                elif arm == "review":
                    result[key] = arm_review(base, head, pr)
            except Exception as e:  # noqa: BLE001 - record and keep going; rerun picks it up
                print(f"[bench] {pr['id']} {arm}: {type(e).__name__}: {e}", file=sys.stderr)
                result.setdefault("errors", {})[key] = f"{type(e).__name__}: {str(e)[:1500]}"
                path.write_text(json.dumps(result, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
                continue
            result.get("errors", {}).pop(key, None)
            path.write_text(json.dumps(result, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        cov = result.get("doppel_guided" if a.guided else "doppel", {}).get("coverage")
        served = f" served={cov['served']}/{cov['requests']}" if cov else ""
        marks = " ".join(f"{k}={'CAUGHT' if result[k]['caught'] else '-'}"
                         for k in ("tests", "review", "doppel", "doppel_guided", "doppel_fixed", "doppel_fixed_nojudge")
                         if k in result)
        print(f"[bench] {pr['kind']:10} {pr['id']:24} {marks}{served}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
