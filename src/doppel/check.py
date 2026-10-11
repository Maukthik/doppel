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


def _pattern(request: str) -> str:
    """GET /orders/17?x=1 -> GET /orders/{id}: requests that differ only in ids are the same route."""
    import re
    method, _, path = request.partition(" ")
    path = path.split("?")[0]
    path = re.sub(r"/(\d+|[0-9a-f]{8,}|[0-9a-f-]{36})(?=/|$)", "/{id}", path)
    return f"{method} {path}".strip()


def _fields(detail: str) -> str:
    """"changed: data.*.author.password_hash (added) x3, timestamp" -> "password_hash (added), timestamp": the
    fields that changed and how, without where in the body or how often (that differs per route)."""
    import re
    detail = re.sub(r"\s*\(the base branch's test fails on this\)", "", detail.removeprefix("changed: "))
    parts = []
    for item in re.split(r",\s*(?![^()]*\))", detail):
        item = re.sub(r"\s+x\d+$", "", item.strip())
        m = re.match(r"(.*?)(\s*\((?:added|removed)\))?$", item)
        leaf = re.sub(r".*\.", "", m.group(1)) or m.group(1)
        parts.append(leaf + (m.group(2) or ""))
    return ", ".join(sorted(set(p for p in parts if p and not p.startswith("+"))))


def _telling(row: dict) -> bool:
    """An example worth showing: head actually has a value (not null, not absent) where the change is."""
    head = row.get("head")
    if isinstance(head, dict):
        return any(v not in (None, "", "(absent)", "(not sent)") for v in head.values())
    return head not in (None, "", "(absent)", "(not sent)")


def group(rows: list[dict]) -> list[dict]:
    """One entry per distinct change. Judged rows: same verdict and the judge's same reasoning (the judge
    already looked at each distinct change once, so equal reasoning is the same change). Unjudged rows: same
    route and the same kind of difference. Each group keeps every row it stands for."""
    import re
    groups: dict[tuple, dict] = {}
    for r in rows:
        if r.get("kind") == "body" and r["detail"].startswith("changed: "):
            # the same field change, wherever it shows up (a user object, a list, a post's author): one change,
            # even when the judge wrote one explanation per place
            key = (r["label"], "body", _fields(r["detail"]))
        elif r.get("why"):
            key = (r["label"], r["why"].strip())
        else:
            key = (r["label"], _pattern(r["request"]), re.sub(r"\s*\(the base branch's test fails on this\)", "",
                                                               r["detail"]))
        g = groups.setdefault(key, {"label": r["label"], "why": r.get("why", ""), "rows": []})
        g["rows"].append(r)
    out = []
    for g in groups.values():
        rows_ = g["rows"]
        routes = list(dict.fromkeys(_pattern(r["request"]) for r in rows_))
        sources = list(dict.fromkeys(r["source"] for r in rows_))
        who = list(dict.fromkeys(r["who"] for r in rows_))
        changes = list(dict.fromkeys(r["detail"] for r in rows_))
        example = next((r for r in rows_ if _telling(r)), rows_[0])
        out.append({**g, "why": g["why"] or example.get("why", ""), "routes": routes, "sources": sources, "who": who,
                    "changes": changes, "example": example, "requests": len(rows_)})
    return sorted(out, key=lambda g: -g["requests"])


def _group_table(groups: list[dict], why: bool) -> list[str]:
    head = "| | Change | Where | Seen by | Example: base → head |" + (" Why |" if why else "")
    out = [head, "|---|---|---|---|---|" + ("---|" if why else "")]
    for k, g in enumerate(groups, 1):
        ex = g["example"]
        from doppel.checkview import headline
        change = _cell(headline(g), 90)      # the same plain words as the check page
        where = "<br>".join(f"`{_cell(r, 45)}`" for r in g["routes"][:3]) + (
            f"<br>+{len(g['routes']) - 3} more" if len(g["routes"]) > 3 else "")
        seen = ", ".join(_seen(g["rows"], src) for src in g["sources"])
        cells = [str(k), change, where, seen, f"`{_cell(ex['base'], 40)}` → `{_cell(ex['head'], 40)}`"]
        if why:
            cells.append(_cell(g["why"], 220))
        out.append("| " + " | ".join(cells) + " |")
    return out


