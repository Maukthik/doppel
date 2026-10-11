"""Does Doppel work on real apps it has never seen, with zero setup? Ten popular open-source Python web apps.

Protocol (fixed before any run, no fixes during it):
  - each app at its latest commit, unmodified: no doppel.toml, no hand-written command
  - `doppel check` runs the base against itself (A/A: the same code as base and head) with test mode only:
    every difference it reports is a false alarm, so a clean result proves the comparison is sound
  - everything comes from what Doppel detects: test command, install command, Python version
  - each result is recorded as it comes out:
        works     the app's own tests ran in the sandbox, requests were recorded, 0 false differences
        noisy     it ran, but A/A showed differences (false alarms)
        stopped   a safeguard stopped it, with the reason (install, services, no recorded client, ...)

    python bench/fresh_apps.py run --backend nebius          # the official numbers (sponsor stack)
    python bench/fresh_apps.py run flaskbb --backend local   # a local preview (your current tools, a fresh venv)
    python bench/fresh_apps.py page                          # bench/results/fresh/index.html
    python bench/fresh_apps.py run --set heldout --backend nebius   # the held-out apps (see HELDOUT below)

Two sets:
  dev       the first ten apps. Screening them on 11 Oct 2026 found the gaps the setup layer was rebuilt for, so
            their later numbers are development numbers (before and after), not an unseen-app claim.
  heldout   ten more apps, chosen and pinned to a commit on 11 Oct 2026 BEFORE any setup-layer change, by the rule
            written next to the list. They are run once after the rebuild, with no fixes for them: that is the claim.

Each app gets bench/results/fresh/<app>/check.json and doppel-check.html (the same page `doppel view` makes).
Finished apps are skipped; --force redoes them.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
CACHE = HERE / ".cache" / "fresh"
RESULTS = HERE / "results" / "fresh"

APPS = [
    {"name": "ctfd", "repo": "https://github.com/CTFd/CTFd", "framework": "Flask"},
    {"name": "redash", "repo": "https://github.com/getredash/redash", "framework": "Flask"},
    {"name": "flaskbb", "repo": "https://github.com/flaskbb/flaskbb", "framework": "Flask"},
    {"name": "healthchecks", "repo": "https://github.com/healthchecks/healthchecks", "framework": "Django"},
    {"name": "wger", "repo": "https://github.com/wger-project/wger", "framework": "Django"},
    {"name": "paperless-ngx", "repo": "https://github.com/paperless-ngx/paperless-ngx", "framework": "Django"},
    {"name": "label-studio", "repo": "https://github.com/HumanSignal/label-studio", "framework": "Django"},
    {"name": "bakerydemo", "repo": "https://github.com/wagtail/bakerydemo", "framework": "Django (Wagtail)"},
    {"name": "mealie", "repo": "https://github.com/mealie-recipes/mealie", "framework": "FastAPI"},
    {"name": "fastapi-template", "repo": "https://github.com/fastapi/full-stack-fastapi-template",
     "framework": "FastAPI", "subdir": "backend"},
]
HELDOUT_RULE = """Chosen on 11 Oct 2026, before any change to the setup layer, and not inspected for how they set up:
  - an open-source Python web application (Flask, Django or FastAPI), not a library or framework
  - a commit within the last 12 months and a Python test suite in the repo
  - not one of the ten dev apps
  - run from the repo root with no hand-given subfolder, pinned to the commit below so the run cannot drift
