"""Recompute the README's results table from the recorded results: no keys, no network, nothing rewritten.

    python bench/verify_numbers.py                                   # the numbers the README quotes
    python bench/verify_numbers.py --guided bench/results/guided-rerun-grouping-2   # another rejudge

The app's tests and the AI reviewer come from the first full run (bench/results/<id>.json); review-guided
Doppel comes from the folder given with --guided. A regression counts as caught when the arm caught it; a
safe PR counts as a false alarm when the arm flagged anything on it.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

BENCH = Path(__file__).resolve().parent
RESULTS = BENCH / "results"


def load(folder: Path, pr_id: str) -> dict:
    path = folder / f"{pr_id}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def flagged(arm: dict | None, kind: str) -> bool | None:
    if not isinstance(arm, dict):
        return None
    if kind == "safe" and "any_regression" in arm:   # Doppel: anything judged a regression is a false alarm
        return bool(arm["any_regression"])
    return bool(arm.get("caught"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--guided", type=Path, default=RESULTS / "guided-rerun-grouping",
                    help="folder with the review-guided Doppel results (default: the one the README quotes)")
    ap.add_argument("--chart", type=Path,
                    help="also write the bar chart of these numbers (the replay site shows it), e.g. "
                         "bench/results/guided-rerun-grouping/chart.svg")
    a = ap.parse_args(argv)
    if a.chart:
        import sys
        sys.path.insert(0, str(BENCH))
        from summarize import chart

        from doppel.site import bench_scores
        scores = bench_scores(RESULTS, guided=a.guided)
        a.chart.write_text(chart({k: v for k, v in scores.items() if k in ("tests", "review", "doppel_guided")}),
                           encoding="utf-8")
    prs = json.loads((BENCH / "microblog" / "prs" / "index.json").read_text(encoding="utf-8"))
    print(f"Review-guided Doppel from {a.guided}\n")
    for set_name in ("main", "heldout", "consequence"):
        rows = [p for p in prs if p.get("set", "main") == set_name]
        reg = [p for p in rows if p["kind"] == "regression"]
        safe = [p for p in rows if p["kind"] == "safe"]
        arms = {
            "App's own tests": lambda p: load(RESULTS, p["id"]).get("tests"),
            "AI code review": lambda p: load(RESULTS, p["id"]).get("review"),
            "Review-guided Doppel": lambda p: load(a.guided, p["id"]).get("doppel_guided"),
        }
        hits = {name: {p["id"]: flagged(get(p), p["kind"]) for p in rows} for name, get in arms.items()}
        print(f"== {set_name}: {len(reg)} regressions, {len(safe)} safe")
        print(f"   {'':24} {'caught':>8} {'false alarms':>13}   missed")
        for name, h in hits.items():
            if all(h[p["id"]] is None for p in rows):
                print(f"   {name:24} {'(not run on this set)':>22}")
                continue
            caught = [p["id"] for p in reg if h[p["id"]]]
            alarms = [p["id"] for p in safe if h[p["id"]]]
            missed = [p["id"] for p in reg if not h[p["id"]]]
            print(f"   {name:24} {len(caught):>4}/{len(reg):<3} {len(alarms):>8}/{len(safe):<4}   "
                  f"{', '.join(missed) or '-'}")
        doppel = hits["Review-guided Doppel"]
        for other in ("App's own tests", "AI code review"):
            h = hits[other]
            if all(h[p["id"]] is None for p in rows):
                continue
            both = sum(bool(h[p["id"]] or doppel[p["id"]]) for p in reg)
            alarms = sum(bool(h[p["id"]] or doppel[p["id"]]) for p in safe)
            label = {"App's own tests": "Tests + Doppel", "AI code review": "AI review + Doppel"}[other]
            print(f"   {label:24} {both:>4}/{len(reg):<3} {alarms:>8}/{len(safe):<4}")
        tests = hits["App's own tests"]
        if not all(tests[p["id"]] is None for p in rows):
            missed = [p["id"] for p in reg if not tests[p["id"]]]
            print(f"   Of the {len(missed)} regressions the tests missed, Doppel caught "
                  f"{sum(bool(doppel[i]) for i in missed)}.")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
