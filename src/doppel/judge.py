"""Stage 3: Nemotron Ultra judges every behavior change against what the pull request says it does.

Each finding gets a label:
  intended     the PR description (or an obvious consequence of it) explains this change
  regression   behavior changed that the PR does not mention and a user would notice
  needs_human  can't tell from the description and the diff

Identical changes (the same field added on ten list items, the same total change seen by two
steps) are grouped first, so the model judges each distinct change once and the call stays cheap.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from doppel.diff import Finding
from doppel.personas import cost_usd, nemotron_chat, parse_json

LABELS = ("regression", "needs_human", "intended")

PROMPT = """You review a pull request. A twin of the app replayed the same synthetic users against the
code before the PR (base) and after it (head). Below are the behavior changes it observed.

For EACH change id, decide:
- "intended": the PR description says it, or it follows directly from what it says.
- "regression": a behavior change the description does not mention and a user or client would
  notice (different money amounts, lost validation, new errors, removed or renamed fields, slower).
  "Tidies up" or "refactor" in the description never makes a behavior change intended.
- "needs_human": you cannot tell from the description and the diff.

Return ONLY JSON:
{{"verdicts": [{{"id": "c1", "label": "intended|regression|needs_human",
                "why": "one sentence a reviewer can check, naming the code that causes it"}}],
 "summary": "one sentence for the top of the PR comment"}}

PR description:
{pr}

PR diff:
{diff}

Behavior changes:
{changes}
"""


@dataclass
class Verdict:
    label: str
    why: str


def group(findings: list[Finding]) -> dict[str, list[int]]:
    """Group findings that show the same change: same kind and same 'what changed' text
    (slow findings: same route, with ids and query ignored)."""
    groups: dict[tuple, list[int]] = {}
    for i, f in enumerate(findings):
        method, _, path = f.request.partition(" ")
        route = "/".join("{id}" if part.isdigit() else part for part in path.split("?")[0].split("/"))
        # "*.in_stock (added) x10" on a list and "in_stock (added)" on one item are the same change
        detail = re.sub(r" x\d+", "", f.detail).replace("*.", "")
        key = (f.kind, detail if f.kind != "slow" else f"{method} {route}")
        groups.setdefault(key, []).append(i)
    return {f"c{n + 1}": idx for n, idx in enumerate(groups.values())}


def describe(findings: list[Finding], groups: dict[str, list[int]]) -> str:
    lines = []
    for cid, idx in groups.items():
        f = findings[idx[0]]
        seen = sorted({findings[i].request for i in idx})
        lines.append(json.dumps({"id": cid, "kind": f.kind, "what": f.detail, "requests": seen[:5],
                                 "personas": sorted({findings[i].persona for i in idx})[:5],
                                 "base": f.base, "head": f.head}, ensure_ascii=False, default=str)[:1500])
    return "\n".join(lines)


def judge(findings: list[Finding], pr_text: str, diff: str, model: str | None = None,
          chat=nemotron_chat) -> dict:
    """Returns {"verdicts": [Verdict per finding, same order], "summary", "model", "usd"}."""
    if not findings:
        return {"verdicts": [], "summary": "No behavior changes.", "model": None, "usd": 0.0}
    model = model or os.getenv("JUDGE_MODEL", "nvidia/Nemotron-3-Ultra-550b-a55b")
    groups = group(findings)
    prompt = PROMPT.format(pr=pr_text.strip() or "(no description)", diff=diff[:60_000] or "(no diff)",
                           changes=describe(findings, groups))
    text, tin, tout = chat(model, prompt, temperature=0.1)
    raw = parse_json(text, key="verdicts") or {}
    by_id = {}
    for v in raw.get("verdicts", []) if isinstance(raw, dict) else []:
        if isinstance(v, dict) and v.get("label") in LABELS:
            by_id[str(v.get("id"))] = Verdict(v["label"], str(v.get("why", ""))[:400])
    verdicts: list[Verdict | None] = [None] * len(findings)
    for cid, idx in groups.items():
        verdict = by_id.get(cid, Verdict("needs_human", "The judge gave no verdict for this change."))
        for i in idx:
            verdicts[i] = verdict
    summary = str(raw.get("summary", "")) if isinstance(raw, dict) else ""
    return {"verdicts": verdicts, "summary": summary[:500], "model": model, "usd": cost_usd(model, tin, tout),
            "groups": len(groups)}


# --- regression tests -----------------------------------------------------------

TEST_HEADER = '''"""Regression tests written by Doppel from behaviors that changed in a pull request.

Each test replays the synthetic user that saw the change and expects what the base code answered.
Run against a live app:  DOPPEL_BASE_URL=http://127.0.0.1:8000 pytest {name}
(the app must start from the same seeded data Doppel used).
"""
import os

import pytest

from doppel.runner import extract, replay

BASE_URL = os.environ.get("DOPPEL_BASE_URL")
pytestmark = pytest.mark.skipif(not BASE_URL, reason="set DOPPEL_BASE_URL to the running app")
'''


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in text.lower()).strip("_")[:50] or "case"


def regression_tests(findings: list[Finding], verdicts: list[Verdict], personas: list[dict],
                     name: str = "test_doppel_regressions.py") -> str:
    """A pytest file with one test per regression: replay the persona up to the changed step and
    assert the base behavior (status, or the values at the changed JSON paths)."""
    by_name = {p["name"]: p for p in personas}
    out, seen = [TEST_HEADER.format(name=name)], set()
    for f, v in zip(findings, verdicts, strict=True):
        if v.label != "regression" or f.persona not in by_name or f.kind == "slow":
            continue
        fn = f"test_{_slug(f.persona)}_step{f.step}_{_slug(f.request)}"
        if fn in seen:
            continue
        seen.add(fn)
        persona = dict(by_name[f.persona])
        persona["steps"] = persona["steps"][: f.step + 1]
        if f.kind in ("status", "error"):
            check = f"    assert step['status'] == {f.base!r}, step"
        elif isinstance(f.base, dict):
            check = "\n".join(f"    assert extract(step['body'], {path!r}) == {value!r}, step['body']"
                              for path, value in f.base.items() if value != "(absent)")
            check = check or f"    assert step['body'] == {f.base!r}"
        else:
            check = f"    assert step['body'] == {f.base!r}"
        out.append(f'''

def {fn}():
    """{v.why.replace('"', "'")}"""
    persona = {persona!r}
    step = replay(BASE_URL, [persona])[0]["steps"][{f.step}]
{check}
''')
    return "".join(out)