Rejected by the rule while choosing: fastapi-realworld-example-app (last commit 2022), Netflix/dispatch (last commit
Sep 2025), open-webui (no Python test suite)."""
HELDOUT = [
    {"name": "ihatemoney", "repo": "https://github.com/spiral-project/ihatemoney", "framework": "Flask",
     "sha": "e66a7672e8e5c41549bf53c4a824c72c43ab9079"},
    {"name": "searxng", "repo": "https://github.com/searxng/searxng", "framework": "Flask",
     "sha": "f4822b3fc46726bb259d14c6332b702c76b98f82"},
    {"name": "changedetection", "repo": "https://github.com/dgtlmoon/changedetection.io", "framework": "Flask",
     "sha": "75a508fecc37483e936795f1e3f9b076e375cf0f"},
    {"name": "babybuddy", "repo": "https://github.com/babybuddy/babybuddy", "framework": "Django",
     "sha": "2f80de4ae05e5f751d6fb29c36b25b4cda1755d5"},
    {"name": "pretalx", "repo": "https://github.com/pretalx/pretalx", "framework": "Django",
     "sha": "dc09fdfc29c4155edcf376199dd19654c53c2ad6"},
    {"name": "tandoor", "repo": "https://github.com/TandoorRecipes/recipes", "framework": "Django",
     "sha": "bb07441b3cd566dbc6c29ab09dc74b741804ef02"},
    {"name": "inventree", "repo": "https://github.com/inventree/InvenTree", "framework": "Django",
     "sha": "fcd8af4283f3b109f8253f418997fd7299cb521a"},
    {"name": "lnbits", "repo": "https://github.com/lnbits/lnbits", "framework": "FastAPI",
     "sha": "b717d51d7b6c099cb5cb4d730e59c6aa720e5365"},
    {"name": "polar", "repo": "https://github.com/polarsource/polar", "framework": "FastAPI",
     "sha": "6d5e2fca0ee8519936afb5d7a0da801633339a70"},
    {"name": "fastapi-best-architecture", "repo": "https://github.com/fastapi-practices/fastapi_best_architecture",
     "framework": "FastAPI", "sha": "e90b6f266cc545136a59cd3faf60610958dda44f"},
]
HELDOUT_RESULTS = HERE / "results" / "heldout"
SET_TITLES = {"dev": "Ten real Python web apps, never seen before",
              "heldout": "Ten held-out apps, chosen before the setup layer was rebuilt"}

# What a stopped run's reason says, in words a reader can act on
CATEGORIES = [
    ("install", ("install command failed", "No matching distribution", "Failed building wheel")),
    ("missing dependency", ("ModuleNotFoundError", "No module named")),   # before services: "No module named 'redis'"
    ("needs a service", ("fail on the unchanged code", "Connection refused", "could not connect", "OperationalError",
                         "redis", "postgres", "psycopg")),
    ("no recorded requests", ("sent no request through a test client",)),
    ("no test suite found", ("no test suite found", "no test suite in this folder")),
    ("tests did not run", ("ran no test",)),
    ("sandbox service", ("infrastructure error",)),
]


def clone(app: dict) -> tuple[Path, str]:
    """The app's code: its latest commit, or exactly app["sha"] when the set pins one."""
    src = CACHE / app["name"]
    pinned = app.get("sha")
    if pinned:
        here = subprocess.run(["git", "rev-parse", "HEAD"], cwd=src, capture_output=True, text=True).stdout.strip() \
            if (src / ".git").exists() else ""
        if here != pinned:
            src.mkdir(parents=True, exist_ok=True)
            git = lambda *a: subprocess.run(["git", *a], cwd=src, check=True, capture_output=True)  # noqa: E731
            if not (src / ".git").exists():
                git("init", "-q")
                git("remote", "add", "origin", app["repo"])
            git("fetch", "-q", "--depth", "1", "origin", pinned)
            git("checkout", "-q", "--force", "FETCH_HEAD")
    elif not (src / ".git").exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "-q", "--depth", "1", app["repo"], str(src)], check=True)
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=src, capture_output=True, text=True).stdout.strip()
    return src / app.get("subdir", ""), sha


def local_python(app: dict, code: Path) -> tuple[str, str]:
    """A fresh venv on the Python the app declares, with what Doppel detects installed into it. Returns the
    interpreter, or "" and the install error."""
    from doppel.probe import IMAGE, image_for
    from doppel.traffic import detect
    version = image_for(code).split(":")[1].split("-")[0] if image_for(code) != IMAGE else "3.12"
    venv = CACHE / "venvs" / app["name"]
    py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not py.exists():
        subprocess.run(["uv", "venv", "-q", "--seed", "-p", version, str(venv)], check=True)
    install = detect(code)["install"]
    if install:
        env = {**os.environ, "VIRTUAL_ENV": str(venv),
               "PATH": str(py.parent) + os.pathsep + os.environ.get("PATH", "")}
        r = subprocess.run(install, shell=True, cwd=code, env=env, capture_output=True, text=True, timeout=1800)
        if r.returncode:
            return "", "the install command failed:\n" + (r.stdout + r.stderr)[-2500:]
    return str(py), ""


