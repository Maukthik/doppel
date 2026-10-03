"""Re-run only the judge on recorded results: no sandboxes, no new personas, cents per PR.

The twin's recordings and findings in bench/results/*.json stay exactly as they were; the judge
(and the escalation of confirmed suspicions) labels the same findings again. Used when the judge
changes, so the twin's run-to-run variance doesn't get mixed into the comparison.

    python bench/rejudge.py                       # doppel_guided on all 40 PRs
    python bench/rejudge.py --key doppel --only r05-feed-own-posts
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

from doppel.diff import Finding
from doppel.judge import escalate, judge

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_bench as rb  # noqa: E402


def _values(rec: dict, persona: str, request: str):
    """Base and head answers for the first recorded step of this persona with this request."""
    for w in ("base", "head"):
        for r in rec.get(w, []):
            if r["persona"] != persona:
                continue
            for st in r["steps"]:
                if st["request"] == request:
                    yield f"{st['status']} {st['body'][:300]}"
                    break
            break


def rejudge(result: dict, key: str, base: Path, head: Path, pr: dict) -> dict:
    d = result[key]
    rows = d.get("findings", [])
    if not rows:
        return d
    findings = []
    for r in rows:
        vals = list(_values(d.get("recordings", {}), r["persona"], r["request"])) + [None, None]
        findings.append(Finding(r["kind"], r["persona"], 0, r["request"], r["detail"], vals[0], vals[1]))
    judged = judge(findings, rb.pr_text(pr), rb.code_diff(base, head))
    checks = {s["id"]: s for s in d.get("suspicions", []) if s.get("status")}
    verdicts = escalate(judged["verdicts"], checks) if checks else judged["verdicts"]
    expect = re.compile(pr["expect"]) if pr.get("expect") else None
    for r, v in zip(rows, verdicts, strict=True):
        r.setdefault("label_v1", r["label"])
        r["label"], r["why"] = v.label, v.why
    regressions = [r for r in rows if r["label"] == "regression"]
    d["caught"] = any(r["on_target"] for r in regressions) if expect else bool(regressions)
    d["any_regression"] = bool(regressions)
    d["needs_human"] = sum(r["label"] == "needs_human" for r in rows)
    d["summary"] = judged.get("summary", "")
    d["judge_version"] = 2
    d.setdefault("usd", {})["rejudge"] = judged.get("usd", 0.0)
    return d


def main(argv=None) -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--key", default="doppel_guided")
    ap.add_argument("--only", help="comma-separated PR ids")
    a = ap.parse_args(argv)
    prs = {p["id"]: p for p in json.loads((rb.APP / "prs" / "index.json").read_text(encoding="utf-8"))}
    base = rb.upstream()
    for path in sorted(rb.RESULTS.glob("*.json")):
        result = json.loads(path.read_text(encoding="utf-8"))
        pid = result.get("id")
        if pid not in prs or a.key not in result or (a.only and pid not in a.only.split(",")):
            continue
        try:
            result[a.key] = rejudge(result, a.key, base, rb.head_for(base, prs[pid]), prs[pid])
        except Exception as e:  # noqa: BLE001 - keep going, report
            print(f"[rejudge] {pid}: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        path.write_text(json.dumps(result, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        d = result[a.key]
        print(f"[rejudge] {result['kind']:10} {pid:24} {'CAUGHT' if d['caught'] else '-'}"
              f"{'  (flags a safe PR)' if result['kind'] == 'safe' and d['any_regression'] else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