def _n(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _seen(rows: list[dict], source: str) -> str:
    """"7 requests from 6 users" / "1 request from 1 test", for one mode."""
    mine = [r for r in rows if r["source"] == source]
    who = len({r["who"] for r in mine})
    noun = {"your tests": "test", "invented users": "user"}.get(source, source)
    return f"{_n(len(mine), 'request')} from {_n(who, noun)}"


def _all_requests(rows: list[dict]) -> list[str]:
    out = ["| Found by | Who | Request | What changed | Base | Head |", "|---|---|---|---|---|---|"]
    for r in rows:
        out.append("| " + " | ".join([r["source"], _cell(r["who"], 45), f"`{_cell(r['request'], 45)}`",
                                      _cell(r["detail"], 70), f"`{_cell(r['base'], 35)}`",
                                      f"`{_cell(r['head'], 35)}`"]) + " |")
    return out


def _section(title: str, groups: list[dict], why: bool, open_: bool = True) -> list[str]:
    rows = [r for g in groups for r in g["rows"]]
    lines = ["", title, ""] if open_ else ["", f"<details><summary>{title}</summary>", ""]
    lines += _group_table(groups, why)
    lines += ["", f"<details><summary>All {_n(len(rows), 'request')} behind "
                  f"{'this' if len(groups) == 1 else 'these'}</summary>", ""]
    lines += _all_requests(rows) + ["", "</details>"]
    if not open_:
        lines += ["", "</details>"]
    return lines


def report(tests: dict, twin: dict) -> str:
    from doppel.report import suspicions_section
    rows = tests.get("rows", []) + twin.get("rows", [])
    regressions = group([r for r in rows if r["label"] in ("regression", "unjudged")])
    human = group([r for r in rows if r["label"] == "needs_human"])
    intended = group([r for r in rows if r["label"] == "intended"])
    unjudged = bool(regressions) and all(g["label"] == "unjudged" for g in regressions)
    n_requests = sum(g["requests"] for g in regressions)
    if not tests.get("ran") and not twin.get("ran"):
        title = "## Doppel: couldn't check this PR"
    elif regressions and unjudged:
        title = f"## Doppel: {_n(len(regressions), 'behavior change')}, seen in {_n(n_requests, 'request')}"
    elif regressions:
        title = (f"## Doppel: {_n(len(regressions), 'regression')} the PR doesn't mention, "
                 f"seen in {_n(n_requests, 'request')}")
    else:
        title = "## Doppel: no regressions"
    lines = [title, ""]
    summary = []
    if tests.get("ran"):
        st = tests["result"]["stats"]
        summary.append(f"- **Your tests** (test mode, no setup): {st['tests']} tests sent {st['requests']} requests "
                       "through the app, on base and on head.")
    else:
        reason = tests["reason"].splitlines()[0].split(" Use the HTTP twin")[0]   # the twin is right below
        summary.append(f"- **Your tests** (test mode): couldn't compare: {reason[:300]}")
    if twin.get("ran"):
        cov = twin.get("coverage") or {}
        served = f"; the app served {cov['served']} of {cov['requests']} requests" if cov.get("requests") else ""
        summary.append(f"- **Invented users** (the twin): {twin.get('personas', 0)} users "
                       f"({twin.get('source', '')}), replayed in both worlds{served}.")
    else:
        summary.append(f"- **Invented users** (the twin): {twin['reason'].splitlines()[0][:300]}")
    lines += ["Two independent checks compared the code before and after this PR:", "", *summary]
    if regressions:
        title = ("### Behavior changes (not judged: no PR description given)" if unjudged
                 else "### 🚨 Regressions the PR doesn't mention")
        lines += _section(title, regressions, why=not unjudged)
    if human:
        lines += _section("### ❓ Needs a human", human, why=True)
    if intended:
        lines += _section(f"✅ Intended: matches the PR description ({len(intended)})", intended, why=True,
                          open_=False)
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
    lines += ["", "<sub>Each change is proven by real runs of the code before and after the PR; the verdicts are "
                  "Nemotron's, against the PR description.</sub>"] if (regressions or human or intended) and \
        not unjudged else []
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
        title = next((ln.lstrip("# ").strip() for ln in pr_text.splitlines() if ln.strip()), "")
        res = tests.get("result") or {}
        Path(a.json).write_text(json.dumps({
            "mode": "check", "app": getattr(a, "app", None) or Path(a.base).resolve().name,
            "pr": {"title": title, "body": pr_text},
            "regressions": len(group(regressions)), "regression_requests": len(regressions), "rows": rows,
            "tests": {k: v for k, v in tests.items() if k not in ("result", "rows")} | ({
                "stats": res["stats"], "noise_keys": res["noise_keys"], "test": res["test"],
                "source": res["detected"]["source"]["test"], "excluded": res["excluded"], "flaky": res["flaky"],
                "unstable": res["unstable"]} if tests.get("ran") else {}),
            "twin": {k: v for k, v in twin.items() if k != "rows"},
            "seconds": round(time.perf_counter() - t0, 1)}, indent=1, default=str), encoding="utf-8")
    if not tests.get("ran") and not twin.get("ran"):
        return 2
    return 1 if a.strict and regressions else 0