def category(reason: str) -> str:
    low = reason.lower()
    return next((name for name, signs in CATEGORIES if any(s.lower() in low for s in signs)), "other")


def results_for(set_name: str) -> Path:
    return RESULTS if set_name == "dev" else HELDOUT_RESULTS   # looked up at call time (tests patch RESULTS)


def apps_for(set_name: str) -> list[dict]:
    return APPS if set_name == "dev" else HELDOUT


def run_one(app: dict, backend: str, force: bool, results: Path | None = None) -> dict:
    out_dir = (results or RESULTS) / app["name"]
    if (out_dir / "result.json").exists() and not force:
        return json.loads((out_dir / "result.json").read_text("utf-8"))
    from doppel import cli
    from doppel.checkview import write_check_view
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    result = {**app, "backend": backend, "date": str(date.today())}
    try:
        code, sha = clone(app)
        result["commit"] = sha
        args = ["check", "--base", str(code), "--head", str(code), "--backend", backend, "--no-twin",
                "--app", app["name"], "--json", str(out_dir / "check.json"), "--out", str(out_dir / "report.md")]
        if backend == "local":
            python, error = local_python(app, code)
            if error:
                raise RuntimeError(error)
            args += ["--python", python]
        cli.main(args)
        data = json.loads((out_dir / "check.json").read_text("utf-8"))
        tests = data.get("tests") or {}
        if tests.get("ran"):
            st = tests.get("stats") or {}
            result.update(status="works" if not data.get("rows") else "noisy", tests=st.get("tests"),
                          requests=st.get("recorded_requests"), false_differences=len(data.get("rows") or []),
                          noise_keys=tests.get("noise_keys"), test_command=tests.get("test"),
                          source=tests.get("source"))
        else:
            reason = tests.get("reason", "")
            result.update(status="stopped", category=category(reason), reason=reason[:3000])
        write_check_view(out_dir / "check.json", out_dir / "doppel-check.html", public=True)
    except Exception as e:  # noqa: BLE001 - one app's failure must not stop the others
        reason = f"{type(e).__name__}: {e}"
        result.update(status="stopped", category=category(reason), reason=reason[:3000])
    result["seconds"] = round(time.perf_counter() - t0, 1)
    (out_dir / "result.json").write_text(json.dumps(result, indent=1, default=str) + "\n", encoding="utf-8")
    return result


