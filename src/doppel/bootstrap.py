"""doppel init: set up an app Doppel has never seen.

A new teammate on day one: read how the repo says it runs, try it in a safe room, fix what
breaks, add test data, learn which values change by themselves, then hand the notes over.

  1. gather     read the repo's own setup hints (manifests, CI, Dockerfile, README) and its code
  2. draft      Nemotron writes doppel.toml and doppel_seed.py from them
  3. verify     boot the twin with a smoke persona: seed, migrate, start, ready, log in
  4. repair     on failure the error goes back to the model, up to `tries` attempts
  5. calibrate  replay invented users against the SAME code twice; any JSON key that differs
                between two identical runs is noise and joins [diff] ignore
  6. hand over  write the files for a human to review and commit
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import tomllib
from pathlib import Path

from doppel.backends.nebius import SKIP_PARTS
from doppel.diff import changed_paths
from doppel.personas import nemotron_chat, parse_json
from doppel.spec import TwinSpec

SPEC_FILE, SEED_FILE = "doppel.toml", "doppel_seed.py"
ALLOWED_FILES = {SPEC_FILE, SEED_FILE}
HINT_NAMES = ("pyproject.toml", "setup.py", "setup.cfg", "Pipfile", "Procfile", "Dockerfile", "Makefile",
              "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml", "manage.py",
              ".env.example", ".env.sample", "app.json", "package.json")
LOCKFILES = ("uv.lock", "poetry.lock", "Pipfile.lock", "requirements.lock")
HINT_DIRS = ("docker", "deploy", "scripts", ".devcontainer")
SKIP_CODE = SKIP_PARTS | {"tests", "test", "migrations", "static", "node_modules", "frontend", "assets",
                          "docs", "e2e", "tests_e2e", "fixtures", "locale"}
PRIORITY = ("settings", "models", "urls", "routes", "auth", "api", "serializers", "schemas", "views",
            "app", "main", "wsgi", "asgi", "config", "seed", "db", "database")
MAX_HINT_FILE, MAX_HINTS, MAX_CODE = 6_000, 60_000, 150_000

PROMPT = """You set up a web API so that an automated tester can run it. The tester ("Doppel") boots the
app inside an isolated Linux sandbox from a base image, loads starting data once, then forks copies
and sends HTTP+JSON requests to it. You write two files.

1) doppel.toml (TOML), with these keys:
[app]
image = "python:3.12-slim"   # Docker Hub image to start from; pick the Python version the project needs
install = "..."              # install dependencies, run once in the repo root (shell is available)
start = "..."                # start the server in the FOREGROUND, listening on 0.0.0.0 and the port in $PORT
ready = "/..."               # a GET path that answers < 500 once the app is up (a health route, docs, or a list)
seed = "..."                 # run once: create the database schema, then `python doppel_seed.py`
migrate = "..."              # run in each copy after the code changes: apply schema migrations ("" if none)
[personas]
guide = \"\"\"...\"\"\"          # 2-4 sentences for the user-writer: how to log in, which seeded accounts exist
[personas.login]             # only if most API routes need a login, else omit this whole table
method = "POST"
path = "/..."
auth = ["user", "password"]  # HTTP Basic, OR json = {{...}} for a JSON body; omit both if not needed
save = {{ token = "dotted.path.in.response" }}   # omit if the token is fixed (see seed rules)
header = {{ Authorization = "Bearer {{token}}" }}
[diff]
ignore = [...]               # JSON keys that change on every run: timestamps, fresh tokens, random ids
slow_ratio = 3.0
slow_min_ms = 400

2) doppel_seed.py: a standalone Python script, run from the repo root after the schema exists. It
creates realistic starting data using the app's own models or functions: 2-3 users with known
usernames and passwords (use the password "doppel-pass"), and 5-15 domain records (posts, orders,
bookmarks...). If the API authenticates with stored API tokens, create one per user with a FIXED
40-character value so requests can send it directly. Then make [personas.login] a GET of a route that
needs the token, with that header and no auth/save, e.g. method = "GET", path = "/api/me/",
header = {{ Authorization = "Token aaaa...aaaa" }}; the guide states the exact header too.
Be deterministic: no randomness, no network calls.

