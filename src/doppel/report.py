"""Turn findings (and, from stage 3, the judge's verdicts) into the Markdown for a PR comment."""
from __future__ import annotations

import json

from doppel.diff import Finding

ICON = {"error": "🔴", "status": "🟠", "body": "🟡", "slow": "🐢"}
TITLE = {"error": "Errors", "status": "Status changes", "body": "Response changes", "slow": "Slower"}
SECTION = {"regression": "🚨 Regressions: not mentioned in the PR",
           "needs_human": "❓ Needs a human",
           "intended": "✅ Intended: matches the PR description"}


def _short(value, n: int = 120) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    text = text if len(text) <= n else text[: n - 1] + "…"
    return text.replace("|", "\\|").replace("`", "'")  # keep the Markdown table intact


def _row(f: Finding, why: str | None = None) -> str:
    cells = [ICON[f.kind], f"{f.persona} (step {f.step})", f"`{f.request}`", f.detail,
             f"`{_short(f.base, 60)}`", f"`{_short(f.head, 60)}`"]
    if why is not None:
        cells.append(why.replace("|", "\\|"))
    return "| " + " | ".join(cells) + " |"


def markdown(findings: list[Finding], personas: int, steps: int, judged: dict | None = None) -> str:
    if not findings:
        return (f"## Doppel: no behavior changes\n\n{personas} personas replayed {steps} requests "
                "against base and head; every response matched.\n")
    replayed = f"{personas} personas replayed {steps} requests against base and head."
    if not judged or not judged.get("verdicts"):
        counts = {k: sum(f.kind == k for f in findings) for k in ICON}
        summary = " · ".join(f"{ICON[k]} {counts[k]} {TITLE[k].lower()}" for k in ICON if counts[k])
        lines = [f"## Doppel: {len(findings)} behavior change(s)", "", f"{replayed} {summary}", "",
                 "| | Persona | Request | What changed | Base | Head |", "|---|---|---|---|---|---|"]
        lines += [_row(f) for f in findings]
        return "\n".join(lines) + "\n"

    verdicts = judged["verdicts"]
    by_label = {label: [(f, v) for f, v in zip(findings, verdicts, strict=True) if v.label == label]
                for label in SECTION}
    # the judge shares one verdict per distinct change, so count verdicts, not rows
    n_reg, n_hum, n_int = (len({id(v) for _, v in by_label[k]}) for k in ("regression", "needs_human", "intended"))
    head = f"## Doppel: {n_reg} regression(s) the PR doesn't mention" if n_reg else "## Doppel: no regressions"
    lines = [head, "", f"{replayed} Distinct changes: {n_reg} regression · {n_hum} need a human · "
             f"{n_int} intended.", ""]
    if judged.get("summary"):
        lines += [f"> {judged['summary']}", ""]
    for label, title in SECTION.items():
        rows = by_label[label]
        if not rows:
            continue
        table = ["| | Persona | Request | What changed | Base | Head | Why |", "|---|---|---|---|---|---|---|"]
        table += [_row(f, v.why) for f, v in rows]
        if label == "intended":  # keep the comment short: intended changes fold away
            lines += [f"<details><summary>{title} ({len(rows)})</summary>", "", *table, "", "</details>", ""]
        else:
            lines += [f"### {title}", "", *table, ""]
    if judged.get("model"):
        lines.append(f"<sub>Judged by {judged['model'].split('/')[-1]} · {judged.get('groups', '?')} distinct "
                     f"changes · ${judged.get('usd', 0):.4f}</sub>")
    return "\n".join(lines) + "\n"
