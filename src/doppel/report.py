"""Turn findings into the Markdown that goes into a PR comment."""
from __future__ import annotations

import json

from doppel.diff import Finding

ICON = {"error": "🔴", "status": "🟠", "body": "🟡", "slow": "🐢"}
TITLE = {"error": "Errors", "status": "Status changes", "body": "Response changes", "slow": "Slower"}


def _short(value, n: int = 120) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    text = text if len(text) <= n else text[: n - 1] + "…"
    return text.replace("|", "\\|").replace("`", "'")  # keep the Markdown table intact


def markdown(findings: list[Finding], personas: int, steps: int) -> str:
    if not findings:
        return (f"## Doppel: no behavior changes\n\n{personas} personas replayed {steps} requests "
                "against base and head; every response matched.\n")
    counts = {k: sum(f.kind == k for f in findings) for k in ICON}
    summary = " · ".join(f"{ICON[k]} {counts[k]} {TITLE[k].lower()}" for k in ICON if counts[k])
    lines = [f"## Doppel: {len(findings)} behavior change(s)", "",
             f"{personas} personas replayed {steps} requests against base and head. {summary}", "",
             "| | Persona | Request | What changed | Base | Head |", "|---|---|---|---|---|---|"]
    for f in findings:
        lines.append(f"| {ICON[f.kind]} | {f.persona} (step {f.step}) | `{f.request}` | {f.detail} | "
                     f"`{_short(f.base, 60)}` | `{_short(f.head, 60)}` |")
    return "\n".join(lines) + "\n"
