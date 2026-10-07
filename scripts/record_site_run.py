"""Record one benchmark PR as a full run for the replay site, with the twin's per-sandbox timeline.

    python scripts/record_site_run.py r17-hash-leak

It rebuilds microblog-api at the benchmark's pinned commit (base) and with the PR's patch (head),
reuses the users the benchmark wrote for that PR (minus its probes; guided mode writes fresh ones),
and runs `doppel run --backend nebius --guided` with the PR description. The run lands in
site/runs/<id>-nebius.json and becomes the source of that PR's gallery entry, which keeps the
benchmark's tests and AI-review results for comparison. Needs NEBIUS_API_KEY and NEBIUS_PROJECT_ID
(read from .env). Costs about what one guided benchmark PR costs (~$0.25).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench"))
sys.path.insert(0, str(ROOT / "src"))

import run_bench  # noqa: E402  (bench/run_bench.py: the same base and head the benchmark used)

from doppel.suspects import is_probe  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("pr", help="benchmark PR id, e.g. r17-hash-leak")
    ap.add_argument("--backend", choices=["nebius", "local"], default="nebius")
    ap.add_argument("--no-guided", action="store_true", help="skip the AI reviewer and its probe users")
    ap.add_argument("--no-gallery", action="store_true", help="don't point the gallery entry at the new run")
    a = ap.parse_args(argv)

    index = {p["id"]: p for p in json.loads((run_bench.APP / "prs" / "index.json").read_text("utf-8"))}
    if a.pr not in index:
        sys.exit(f"unknown PR {a.pr}; choose from: {', '.join(sorted(index))}")
    pr = index[a.pr]
    result_path = ROOT / "bench" / "results" / f"{a.pr}.json"
    result = json.loads(result_path.read_text("utf-8"))
    arm = result.get("doppel_guided") or result.get("doppel")
    personas = [p for p in arm["personas"] if not is_probe(p)]

    print(f"[record] building base and head for {a.pr} (microblog-api @ {run_bench.PIN})")
    base = run_bench.upstream()
    head = run_bench.head_for(base, pr)
    work = run_bench.CACHE / "record" / a.pr
    work.mkdir(parents=True, exist_ok=True)
    (work / "personas.json").write_text(json.dumps(personas, indent=2), encoding="utf-8")
    (work / "PR.md").write_text(run_bench.pr_text(pr), encoding="utf-8")

    out_dir = ROOT / "site" / "runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{a.pr}-{a.backend}.json"
    cmd = [sys.executable, "-m", "doppel.cli", "run", "--backend", a.backend, "--base", str(base), "--head", str(head),
           "--personas", str(work / "personas.json"), "--json", str(out), "--out", str(work / "report.md")]
    if a.backend == "nebius" or not a.no_guided:
        cmd += ["--pr", str(work / "PR.md")]
    if not a.no_guided:
        cmd += ["--guided"]
    print(f"[record] {len(personas)} users from the benchmark; running the twin on {a.backend} (about 1-3 minutes)")
    code = subprocess.run(cmd, cwd=ROOT).returncode
    if not out.exists():
        print(f"[record] the run failed before writing {out.name} (exit {code}); nothing changed in the gallery")
        return 2

    run = json.loads(out.read_text("utf-8"))
    tl = (run.get("worlds") or {}).get("timeline") or {}
    people = [e for e in tl.get("events", []) if e.get("kind") == "persona"]
    print(f"[record] wrote {out.relative_to(ROOT)}: {len(run.get('findings', []))} differences, "
          f"{len(people)} sandbox runs on the timeline, {tl.get('total', 0):.1f} s inside the twin")

    if not a.no_gallery:
        gallery_path = ROOT / "site" / "gallery.json"
        gallery = json.loads(gallery_path.read_text("utf-8"))
        where = "Nebius" if a.backend == "nebius" else "the local backend"
        bench_file = f"bench/results/{a.pr}.json"
        for entry in gallery["runs"]:
            if bench_file in (entry.get("source"), entry.get("compare")):
                entry["compare"] = bench_file
                entry["source"] = f"site/runs/{out.name}"
                entry["recorded"] = (f"Recorded on {where}, {date.today():%d %b %Y}, with the users the benchmark "
                                     "wrote for this PR. Tests and AI review results are from the benchmark.")
                gallery_path.write_text(json.dumps(gallery, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                print(f"[record] gallery entry '{entry['slug']}' now shows this run; build with: doppel site")
                break
        else:
            print("[record] no gallery entry uses this PR; add one to site/gallery.json to publish it")
    return 0 if code in (0, 1) else code


if __name__ == "__main__":
    raise SystemExit(main())
