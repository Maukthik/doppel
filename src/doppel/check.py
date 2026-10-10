"""doppel check: one command, one verdict, from both of Doppel's modes.

    test mode   always: the app's own test suite in base, base again and head; every response compared
                (doppel.traffic). Needs nothing set up.
    the twin    when the repo has a doppel.toml: Nemotron's invented users, guided by an AI reviewer's
                suspicions, replayed in both worlds (`doppel run --guided`, unchanged). Explores what no test
                calls.

Both run at the same time, both are judged against the same PR description, and their findings become one
report: regressions first (with where each came from and the old and new value), then what needs a human,
then the intended changes folded away, then the reviewer's suspicions as the twin checked them. A mode
that can't run says why in the report; it never becomes a verdict.

Exit codes: 0 no regressions, 1 regressions found (with --strict), 2 neither mode could compare.
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import threading
import time
from pathlib import Path


def tests_mode(a, pr_text: str, log) -> dict:
    """Test mode, judged. {"ran": bool, "rows": [...], "reason"?, "result"?}."""
    from doppel import traffic
    from doppel.sandbox import InfraError
    try:
        result = traffic.check(a.base, a.head, backend=a.backend, test=a.command, install=a.install,
                               python=a.python, log=lambda m: log(f"[tests] {m.removeprefix('[tests] ')}"))
    except (traffic.NotComparable, InfraError) as e:
        return {"ran": False, "reason": str(e)}
    findings, verdicts = result["findings"], None
    if findings and pr_text:
        from doppel.judge import judge
        from doppel.personas import code_diff
        verdicts = judge(findings, pr_text, code_diff(a.base, a.head))["verdicts"]
    rows = []
    for k, f in enumerate(findings):
        v = verdicts[k] if verdicts else None
        rows.append({"source": "your tests", "who": _short_test(f.persona), "request": f.request,
                     "detail": f.detail, "base": f.base, "head": f.head, "kind": f.kind,
                     "label": v.label if v else "unjudged", "why": v.why if v else ""})
    return {"ran": True, "rows": rows, "result": result}


def twin_mode(a, pr_text_file: str | None, log) -> dict:
    """The guided twin, exactly as `doppel run` does it. {"ran": bool, "rows", "suspicions", "reason"?}."""
    base, head = Path(a.base), Path(a.head)
    if not (base / "doppel.toml").exists() and not (head / "doppel.toml").exists():
        return {"ran": False, "skipped": True,
                "reason": "not set up (no doppel.toml): `doppel init` writes one, so invented users can explore "
                          "what no test calls"}
    from doppel import cli
    with tempfile.TemporaryDirectory(prefix="doppel-check-") as tmp:
        personas = a.personas or str(base / "doppel-personas.json")
        source = f"from {Path(personas).name}"
        if not Path(personas).exists():   # from the base branch only: a PR must not pick the users that test it
            try:
                from doppel.personas import generate
                log("[twin] Nemotron is writing users for this PR")
                made = generate(base, n=a.n, head_dir=head)
            except Exception as e:  # noqa: BLE001 - the twin is one of two modes; its failure is reported
                return {"ran": False, "reason": f"could not write users: {type(e).__name__}: {str(e)[:300]}"}
            personas, source = str(Path(tmp) / "personas.json"), "written by Nemotron for this PR"
            Path(personas).write_text(json.dumps(made["personas"], indent=1), encoding="utf-8")
        out = Path(tmp) / "twin.json"
        args = ["run", "--backend", a.backend, "--base", str(base), "--head", str(head), "--personas", personas,
                "--rehearse", "--json", str(out)]
        if pr_text_file:
            args += ["--pr", pr_text_file] + ([] if getattr(a, "no_guided", False) else ["--guided"])
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):   # its own Markdown report: the combined one replaces it
            code = cli.main(args)
        if code == 2 or not out.exists():
            return {"ran": False, "reason": "the twin could not run (see the log above)"}
        data = json.loads(out.read_text("utf-8"))
        if getattr(a, "twin_json", None):   # the raw twin recording, for `doppel view` (the visual replay)
            Path(a.twin_json).write_text(out.read_text("utf-8"), encoding="utf-8")
    verdicts = data.get("verdicts") or [None] * len(data.get("findings", []))
    rows = []
    for f, v in zip(data.get("findings", []), verdicts, strict=True):
        rows.append({"source": "invented users", "who": f.get("persona", ""), "request": f.get("request", ""),
                     "detail": f.get("detail", ""), "base": f.get("base"), "head": f.get("head"),
                     "kind": f.get("kind", ""), "label": (v or {}).get("label", "unjudged"),
                     "why": (v or {}).get("why", "")})
    return {"ran": True, "rows": rows, "suspicions": data.get("suspicions"), "coverage": data.get("coverage"),
            "personas": len(data.get("personas") or []), "source": source}


def _short_test(test_id: str) -> str:
    parts = test_id.split("::")
    return f"{parts[0].rsplit('/', 1)[-1]}::{parts[-1]}" if len(parts) > 1 else test_id


def _cell(value, n: int = 70) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str, ensure_ascii=False)
    text = text if len(text) <= n else text[: n - 1] + "…"
    return text.replace("|", "\\|").replace("`", "'").replace("\n", " ")


def _table(rows: list[dict], why: bool = True) -> list[str]:
    head = "| Found by | Where | Request | What changed | Base | Head |" + (" Why |" if why else "")
    out = [head, "|---|---|---|---|---|---|" + ("---|" if why else "")]
    for r in rows:
        cells = [r["source"], _cell(r["who"], 50), f"`{_cell(r['request'], 50)}`", _cell(r["detail"], 80),
                 f"`{_cell(r['base'], 40)}`", f"`{_cell(r['head'], 40)}`"]
        if why:
            cells.append(_cell(r["why"], 200))
        out.append("| " + " | ".join(cells) + " |")
    return out


def report(tests: dict, twin: dict) -> str:
    from doppel.report import suspicions_section
    rows = tests.get("rows", []) + twin.get("rows", [])
    regressions = [r for r in rows if r["label"] in ("regression", "unjudged")]
    human = [r for r in rows if r["label"] == "needs_human"]
    intended = [r for r in rows if r["label"] == "intended"]
    if not tests.get("ran") and not twin.get("ran"):
        title = "## Doppel: couldn't check this PR"
    elif regressions:
        unjudged = all(r["label"] == "unjudged" for r in regressions)
        title = (f"## Doppel: {len(regressions)} behavior change(s)" if unjudged else
                 f"## Doppel: {len(regressions)} regression(s) the PR doesn't mention")
    else:
        title = "## Doppel: no regressions"
    lines = [title, "", "Two independent checks ran on this PR, each comparing the code before and after it:", ""]
    if tests.get("ran"):
        st = tests["result"]["stats"]
        lines.append(f"- **Your tests** (test mode, no setup): {st['tests']} tests sent {st['requests']} requests "
                     f"through the app on base and on head.")
    else:
        reason = tests["reason"].splitlines()[0].split(" Use the HTTP twin")[0]   # the twin is right below
        lines.append(f"- **Your tests** (test mode): couldn't compare: {reason[:300]}")
    if twin.get("ran"):
        cov = twin.get("coverage") or {}
        served = f", {cov['served']}/{cov['requests']} requests served" if cov.get("requests") else ""
        lines.append(f"- **Invented users** (the twin): {twin.get('personas', 0)} users ({twin.get('source', '')}), "
                     f"replayed in both worlds{served}.")
    else:
        lines.append(f"- **Invented users** (the twin): {twin['reason'].splitlines()[0][:300]}")
    if regressions:
        lines += ["", "### 🚨 Regressions" if not all(r["label"] == "unjudged" for r in regressions)
                  else "### Behavior changes (not judged: no PR description given)", ""]
        lines += _table(regressions, why=any(r["why"] for r in regressions))
    if human:
        lines += ["", "### ❓ Needs a human", ""] + _table(human)
    if intended:
        lines += ["", f"<details><summary>✅ Intended: matches the PR description ({len(intended)})</summary>", ""]
        lines += _table(intended) + ["", "</details>"]
    extra = suspicions_section(twin.get("suspicions")) if twin.get("ran") else ""
    if extra:
        lines += ["", extra.strip()]
    if tests.get("ran"):
        from doppel.traffic import report as tests_report
        foot = tests_report(tests["result"]).strip().splitlines()[-1]
        if foot.startswith("<sub>"):
            lines += ["", foot]
    if not tests.get("ran") and not twin.get("ran"):
        lines += ["", "> " + tests["reason"].replace("\n", "\n> ")[:2500]]
    return "\n".join(lines) + "\n"


def cmd_check(a) -> int:
    t0 = time.perf_counter()
    lock = threading.Lock()

    def log(message: str) -> None:
        with lock:
            print(message, file=sys.stderr)
    pr_text = Path(a.pr).read_text(encoding="utf-8") if a.pr else ""
    results: dict = {}

    def run(name, fn, *args):
        try:
            results[name] = fn(*args)
        except Exception as e:  # noqa: BLE001 - one mode's crash must not take the other down
            results[name] = {"ran": False, "reason": f"{type(e).__name__}: {str(e)[:400]}"}
    threads = [threading.Thread(target=run, args=("tests", tests_mode, a, pr_text, log))]
    if not a.no_twin:
        threads.append(threading.Thread(target=run, args=("twin", twin_mode, a, a.pr, log)))
    else:
        results["twin"] = {"ran": False, "skipped": True, "reason": "turned off (--no-twin)"}
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    tests, twin = results["tests"], results["twin"]
    md = report(tests, twin)
    print(md)
    log(f"({time.perf_counter() - t0:.1f} s)")
    if a.out:
        Path(a.out).write_text(md, encoding="utf-8")
    rows = tests.get("rows", []) + twin.get("rows", [])
    regressions = [r for r in rows if r["label"] in ("regression", "unjudged")]
    if a.json:
        Path(a.json).write_text(json.dumps({
            "mode": "check", "regressions": len(regressions), "rows": rows,
            "tests": {k: v for k, v in tests.items() if k != "result"} | (
                {"stats": tests["result"]["stats"], "noise_keys": tests["result"]["noise_keys"]}
                if tests.get("ran") else {}),
            "twin": {k: v for k, v in twin.items() if k != "rows"}}, indent=1, default=str), encoding="utf-8")
    if not tests.get("ran") and not twin.get("ran"):
        return 2
    return 1 if a.strict and regressions else 0