def page(set_name: str = "dev") -> Path:
    from doppel.viewer import CSS
    RESULTS = results_for(set_name)  # noqa: N806 - the set's folder; the rest of the page reads it by this name
    rows = [json.loads((RESULTS / a["name"] / "result.json").read_text("utf-8")) for a in apps_for(set_name)
            if (RESULTS / a["name"] / "result.json").exists()]
    counts = {k: sum(r["status"] == k for r in rows) for k in ("works", "noisy", "stopped")}
    cards = []
    for r in rows:
        link = f"{r['name']}/doppel-check.html" if (RESULTS / r["name"] / "doppel-check.html").exists() else ""
        if r["status"] == "works":
            facts = (f"<b>{r.get('tests', 0)}</b> tests, <b>{r.get('requests', 0)}</b> requests recorded, "
                     f"<b>0</b> false differences")
            sub = f"<code>{html.escape(r.get('test_command') or '')}</code> (from {html.escape(r.get('source') or '')})"
        elif r["status"] == "noisy":
            facts = f"{r.get('tests', 0)} tests, <b>{r.get('false_differences')}</b> false differences"
            sub = "differences between two runs of the same code: see the page"
        else:
            facts = f"Stopped: <b>{html.escape(r.get('category', 'other'))}</b>"
            sub = html.escape((r.get("reason") or "").splitlines()[0][:220]) if r.get("reason") else ""
        cards.append(f"""<article class="app {r['status']}"><header><h3>{html.escape(r['name'])}</h3>
<span class="pill {r['status']}">{r['status']}</span></header>
<p class="fw">{html.escape(r['framework'])} · commit {html.escape(r.get('commit', '?'))} · {r.get('seconds', '?')} s</p>
<p>{facts}</p><p class="sub">{sub}</p>{f'<a href="{link}">Open the check →</a>' if link else ''}</article>""")
    body = f"""<p class="brand">doppel · zero-setup experiment</p>
<h1>{SET_TITLES[set_name]}</h1>
<p class="lede">Each app at {'its latest commit' if set_name == 'dev' else 'the commit pinned when it was chosen'}, unmodified, with no configuration. Doppel detected the test command,
the install and the Python version on its own, ran the app's own test suite in base, base again and head (the same
code, so every difference would be a false alarm), and recorded every request. Results are as they came out: no
fixes during the run.</p>
<div class="tally"><span class="works">{counts['works']} work</span><span class="noisy">{counts['noisy']} noisy</span>
<span class="stopped">{counts['stopped']} stopped, each with its reason</span></div>
<div class="apps">{''.join(cards)}</div>"""
    css = CSS + """
.brand{color:var(--quiet);font-size:.85rem;letter-spacing:.04em;text-transform:uppercase;margin:0 0 8px}
.tally{display:flex;gap:10px;flex-wrap:wrap;margin:0 0 22px}.tally span{padding:6px 14px;border-radius:999px;font-weight:600;font-size:.92rem}
.tally .works{background:#E2F1ED;color:var(--pass)}.tally .noisy{background:#F8EED9;color:#8A5A00}.tally .stopped{background:#ECEFF3;color:var(--quiet)}
.apps{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px}
.app{background:var(--paper);border:1px solid var(--line);border-top:5px solid var(--rail);border-radius:14px;padding:14px 16px}
.app.works{border-top-color:var(--pass)}.app.noisy{border-top-color:#C08A1E}
.app header{display:flex;justify-content:space-between;align-items:center;gap:10px}.app h3{margin:0;font-size:1.05rem}
.app p{margin:6px 0 0;font-size:.92rem}.app .fw,.app .sub{color:var(--quiet);font-size:.85rem;overflow-wrap:anywhere}
.app a{display:inline-block;margin-top:10px;color:var(--free);font-weight:500}
.pill{font-size:.78rem;font-weight:600;padding:3px 10px;border-radius:999px;background:var(--fog)}
.pill.works{background:#E2F1ED;color:var(--pass)}.pill.noisy{background:#F8EED9;color:#8A5A00}
"""
    out = RESULTS / "index.html"
    RESULTS.mkdir(parents=True, exist_ok=True)
    out.write_text(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Doppel: {SET_TITLES[set_name]}</title>
<style>{css}</style></head><body><main>{body}</main></body></html>""", encoding="utf-8")
    lines = [f"# {SET_TITLES[set_name]}", "", f"{counts['works']} work · {counts['noisy']} noisy · "
             f"{counts['stopped']} stopped", "", "| App | Framework | Result | Tests | Requests | Reason |",
             "|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['name']} | {r['framework']} | {r['status']} | {r.get('tests', '')} | "
                     f"{r.get('requests', '')} | {r.get('category', '')} |")
    (RESULTS / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("step", choices=["run", "page"])
    ap.add_argument("apps", nargs="*", help="app names (default: every app in the set)")
    ap.add_argument("--set", dest="set_name", choices=["dev", "heldout"], default="dev")
    ap.add_argument("--backend", choices=["local", "nebius"], default="nebius")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    if a.step == "run":
        if a.backend == "nebius":
            from dotenv import load_dotenv
            load_dotenv()
        apps = apps_for(a.set_name)
        unknown = sorted(set(a.apps) - {x["name"] for x in apps})
        if unknown:   # a mistyped name used to be skipped silently, which looked like a finished run
            print(f"unknown app name(s) in the {a.set_name} set: {', '.join(unknown)}. "
                  f"Known: {', '.join(x['name'] for x in apps)}", file=sys.stderr)
            return 2
        chosen = [x for x in apps if not a.apps or x["name"] in a.apps]
        for app in chosen:
            print(f"[fresh] {app['name']} ({app['framework']})", file=sys.stderr)
            r = run_one(app, a.backend, a.force, results_for(a.set_name))
            extra = (f"{r.get('tests')} tests, {r.get('requests')} requests, {r.get('false_differences')} false "
                     "differences") if r["status"] != "stopped" else f"{r.get('category')}: " + \
                (r.get("reason") or "").splitlines()[0][:140]
            print(f"[fresh] {app['name']} -> {r['status']}: {extra}")
    out = page(a.set_name)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
