"""Ask the app instead of guessing: run doppel/probe_app.py where the app's dependencies are installed and
get back what is actually true (routes, database location, login, models, commands, import errors).

    facts = probe(repo, backend="nebius", install="pip install -r requirements.txt")
    text = facts_text(facts)        # for the model: ground truth, ranked above its reading of the code

`doppel probe <app>` prints the facts, which is also the quickest way to see why an app won't import.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROBE = Path(__file__).with_name("probe_app.py")
IMAGE = "python:3.12-slim"
MAX_ROUTES_SHOWN = 80


def _parse(stdout: str, stderr: str = "", install_log: str = "") -> dict:
    lines = [ln for ln in (stdout or "").strip().splitlines() if ln.startswith("{")]
    try:
        facts = json.loads(lines[-1]) if lines else {}
    except ValueError:
        facts = {}
    if not facts:
        facts = {"framework": "unknown", "errors": [{"where": "run the probe", "error": "the probe printed no facts",
                                                     "traceback": ((stdout or "") + (stderr or ""))[-2500:]}]}
    if install_log.strip():
        facts["install_log"] = install_log.strip()[-1500:]
    return facts


def probe_local(repo: str | Path, env: dict | None = None, timeout: int = 120) -> dict:
    """In the current Python environment, on a throwaway copy (importing an app can create files)."""
    import os
    with tempfile.TemporaryDirectory(prefix="doppel-probe-") as tmp:
        work = Path(tmp) / "app"
        shutil.copytree(repo, work, ignore=shutil.ignore_patterns(".git", "__pycache__", ".venv", "venv",
                                                                   "node_modules", ".doppel-trace"))
        p = subprocess.run([sys.executable, str(PROBE)], cwd=work, capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, **(env or {})})
        return _parse(p.stdout, p.stderr)


async def _probe_nebius(client, repo: Path, install: str, image: str, exports: str) -> dict:
    from doppel.backends.nebius import APP, code_files
    python = image.split(":")[1].split("-")[0] if image != IMAGE and ":" in image else ""
    if python:   # never ask the platform for a python:X.Y image it may not have: install that Python with uv
        venv = "/opt/doppel-python"
        exports = (f"export VIRTUAL_ENV={venv}; export PATH={venv}/bin:$PATH; ") + exports
        install = (f"pip install --quiet uv && uv python install {python} && uv venv --quiet --seed -p {python} "
                   f"{venv} && {exports}" + (install or "true"))
    img = await client.images.use(IMAGE)
    img = await img.apply_files(code_files(repo))
    run_probe = f"{exports}python /doppel/probe_app.py"
    shell = (f"cd {APP} && ({exports}{install}) > /tmp/install.log 2>&1; rc=$?; "
             f"if [ $rc -ne 0 ]; then echo '@@install-failed@@' >&2; tail -n 30 /tmp/install.log >&2; fi; "
             f"{run_probe}") if install else f"cd {APP} && {run_probe}"
    from doppel.backends.nebius import OUTPUT_LIMIT
    out = await img.run(shell=shell, files={"doppel/probe_app.py": PROBE.read_bytes()}, disposable=True,
                        timeout=1200, truncate_output_at=OUTPUT_LIMIT)
    stderr = out.stderr or ""
    log = stderr.split("@@install-failed@@", 1)[1] if "@@install-failed@@" in stderr else ""
    return _parse(out.stdout or "", stderr, log)


def probe_nebius(repo: str | Path, install: str = "", image: str = IMAGE, env: dict | None = None,
                 client=None) -> dict:
    """In a fresh Nebius sandbox: upload the app, install its dependencies, run the probe."""
    import shlex

    from doppel.backends.nebius import make_client
    exports = "".join(f"export {k}={shlex.quote(str(v))}; " for k, v in (env or {}).items())
    return asyncio.run(_probe_nebius(client or make_client(), Path(repo).resolve(), install, image, exports))


KNOWN_PYTHONS = ("3.9", "3.10", "3.11", "3.12", "3.13", "3.14")


def image_for(repo: str | Path) -> str:
    """python:<version>-slim for the lowest Python the project allows (requires-python >=3.14 -> 3.14),
    the default image when it says nothing or allows 3.12."""
    import re
    f = Path(repo) / "pyproject.toml"
    m = re.search(r"""requires-python\s*=\s*['"]\s*>=?\s*(3\.\d+)""", f.read_text("utf-8", "replace")) \
        if f.exists() else None
    if m and m.group(1) in KNOWN_PYTHONS and KNOWN_PYTHONS.index(m.group(1)) > KNOWN_PYTHONS.index("3.12"):
        return f"python:{m.group(1)}-slim"
    return IMAGE


def probe(repo: str | Path, backend: str = "nebius", install: str = "", image: str | None = None,
          env: dict | None = None, client=None) -> dict:
    if backend == "local":
        return probe_local(repo, env)
    return probe_nebius(repo, install, image or image_for(repo), env, client)


def facts_text(facts: dict) -> str:
    """The facts as a compact brief for the setup model. Empty when the probe learned nothing useful."""
    if not facts or (facts.get("framework") in (None, "unknown") and not facts.get("errors")):
        return ""
    out = ["(paths are relative to the repo root: the folder every command in doppel.toml runs in)",
           f"Framework: {facts.get('framework')} {facts.get('version', '')}".rstrip()
           + (f", app {facts['app']}" if facts.get("app") else "")
           + f" (probed on Python {facts.get('python', '?')})"]
    if facts.get("python_mismatch"):
        out.append(f"PYTHON VERSION: {facts['python_mismatch']}")
    if facts.get("run"):
        out.append(f"Start command that loads this app: {facts['run']}")
    if facts.get("commands"):
        out.append("The app's own commands: " + "; ".join(facts["commands"]))
    db = facts.get("database") or {}
    if db:
        out.append("Database, as the running app configures it: " + json.dumps(db, default=str))
    if facts.get("instance_path"):
        out.append(f"Flask instance folder (relative to the repo root): {facts['instance_path']}")
    if facts.get("schema_files"):
        out.append("Schema files: " + ", ".join(facts["schema_files"]))
    auth = facts.get("auth") or {}
    if auth:
        out.append("Login and auth: " + json.dumps(auth, default=str))
    routes = facts.get("routes") or []
    if routes:
        out.append(f"Routes ({len(routes)}; reads = what the view reads from the request):")
        for r in routes[:MAX_ROUTES_SHOWN]:
            tags = [f"reads {'+'.join(r['reads'])}"] if r.get("reads") else []
            if r.get("needs_login"):
                tags.append("login needed" + (" for writes" if r["needs_login"] == "writes" else ""))
            if r.get("looks_like_login"):
                tags.append("THIS IS A LOGIN VIEW")
            out.append(f"  {' '.join(r.get('methods') or ['ANY'])} {r['path']}"
                       + (f"  [{', '.join(tags)}]" if tags else "") + (f"  ({r['view']})" if r.get("view") else ""))
        if len(routes) > MAX_ROUTES_SHOWN:
            out.append(f"  ... and {len(routes) - MAX_ROUTES_SHOWN} more")
    models = facts.get("models") or []
    if models:
        out.append("Models (required = must be set when creating one; fk = must point at an existing row):")
        for m in models[:40]:
            req = [f["name"] + (f"->{f['fk']}" if f.get("fk") else "")
                   for f in m.get("fields", []) if f.get("required")]
            uniq = [f["name"] for f in m.get("fields", []) if f.get("unique")]
            out.append(f"  {m['class']} (table {m.get('table')}): required {', '.join(req) or 'none'}"
                       + (f"; unique {', '.join(uniq)}" if uniq else ""))
    for e in (facts.get("errors") or [])[:3]:
        out.append(f"Could not {e['where']}: {e['error']}\n{e.get('traceback', '')[-1200:]}")
    if facts.get("install_log"):
        out.append("The install command failed:\n" + facts["install_log"])
    return relative_paths("\n".join(out))


def relative_paths(text: str) -> str:
    """<repo>/db.sqlite3 -> db.sqlite3. The probe marks the repo root as <repo>; a model copies what it is
    shown into shell commands, where `<repo` means "read from a file named repo". Relative paths are what
    the commands need anyway: they all run in the repo root."""
    text = text.replace("<repo>/", "")
    return re.sub(r"<repo>(?=[\s\"',)}\]]|$)", ".", text)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="print what Doppel's probe learns from a running app")
    ap.add_argument("app", nargs="?", default=".")
    ap.add_argument("--backend", choices=["local", "nebius"], default="nebius")
    ap.add_argument("--install", default=None, help="default: derived from the app's requirements")
    ap.add_argument("--json", action="store_true", help="print the raw facts")
    a = ap.parse_args(argv)
    install = a.install
    if install is None and a.backend == "nebius":
        from doppel.bootstrap import derived_install
        install = derived_install(a.app)
    facts = probe(a.app, a.backend, install or "")
    print(json.dumps(facts, indent=1, default=str) if a.json else facts_text(facts))
    return 0 if facts.get("framework") not in (None, "unknown") and not facts.get("python_mismatch") else 1