Hard rules:
- No Docker, no outside services. Use SQLite (or the app's built-in file/in-memory store) even if
  production uses Postgres, MySQL or Redis; set that through the app's own settings or env vars.
- Turn off background workers, schedulers, email, analytics and outbound HTTP (metadata fetching,
  webhooks) using the app's own settings or env vars, so runs are fast and repeatable.
- Commands run with a POSIX shell in the repo root. Set env vars inline, e.g.
  "env APP_DB=sqlite python manage.py migrate". Prefer the dev server or a single-worker server.
- Install only what the app needs to serve its API. Skip production servers that need a compiler
  (uwsgi), test and lint tools, and frontend builds.
- Read settings files carefully: use real setting names and module paths from the code below.

Return ONLY JSON:
{{"files": [{{"path": "doppel.toml", "content": "..."}}, {{"path": "doppel_seed.py", "content": "..."}}],
  "notes": "one or two sentences: what you assumed"}}
{feedback}
Repository setup hints:
{hints}

Repository code:
{code}
"""

FEEDBACK = """
A previous attempt FAILED. Fix the cause, keep what worked, and return both files again.
Error:
{error}

Previous files:
{files}
"""


# ---------------------------------------------------------------- 1. gather


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n... [{len(text) - limit} more characters]\n"


def file_tree(repo: Path, limit: int = 300) -> str:
    rows = []
    for p in sorted(repo.rglob("*")):
        rel = p.relative_to(repo)
        if SKIP_PARTS & set(rel.parts) or len(rel.parts) > 3:
            continue
        rows.append(rel.as_posix() + ("/" if p.is_dir() else ""))
        if len(rows) >= limit:
            rows.append("...")
            break
    return "\n".join(rows)


def gather_hints(repo: str | Path) -> str:
    """The files where a repo says how it installs and runs: manifests, CI, Docker, README."""
    repo = Path(repo).resolve()
    picks: list[Path] = [repo / n for n in HINT_NAMES if (repo / n).is_file()]
    picks += sorted(repo.glob("requirements*.txt")) + sorted(repo.glob("*.sh"))
    picks += sorted((repo / ".github" / "workflows").glob("*.y*ml"))
    for d in HINT_DIRS:
        picks += sorted(p for p in (repo / d).glob("*") if p.is_file() and p.stat().st_size < 20_000)
    readme = next((p for p in sorted(repo.glob("README*")) if p.is_file()), None)
    parts = ["### file tree (depth 3)\n" + file_tree(repo)]
    parts += [f"### {n} exists (lockfile, not shown)" for n in LOCKFILES if (repo / n).exists()]
    for p in dict.fromkeys(picks):  # keep order, drop duplicates
        parts.append(f"### {p.relative_to(repo).as_posix()}\n"
                     + _clip(p.read_text("utf-8", "replace"), MAX_HINT_FILE))
    if readme:
        parts.append(f"### {readme.name}\n" + _clip(readme.read_text("utf-8", "replace"), 8_000))
    return _clip("\n".join(parts), MAX_HINTS)


def _rank(rel: Path) -> tuple:
    stem = rel.stem.lower()
    hit = next((i for i, word in enumerate(PRIORITY) if word in stem or word in rel.parts), len(PRIORITY))
    return (hit, len(rel.parts), rel.as_posix())


def gather_code(repo: str | Path, limit: int = MAX_CODE) -> str:
    """Python source, settings and models first, so a big repo still shows what the seed needs."""
    repo = Path(repo).resolve()
    files = [p.relative_to(repo) for p in repo.rglob("*.py")
             if p.is_file() and not (SKIP_CODE & set(p.relative_to(repo).parts))]
    parts, used = [], 0
    for rel in sorted(files, key=_rank):
        block = f"### {rel.as_posix()}\n{(repo / rel).read_text('utf-8', 'replace')}\n"
        if used + len(block) > limit:
            continue  # a smaller, lower-ranked file may still fit
        parts.append(block)
        used += len(block)
    return "".join(parts)


# ---------------------------------------------------------------- 2. draft


def draft(repo: str | Path, error: str = "", previous: dict | None = None, chat=nemotron_chat,
          model: str | None = None) -> dict:
    """Ask Nemotron for the two files. Returns {"files": {name: text}, "notes": str}."""
    model = model or os.getenv("INIT_MODEL", "nvidia/Nemotron-3-Ultra-550b-a55b")
    feedback = ""
    if error:
        shown = "\n".join(f"--- {k}\n{v}" for k, v in (previous or {}).items())
        feedback = FEEDBACK.format(error=_clip(error, 6_000), files=shown)
    prompt = PROMPT.format(feedback=feedback, hints=gather_hints(repo), code=gather_code(repo))
    text, _, _ = chat(model, prompt, temperature=0.2)
    reply = parse_json(text, key="files") or {}
    files = {}
    for f in reply.get("files") or []:
        if isinstance(f, dict) and f.get("path") in ALLOWED_FILES and isinstance(f.get("content"), str):
            files[f["path"]] = f["content"].rstrip() + "\n"
    if SPEC_FILE not in files:
        raise ValueError(f"model returned no {SPEC_FILE}. Reply started with: {text[:300]!r}")
    return {"files": files, "notes": str(reply.get("notes", ""))}


# ---------------------------------------------------------------- 3-5. verify and calibrate


def smoke_persona(spec: TwinSpec) -> dict:
    """The smallest proof the twin works: the ready route, and the owner's login if there is one."""
    from doppel.rehearsal import with_login
    persona = {"name": "doppel-init smoke", "goal": "app starts and the seeded login works",
               "steps": [{"method": "GET", "path": spec.ready}]}
    return with_login(persona, spec.login) if spec.login else persona


def smoke_problem(recording: dict, spec: TwinSpec) -> str:
    """Why the smoke run is not good enough, or "" if it is."""
    steps = recording.get("steps", [])
    for s in steps:
        if s["status"] == 0 or s["status"] >= 500:
            return f"{s['request']} answered {s['status'] or 'nothing'}: {str(s.get('body'))[:500]}"
    if spec.login and steps and steps[0]["status"] >= 400:
        return (f"the login step {steps[0]['request']} was refused with {steps[0]['status']}: "
                f"{str(steps[0].get('body'))[:500]}. The seeded account or the login settings are wrong.")
    return ""


def noisy_keys(base: list[dict], head: list[dict]) -> list[str]:
    """JSON keys whose values differ between two replays of the SAME code from the SAME data."""
    keys: set[str] = set()
    head_by_name = {r["persona"]: r for r in head}
    for rb in base:
        rh = head_by_name.get(rb["persona"])
        if rh is None:
            continue
        for sb, sh in zip(rb["steps"], rh["steps"], strict=False):
            if sb["status"] != sh["status"]:
                continue
            for path in changed_paths(sb["body"], sh["body"], limit=500):
                path = path.split(" (")[0]
                names = [p for p in path.split(".") if not p.isdigit() and p != "(whole body)"]
                if names:
                    keys.add(names[-1])
    return sorted(keys)


def flaky_statuses(base: list[dict], head: list[dict]) -> list[str]:
    """Requests that got a different status in two identical runs: noise the ignore list can't fix."""
    head_by_name = {r["persona"]: r for r in head}
    out = []
    for rb in base:
        for sb, sh in zip(rb["steps"], head_by_name.get(rb["persona"], {}).get("steps", []), strict=False):
            if sb["status"] != sh["status"]:
                out.append(f"{rb['persona']}: {sb['request']} {sb['status']} vs {sh['status']}")
    return out


def coverage(recordings: list[dict]) -> float:
    steps = [s for r in recordings for s in r["steps"]]
    return sum(1 for s in steps if 0 < s["status"] < 400) / len(steps) if steps else 0.0


def with_ignore(toml_text: str, extra: list[str]) -> str:
    """doppel.toml with `extra` merged into [diff] ignore, keeping the rest of the file as written."""
    data = tomllib.loads(toml_text)
    keys = sorted(set(data.get("diff", {}).get("ignore", [])) | set(extra))
    line = "ignore = " + json.dumps(keys)
    pattern = re.compile(r"(?ms)^(\[diff\][^\[]*?)^ignore\s*=\s*\[.*?\]")
    if pattern.search(toml_text):
        return pattern.sub(lambda m: m.group(1) + line, toml_text, count=1)
    if re.search(r"(?m)^\[diff\]\s*$", toml_text):
        return re.sub(r"(?m)^\[diff\]\s*$", "[diff]\n" + line, toml_text, count=1)
    return toml_text.rstrip() + "\n\n[diff]\n" + line + "\n"


def _workspace(repo: Path, files: dict, root: Path) -> Path:
    work = root / "app"
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(repo, work, ignore=shutil.ignore_patterns(*SKIP_PARTS))
    for name, text in files.items():
        (work / name).write_text(text, encoding="utf-8")
    return work


def _run_twin(backend: str):
    if backend == "local":
        from doppel.backends.local import run_twin
    else:
        from doppel.backends.nebius import run_twin
    return run_twin


def try_files(repo: Path, files: dict, backend: str = "nebius", run_twin=None, chat=nemotron_chat,
              personas: int = 6) -> dict:
    """Boot the twin with these files. Returns {"ok": bool, "error": str, "files": ..., ...}."""
    run_twin = run_twin or _run_twin(backend)
    with tempfile.TemporaryDirectory(prefix="doppel-init-", ignore_cleanup_errors=True) as tmp:
        work = _workspace(repo, files, Path(tmp))
        try:
            spec = TwinSpec.load(work / SPEC_FILE)
        except (ValueError, tomllib.TOMLDecodeError) as e:
            return {"ok": False, "error": f"{SPEC_FILE} is invalid: {e}"}
        # verify: one smoke persona; seed, migrate, start, ready and login errors surface here
        try:
            smoke = run_twin(work, work, spec, [smoke_persona(spec)])
        except Exception as e:  # noqa: BLE001 - every failure is feedback for the next draft
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        if smoke.get("failed"):
            return {"ok": False, "error": smoke["failed"][0]["error"]}
        problem = smoke_problem(smoke["base"][0], spec) if smoke["base"] else "the smoke persona never ran"
        if problem:
            return {"ok": False, "error": problem}
        # calibrate: invented users against the same code twice; what differs is noise
        from doppel.personas import generate
        users = generate(work, n=personas, guide=spec.persona_guide, chat=chat)["personas"]
        twice = run_twin(work, work, spec, users)
    noise = noisy_keys(twice["base"], twice["head"])
    files = {**files, SPEC_FILE: with_ignore(files[SPEC_FILE], noise)}
    return {"ok": True, "error": "", "files": files, "noise": noise,
            "flaky": flaky_statuses(twice["base"], twice["head"]),
            "coverage": coverage(twice["base"]), "personas": len(twice["base"])}


# ---------------------------------------------------------------- the loop


def init(repo: str | Path, backend: str = "nebius", tries: int = 3, chat=nemotron_chat, run_twin=None,
         personas: int = 6, log=lambda msg: print(msg, file=sys.stderr)) -> dict:
    """Draft, verify, repair, calibrate. Returns the last attempt with "attempts" and "notes" added;
    "files" holds the calibrated files when "ok" is true. Writes nothing to disk."""
    repo = Path(repo).resolve()
    error, previous, result = "", None, {"ok": False, "error": "no attempt made"}
    for attempt in range(1, tries + 1):
        log(f"[init] attempt {attempt}/{tries}: asking Nemotron for {SPEC_FILE} and {SEED_FILE}")
        try:
            drafted = draft(repo, error, previous, chat=chat)
        except Exception as e:  # noqa: BLE001
            error, result = f"the reply could not be used: {e}", {"ok": False, "error": str(e)}
            log(f"[init] {error}")
            continue
        previous = drafted["files"]
        log(f"[init] booting the twin ({backend}) to check it")
        result = try_files(repo, previous, backend=backend, run_twin=run_twin, chat=chat, personas=personas)
        result.update(attempts=attempt, notes=drafted["notes"])
        result.setdefault("files", previous)
        if result["ok"]:
            log(f"[init] works: {result['personas']} users replayed, {result['coverage']:.0%} of requests "
                f"served, ignoring {len(result['noise'])} noisy key(s)")
            return result
        error = result["error"]
        log(f"[init] failed: {error.splitlines()[0][:200] if error else 'unknown error'}")
    return result


def write(repo: str | Path, files: dict, force: bool = False) -> list[Path]:
    repo = Path(repo)
    clash = [repo / n for n in files if (repo / n).exists()]
    if clash and not force:
        raise FileExistsError(f"{', '.join(p.name for p in clash)} already exist; use --force to overwrite")
    for name, text in files.items():
        (repo / name).write_text(text, encoding="utf-8")
    return [repo / n for n in files]
