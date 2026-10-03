"""Turn bench/results/*.json into bench/results/results.md and bench/results/chart.svg.

    python bench/summarize.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

RESULTS = Path(__file__).resolve().parent / "results"
ARMS = [("tests", "App's own test suite (44 tests)"),
        ("review", "AI code review (Nemotron 3 Ultra)"),
        ("doppel", "Doppel (personas + twin + judge)"),
        ("doppel_guided", "Review-guided Doppel")]


def load(folder: Path) -> list[dict]:
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))]
    return sorted(rows, key=lambda r: r["id"])


def mark(r: dict, arm: str) -> str:
    if arm not in r:
        return "·"
    res = r[arm]
    if r["kind"] == "regression":
        return "✅" if res["caught"] else "❌"
    hit = res.get("any_regression", res["caught"]) if arm.startswith("doppel") else res["caught"]
    return "⚠️ false alarm" if hit else "✅"


def score(rows: list[dict], arm: str) -> dict | None:
    have = [r for r in rows if arm in r]
    if not have:
        return None
    reg = [r for r in have if r["kind"] == "regression"]
    safe = [r for r in have if r["kind"] == "safe"]
    alarm = (lambda r: r[arm].get("any_regression", r[arm]["caught"])) if arm.startswith("doppel") else \
        (lambda r: r[arm]["caught"])
    usd = [sum(r[arm]["usd"].values()) if isinstance(r[arm].get("usd"), dict) else r[arm].get("usd", 0.0)
           for r in have]
    secs = [sum(r[arm]["seconds"].values()) if isinstance(r[arm].get("seconds"), dict) else r[arm]["seconds"]
            for r in have]
    return {"caught": sum(r[arm]["caught"] for r in reg), "regressions": len(reg),
            "alarms": sum(bool(alarm(r)) for r in safe), "safe": len(safe),
            "usd": sum(usd) / len(have), "seconds": sum(secs) / len(have)}


def chart(scores: dict[str, dict]) -> str:
    """Two bar groups: regressions caught (out of N) and false alarms (out of M), one bar per arm."""
    arms = [(k, label) for k, label in ARMS if scores.get(k)]
    colors = {"tests": "#8a8f98", "review": "#c9a227", "doppel": "#a996f0", "doppel_guided": "#5b3cc4"}
    w, h, left, top, bar, gap = 720, 320, 40, 40, 40, 8
    group_w = len(arms) * (bar + gap) + 60
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" font-family="system-ui,sans-serif" '
           f'font-size="12">', f'<rect width="{w}" height="{h}" fill="#ffffff"/>']
    base_y = h - 50
    for g, (title, num, den) in enumerate((("Regressions caught", "caught", "regressions"),
                                           ("False alarms on safe PRs", "alarms", "safe"))):
        x0 = left + g * (group_w + 60)
        out.append(f'<text x="{x0}" y="{top - 14}" font-weight="600" fill="#222">{title}</text>')
        for i, (k, _label) in enumerate(arms):
            s = scores[k]
            frac = s[num] / max(s[den], 1)
            bh = frac * (base_y - top)
            x = x0 + i * (bar + gap)
            out.append(f'<rect x="{x}" y="{base_y - bh:.1f}" width="{bar}" height="{bh:.1f}" '
                       f'fill="{colors[k]}" rx="3"/>')
            out.append(f'<text x="{x + bar / 2}" y="{base_y - bh - 6:.1f}" text-anchor="middle" fill="#222">'
                       f'{s[num]}/{s[den]}</text>')
        out.append(f'<line x1="{x0 - 6}" y1="{base_y}" x2="{x0 + len(arms) * (bar + gap)}" y2="{base_y}" '
                   f'stroke="#bbb"/>')
    for i, (k, label) in enumerate(arms):
        y = h - 30 + (i // 2) * 16
        x = left + (i % 2) * 300
        out.append(f'<rect x="{x}" y="{y - 10}" width="12" height="12" fill="{colors[k]}" rx="2"/>')
        out.append(f'<text x="{x + 18}" y="{y}" fill="#333">{label}</text>')
    out.append("</svg>")
    return "\n".join(out)


def main() -> int:
    rows = load(RESULTS)
    if not rows:
        print("no results yet: run bench/run_bench.py first", file=sys.stderr)
        return 1
    held = [r for r in rows if r.get("set") == "heldout"]
    rows = [r for r in rows if r.get("set", "main") == "main"]
    scores = {k: score(rows, k) for k, _ in ARMS}
    md = ["# Stage 5 benchmark: microblog-api", "",
          "30 pull requests against [miguelgrinberg/microblog-api](https://github.com/miguelgrinberg/microblog-api) "
          "(20 with a regression, 10 safe). How the PRs were made and scored: [bench/README.md](../README.md).", "",
          "![results](chart.svg)", "",
          "| | Regressions caught | False alarms on safe PRs | Avg cost per PR | Avg time per PR |",
          "|---|---|---|---|---|"]
    for k, label in ARMS:
        s = scores[k]
        if s is None:
            md.append(f"| {label} | not run | | | |")
            continue
        cost = "$0 (CI minutes)" if k == "tests" else f"${s['usd']:.3f}"
        md.append(f"| {label} | **{s['caught']}/{s['regressions']}** | {s['alarms']}/{s['safe']} | {cost} | "
                  f"{s['seconds']:.0f} s |")
    best = "doppel_guided" if scores.get("doppel_guided") else "doppel"
    if scores["tests"] and scores.get(best):
        reg = [r for r in rows if r["kind"] == "regression" and "tests" in r and best in r]
        both = sum(r["tests"]["caught"] or r[best]["caught"] for r in reg)
        only = [r for r in reg if r[best]["caught"] and not r["tests"]["caught"]]
        name = dict(ARMS)[best]
        md += ["", f"**Tests + {name}: {both}/{len(reg)}.** Caught by it and missed by the tests:", ""]
        md += [f"- `{r['id']}`: {r['bug']}" for r in only] or ["- (none)"]
    md += ["", "## Every PR", "", "| PR | Kind | What it really does | Tests | AI review | Doppel | Guided |",
           "|---|---|---|---|---|---|---|"]
    for r in rows:
        what = r.get("bug") or "(safe)"
        src = f" *({r['source']})*" if r.get("source", "planted") != "planted" else ""
        md.append(f"| `{r['id']}` | {r['kind']} | {what}{src} | {mark(r, 'tests')} | {mark(r, 'review')} | "
                  f"{mark(r, 'doppel')} | {mark(r, 'doppel_guided')} |")
    if held:
        hs = {k: score(held, k) for k, _ in ARMS}
        md += ["", "## Held-out set", "",
               f"{len(held)} more PRs written after the results above were in, and run once on the final version.", "",
               "| | Regressions caught | False alarms on safe PRs |", "|---|---|---|"]
        md += [f"| {label} | " + (f"**{hs[k]['caught']}/{hs[k]['regressions']}** | {hs[k]['alarms']}/{hs[k]['safe']} |"
                                   if hs[k] else "not run | |") for k, label in ARMS]
        md += ["", "| PR | Kind | What it really does | Tests | AI review | Doppel | Guided |",
               "|---|---|---|---|---|---|---|"]
        md += [f"| `{r['id']}` | {r['kind']} | {r.get('bug') or '(safe)'} | {mark(r, 'tests')} | {mark(r, 'review')} | "
               f"{mark(r, 'doppel')} | {mark(r, 'doppel_guided')} |" for r in held]
    md += ["", "✅ right call · ❌ missed the regression · ⚠️ flagged a safe PR · · not run"]
    (RESULTS / "results.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (RESULTS / "chart.svg").write_text(chart({k: v for k, v in scores.items() if v}), encoding="utf-8")
    print("\n".join(md[:12]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
