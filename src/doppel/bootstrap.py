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

import ast
import json
import os
import re
import shlex
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
SEED_REQUESTS_FILE = "doppel_seed.json"
ALLOWED_FILES = {SPEC_FILE, SEED_FILE, SEED_REQUESTS_FILE}
HINT_NAMES = ("pyproject.toml", "setup.py", "setup.cfg", "Pipfile", "Procfile", "Dockerfile", "Makefile",
              "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml", "manage.py",
              ".env.example", ".env.sample", "app.json", "package.json")
LOCKFILES = ("uv.lock", "poetry.lock", "Pipfile.lock", "requirements.lock")
HINT_DIRS = ("docker", "deploy", "scripts", ".devcontainer")
SKIP_CODE = SKIP_PARTS | {"tests", "test", "migrations", "static", "node_modules", "frontend", "assets",
                          "docs", "e2e", "tests_e2e", "fixtures", "locale"}
PRIORITY = ("settings", "models", "urls", "routes", "auth", "api", "serializers", "schemas", "views",
            "app", "main", "wsgi", "asgi", "config", "seed", "db", "database")
MAX_HINT_FILE, MAX_HINTS, MAX_CODE = 6_000, 60_000, 120_000

PROMPT = """You set up a web API so that an automated tester can run it. The tester ("Doppel") boots the
app inside an isolated Linux sandbox from a base image, loads starting data once, then forks copies
and sends HTTP requests to it (JSON APIs and HTML pages with forms alike). You write two files.

1) doppel.toml (TOML), with these keys:
[app]
image = "python:3.12-slim"   # Docker Hub image to start from; pick the Python version the project needs
install = "..."              # install dependencies, run once in the repo root (shell is available)
start = "..."                # start the server in the FOREGROUND, listening on 0.0.0.0 and the port in $PORT
ready = "/..."               # a GET path that answers < 500 once the app is up (a health route, docs, or a list)
seed = "..."                 # run once: create the database schema (the app's own command when it has one),
                             # then `python doppel_seed.py` if you write a seed script
seed_requests = "doppel_seed.json"  # PREFERRED when the app has routes that create users and records: run once
                             # against the started app, after `seed`. Omit the line if you only use the script
migrate = "..."              # run in each copy after the code changes: apply schema migrations ("" if none)
env = {{ NAME = "value" }}     # env vars for EVERY command above (install, seed, migrate, start), set once
[personas]
guide = \"\"\"...\"\"\"          # 2-4 sentences for the user-writer: how to log in, which seeded accounts exist
[personas.login]             # only if most routes (API or pages) need a login, else omit this whole table
method = "POST"
path = "/..."
auth = ["<a username your seed creates>", "<its password>"]  # HTTP Basic, OR json = {{...}} for a JSON body,
                             # OR form = {{ username = "...", password = "..." }} for an HTML login form
                             # (request.form, a Form class, Django's LoginView): its session cookie is kept
save = {{ token = "dotted.path.in.response" }}   # omit if the token is fixed (see seed rules) or for a form login
header = {{ Authorization = "Bearer {{token}}" }}  # omit for a form login: the session cookie does the work
[diff]
ignore = [...]               # JSON keys that change on every run: timestamps, fresh tokens, random ids
slow_ratio = 3.0
slow_min_ms = 400

2) Starting data, in one of two ways (or both):

2a) doppel_seed.json, PREFERRED whenever the app has routes that create what is needed (a sign-up or
register route for users, routes that create the domain records): a JSON list of HTTP steps that Doppel
sends once to the running app, in order, keeping cookies between them like one browser:
[{{"method": "POST", "path": "/auth/register", "form": {{"username": "alice", "password": "doppel-pass"}}}},
 {{"method": "POST", "path": "/auth/login", "form": {{"username": "alice", "password": "doppel-pass"}}}},
 {{"method": "POST", "path": "/posts", "json": {{"title": "Hello", "body": "..."}}}}]
Each step takes "form" (an HTML form; hidden fields such as CSRF tokens are filled in automatically from
the page, so GET the form's page first) or "json" (a JSON body), "headers", "auth" ([user, password] for
HTTP Basic) and "save" ({{"token": "access_token"}} keeps a value from the reply for later steps as {{token}}).
Every step must succeed (an answer below 400). The app then hashes the passwords, fills its own required
fields and writes to its own database, so none of that can go wrong. Use the facts' routes and what each
view reads. Create 2-3 users (password "doppel-pass") and 5-15 records, logging in as each user before
creating their records.

2b) doppel_seed.py, only for data no route can create (accounts when sign-up is closed, an admin user,
stored API keys): a standalone Python script, run from the repo root after the schema exists. It
creates realistic starting data using the app's own models or functions: 2-3 users with known
usernames and passwords (use the password "doppel-pass"), and 5-15 domain records (posts, orders,
bookmarks...). If the API authenticates with stored API tokens, create one per user with a FIXED
40-character value so requests can send it directly. Do this ONLY for stored API keys with no route
that hands them out. If the app has a route that issues tokens or logs in (a POST that checks a
password), log in through it instead: tokens it issues are valid by construction, while a seeded value
fails whenever the app signs (JWT), hashes or expires its tokens. Then make [personas.login] a GET of a route that
needs the token, with that header and no auth/save, e.g. method = "GET", path = "/api/me/",
header = {{ Authorization = "Token aaaa...aaaa" }}; the guide states the exact header too.
Create records with the app's model classes and their real fields (e.g. Model.objects.create(...)
in Django, session.add(Model(...)) in SQLAlchemy), not with service or helper functions: the models
are shown below, helpers may not be. Fill every required field, including dates, with fixed values;
but anything with an expiry (tokens, sessions, invitations) must still be valid when the twin runs, so
give expiry dates far in the future, like the year 2100.
Keep doppel_seed.py short (under 80 lines) and the notes to two sentences. End the seed by printing
one line with the database it wrote to and what it created, e.g. "seeded 3 users, 12 posts into <url>".
Set passwords through the app's own hashing (a password property, set_password, create_user), never by
writing a plain string into a password-hash field.
Be deterministic: no randomness, no network calls. Use only names, fields and parameters that appear in
the API index below; the seed is checked against the real code before anything runs.

Hard rules:
- No Docker, no outside services. Use SQLite (or the app's built-in file/in-memory store) even if
  production uses Postgres, MySQL or Redis; set that through the app's own settings or env vars.
- Turn off background workers, schedulers, email, analytics and outbound HTTP (metadata fetching,
  webhooks) using the app's own settings or env vars, so runs are fast and repeatable.
- Commands run with a POSIX shell in the repo root. Put every env var (database URL, settings module,
  feature switches) in [app] env, never inline in one command and never inside doppel_seed.py: the seed
  and the server must see the same settings, or the seed fills one database while the app reads
  another. Prefer the dev server or a single-worker server.
- Install only what the app needs to serve its API. Skip production servers that need a compiler
  (uwsgi), test and lint tools, and frontend builds. The base image has NO C compiler. Lockfile
  installs (`uv sync`, `poetry install`, `pip install .` or `-e .`) install everything the project
  lists, so if it lists such a server, `pip install` the other dependencies by name instead.
- Keep every command short: set each env var once, never repeat a setting. Only use env var and
  setting names that appear in the code below; never invent one.
- SQLite needs the folder of each database file to exist. Read where the settings put every
  SQLite file (the main database, task queues, caches) and `mkdir -p` those folders in `seed`
  before anything opens them; they are usually git-ignored, so the checkout does not have them.
- Read settings files carefully: use real setting names and module paths from the code below.

Return ONLY JSON (include doppel_seed.json and/or doppel_seed.py, whichever you use):
{{"files": [{{"path": "doppel.toml", "content": "..."}}, {{"path": "doppel_seed.json", "content": "[...]"}},
            {{"path": "doppel_seed.py", "content": "..."}}],
  "notes": "one or two sentences: what you assumed"}}
{feedback}
{facts}{install_hint}{versions}Repository setup hints:
{hints}

Repository API index (every top-level function and class, with model fields):
{index}

Repository code (the most relevant files; a big repo is cut, the API index is not):
{code}
"""

FEEDBACK = """
A previous attempt FAILED. Fix the cause, keep what worked, and return both files again.
{kept}{history}{repeat}Error of the last attempt:
{error}
{source}
Previous files:
{files}
"""


# ---------------------------------------------------------------- 1. gather


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n... [{len(text) - limit} more characters]\n"


def _clip_error(text: str, limit: int = 6_000) -> str:
    """Keep both ends of a long error: the command up top, the actual cause at the bottom
    (pip and uv print pages of traceback before the one line that matters)."""
    if len(text) <= limit:
        return text
    head, tail = limit // 4, limit - limit // 4
    return f"{text[:head]}\n... [{len(text) - limit} characters cut] ...\n{text[-tail:]}"


def file_tree(repo: Path, limit: int = 300) -> str:
    rows = []
    for p in sorted(repo.rglob("*")):
        rel = p.relative_to(repo)
        if SKIP_PARTS & set(rel.parts) or len(rel.parts) > 3 or rel.parts[0].startswith(".doppel-"):
            continue  # .doppel-trace/ and .doppel-init.json are Doppel's own files, not the app's
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


NAME_PATTERNS = (r"(\w+)\(\) got an unexpected keyword argument", r"(\w+)\(\) missing \d+ required",
                 r"(\w+)\(\) takes", r"has no attribute '(\w+)'", r"cannot import name '(\w+)'",
                 r"NOT NULL constraint failed: \w+?_(\w+)\.", r"UNIQUE constraint failed: \w+?_(\w+)\.",
                 r"(\w+) matching query does not exist")
FRAME = re.compile(r'File "([^"]+\.py)", line (\d+)')


def _repo_file(repo: Path, path: str) -> Path | None:
    """The repo file a sandbox or temp path points at (/app/bookmarks/x.py -> bookmarks/x.py)."""
    parts = [p for p in Path(path.replace("\\", "/")).parts if p not in ("/", "\\") and not p.endswith(":\\")]
    for i in range(len(parts)):
        candidate = repo.joinpath(*parts[i:])
        # inside the repo only: a library frame (/usr/lib/python3.12/socketserver.py) is not app code
        if parts[i:] and candidate.is_file() and not SKIP_PARTS & set(parts[i:]) \
                and candidate.resolve().is_relative_to(repo.resolve()):
            return candidate
    return None


def _definition(repo: Path, name: str, lines: int = 30, any_case: bool = False) -> str:
    """The first def or class called `name`. any_case: a class whose name matches ignoring case, for
    database table names (bookmarks_apitoken -> class ApiToken)."""
    pattern = (re.compile(rf"^\s*class\s+{re.escape(name)}\b", re.I) if any_case
               else re.compile(rf"^\s*(async\s+def|def|class)\s+{re.escape(name)}\b"))
    for p in sorted(repo.rglob("*.py")):
        rel = p.relative_to(repo)
        if SKIP_PARTS & set(rel.parts):
            continue
        text = p.read_text("utf-8", "replace").splitlines()
        for i, line in enumerate(text):
            if pattern.match(line):
                return f"### {rel.as_posix()} (line {i + 1})\n" + "\n".join(text[i:i + lines])
    return ""


def related_source(repo: str | Path, error: str, limit: int = 10_000) -> str:
    """Real code the error points at: the repo's own traceback frames, and the definition of any
    function or class the error names. A model that guessed a signature can't fix it from the error
    alone; it needs to see the definition it got wrong."""
    repo = Path(repo).resolve()
    snippets: list[str] = []
    for path, line in FRAME.findall(error)[-6:]:  # innermost frames last; keep the end of the chain
        f = _repo_file(repo, path)
        if f is None or f.name == SEED_FILE:
            continue
        text, n = f.read_text("utf-8", "replace").splitlines(), int(line)
        lo = max(0, n - 12)
        snippets.append(f"### {f.relative_to(repo).as_posix()} (around line {n})\n" + "\n".join(text[lo:n + 12]))
    names = dict.fromkeys(m for pat in NAME_PATTERNS for m in re.findall(pat, error))
    for name in names:
        found = (_definition(repo, name) or (_definition(repo, name.title().replace("_", "")) if "_" in name else "")
                 or _definition(repo, name.replace("_", ""), any_case=True))
        if found:
            snippets.append(found)
    out, used = [], 0
    for snip in dict.fromkeys(snippets):
        if used + len(snip) > limit:
            break
        out.append(snip)
        used += len(snip)
    if re.search(r"NOT NULL constraint failed|violates not-null constraint", error):
        # linkding: Tag.date_added is a plain DateTimeField with no default, so get_or_create(name=...)
        # fails; the model had the whole models.py in its prompt and still missed it
        out.insert(0, "A NOT NULL constraint failed: a required field got no value. Pass that field explicitly "
                      "in every create()/get_or_create() for that model (in defaults= for get_or_create), "
                      "e.g. a fixed datetime for a date field. Its definition is below.")
    if re.search(r"UNIQUE constraint failed|duplicate key value violates unique constraint", error):
        # linkding: the seed gave every user's ApiToken the same key; the model's save() makes a random
        # one when the key is left empty, which the model can only know from the source above
        out.insert(0, "A UNIQUE constraint failed: two rows got the same value for that field. Give each row its "
                      "own value, or leave the field out if the model below fills it in itself (look at save()).")
    routes = route_source(repo) if re.search(r"(refused with|answered) 404", error) else ""
    routes += auth_source(repo) if re.search(r"(refused with|answered) 40[13]", error) else ""
    return (("\nSource the error points at (real code from this repo; trust it over your memory):\n"
             + "\n\n".join(out) + "\n") if out else "") + routes


# ---------------------------------------------------------------- install: derived, not guessed
#
# Every run, Nemotron rediscovered the install from scratch (uv sync, pip install -r pyproject.toml,
# pip install -e . ...), each wrong guess costing a sandbox boot. Doppel can read the dependency list
# itself, drop what can't build without a compiler, and hand the model a command that works.

NEEDS_COMPILER = {"uwsgi": None, "mod-wsgi": None, "mysqlclient": None, "python-ldap": None,
                  "pycairo": None, "psycopg2": "psycopg2-binary"}  # None: drop it; str: install this instead
WHOLE_PROJECT = re.compile(r"\buv sync\b|\bpoetry install\b|\bpdm install\b|\bpip install\s+(-e\s+)?\.(\s|$|\[)")


def _req_name(req: str) -> str:
    return re.split(r"[\s<>=!~;\[(]", req.strip(), maxsplit=1)[0].lower().replace("_", "-")


def dependencies(repo: str | Path) -> tuple[list[str], str]:
    """The app's runtime requirements and the file they came from."""
    repo = Path(repo)
    for name in ("requirements.txt", "requirements/base.txt", "requirements/prod.txt"):
        f = repo / name  # pinned versions the app is tested with beat the loose ranges in pyproject
        if f.is_file():
            reqs = [ln.split("#")[0].strip() for ln in f.read_text("utf-8", "replace").splitlines()]
            reqs = [r for r in reqs if r and not r.startswith("-")]
            if reqs:
                return reqs, name
    pyproject = repo / "pyproject.toml"
    if pyproject.is_file():
        try:
            data = tomllib.loads(pyproject.read_text("utf-8"))
        except tomllib.TOMLDecodeError:
            data = {}
        deps = data.get("project", {}).get("dependencies")
        if deps:
            return list(deps), "pyproject.toml"
        poetry = data.get("tool", {}).get("poetry", {}).get("dependencies") or {}
        if poetry:
            return [n for n in poetry if n.lower() != "python"], "pyproject.toml [tool.poetry]"
    for name in ("requirements.txt", "requirements/base.txt", "requirements/prod.txt"):
        f = repo / name
        if f.is_file():
            reqs = [ln.split("#")[0].strip() for ln in f.read_text("utf-8", "replace").splitlines()]
            return [r for r in reqs if r and not r.startswith("-")], name
    return [], ""


def compiled_deps(repo: str | Path) -> list[str]:
    return [_req_name(r) for r in dependencies(repo)[0] if _req_name(r) in NEEDS_COMPILER]


def derived_install(repo: str | Path) -> str:
    """A pip command for the app's dependencies with compiler-only packages dropped or swapped."""
    reqs, _ = dependencies(repo)
    keep = []
    for r in reqs:
        swap = NEEDS_COMPILER.get(_req_name(r), r) if _req_name(r) in NEEDS_COMPILER else r
        if swap:
            keep.append("'" + swap.replace("'", "") + "'")
    return "pip install --quiet " + " ".join(keep) if keep else ""


def install_hint(repo: str | Path) -> str:
    cmd, source = derived_install(repo), dependencies(repo)[1]
    if not cmd:
        return ""
    dropped = compiled_deps(repo)
    why = f" ({', '.join(dropped)} removed: it needs a C compiler)" if dropped else ""
    return (f"Suggested install, derived by Doppel from {source}{why}. Use it as `install` unless the code "
            f"shows it is incomplete:\n{cmd}\n\n")


ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(\S*)$")


def command_env(cmd: str) -> dict[str, str]:
    """K=V assignments a command sets inline (`env K=V cmd`, `K=V cmd`, `export K=V; cmd`)."""
    out: dict[str, str] = {}
    for part in re.split(r"&&|\|\||;", cmd or ""):
        try:
            words = shlex.split(part)
        except ValueError:
            words = part.split()
        if words[:1] in (["env"], ["export"]):
            words = words[1:]
        for w in words:
            m = ASSIGNMENT.match(w)
            if not m:
                break
            out[m.group(1)] = m.group(2)
    return out


def _seed_env_writes(seed: str) -> list[tuple[int, str, str, bool]]:
    """(line, name, value, is_setdefault) for each os.environ write with constant arguments."""
    try:
        tree = ast.parse(seed)
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Subscript) \
                and ast.unparse(node.targets[0].value).endswith("environ") \
                and isinstance(node.targets[0].slice, ast.Constant) and isinstance(node.value, ast.Constant):
            out.append((node.lineno, str(node.targets[0].slice.value), str(node.value.value), False))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in ("setdefault", "putenv") \
                and ast.unparse(node.func.value).endswith(("environ", "os")) \
                and len(node.args) == 2 and all(isinstance(a, ast.Constant) for a in node.args):
            key, value = (str(a.value) for a in node.args)
            out.append((node.lineno, key, value, node.func.attr == "setdefault"))
    return out


def check_env(repo: str | Path, toml_text: str, seed: str = "") -> list[str]:
    """The seed and the server must run with the same settings (microblog: the seed filled the database
    named in an env var only the seed had, the server read its default one, and every login got 401)."""
    try:
        app = tomllib.loads(toml_text).get("app", {})
    except tomllib.TOMLDecodeError:
        return []
    shared = {str(k): str(v) for k, v in (app.get("env") or {}).items()}
    start = {**shared, **command_env(app.get("start", ""))}
    problems = []
    for stage in ("seed", "migrate"):
        for name, value in command_env(app.get(stage, "")).items():
            if start.get(name) != value:
                theirs = f"sets {name}={start[name]}" if name in start else "does not"
                problems.append(f"`{stage}` sets {name}={value} but `start` {theirs}, so the app won't see "
                                f"what the {stage} did; put {name} in [app] env once")
    repo = Path(repo)
    for line, name, value, setdefault in _seed_env_writes(seed):
        if start.get(name) == value:
            continue
        if setdefault and _repo_setdefault(repo, name, value):
            continue  # the app sets the same default itself (manage.py, wsgi.py)
        problems.append(f"{SEED_FILE} line {line} sets {name}={value!r} for itself, but the server doesn't get it; "
                        f"put {name} in [app] env so the seed and the server agree")
    return problems


def seed_passwords(seed: str) -> set[str]:
    """Literal passwords the seed gives users (password=..., set_password(...), create_user(..., pw),
    obj.password = ...), with module-level constants like PASSWORD = "x" resolved."""
    try:
        tree = ast.parse(seed)
    except SyntaxError:
        return set()
    consts = {t.id: n.value.value for n in tree.body if isinstance(n, ast.Assign)
              and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)
              for t in n.targets if isinstance(t, ast.Name)}

    def literal(node):
        if isinstance(node, ast.Call) and node.args and "hash" in ast.unparse(node.func).lower():
            node = node.args[0]  # password=generate_password_hash("x"), make_password("x")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return consts.get(node.id) if isinstance(node, ast.Name) else None

    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = ast.unparse(node.func).rsplit(".", 1)[-1]
            if "hash" in name.lower() and "check" not in name.lower() and node.args:
                found.add(literal(node.args[0]))  # raw SQL seeds: VALUES (?, ?), ("alice", hash("x"))
            for kw in node.keywords:
                if kw.arg in ("password", "raw_password", "pwd", "passwd"):
                    found.add(literal(kw.value))
            if name == "set_password" and node.args:
                found.add(literal(node.args[0]))
            if name in ("create_user", "create_superuser") and len(node.args) >= 3:
                found.add(literal(node.args[2]))
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Attribute) \
                and node.targets[0].attr == "password":
            found.add(literal(node.value))
    return {p for p in found if p}


def front_door_passwords(seed_json: str) -> set[str]:
    """Passwords the front-door seed sends when it signs users up (fields named like "password")."""
    try:
        data = json.loads(seed_json or "[]")
    except ValueError:
        return set()
    steps = data.get("steps", []) if isinstance(data, dict) else data
    out = set()
    for step in steps if isinstance(steps, list) else []:
        body = (step.get("form") or step.get("json")) if isinstance(step, dict) else None
        if isinstance(body, dict):
            out |= {str(v) for k, v in body.items() if "pass" in str(k).lower() and "confirm" not in str(k).lower()}
        if isinstance(step, dict) and isinstance(step.get("auth"), list) and len(step["auth"]) == 2:
            out.add(str(step["auth"][1]))
    return out


def check_login_matches_seed(toml_text: str, seed: str, seed_json: str = "") -> list[str]:
    """The password the login sends must be one the seed set (by script or through the front door)."""
    try:
        login = tomllib.loads(toml_text).get("personas", {}).get("login") or {}
    except tomllib.TOMLDecodeError:
        return []
    body = login.get("json") if isinstance(login.get("json"), dict) else login.get("form") or {}
    sent = login["auth"][1] if isinstance(login.get("auth"), list) and len(login["auth"]) == 2 else \
        next((v for k, v in body.items() if "pass" in k.lower()), None) if isinstance(body, dict) else None
    set_by_seed = seed_passwords(seed) | front_door_passwords(seed_json)
    if sent and set_by_seed and sent not in set_by_seed:
        return [f"the login sends password {sent!r}, but the seed sets {sorted(set_by_seed)}; use the same one"]
    return []


def _repo_setdefault(repo: Path, name: str, value: str) -> bool:
    pattern = re.compile(rf"setdefault\(\s*['\"]{re.escape(name)}['\"]\s*,\s*['\"]{re.escape(value)}['\"]")
    return any(pattern.search(p.read_text("utf-8", "replace")) for p in _py_files(repo)
               if p.name in ("manage.py", "wsgi.py", "asgi.py", "app.py", "__init__.py", "settings.py"))


DB_SETTING = re.compile(r"DATABASE|SQLALCHEMY|DB_URL|DB_PATH|DB_NAME|\bsqlite\b|database_url", re.I)


def database_config(repo: Path, limit: int = 2_500) -> str:
    """Where the app finds its database: the seed must write there."""
    hits = []
    for path in _py_files(repo):
        rel = path.relative_to(repo).as_posix()
        if not re.search(r"settings|config|app\.py$|__init__\.py$|database|db\.py$", rel):
            continue
        for n, ln in enumerate(path.read_text("utf-8", "replace").splitlines(), 1):
            if DB_SETTING.search(ln) and not ln.strip().startswith("#"):
                hits.append(f"{rel}:{n}: {ln.strip()}")
    text = _clip("\n".join(hits), limit)
    return f"\nWhere the app finds its database (the seed must fill exactly this one):\n{text}" if hits else ""


def route_uses_basic(repo: Path, path: str) -> bool:
    """Does the route declared for the last segment of `path` say HTTP Basic in the lines around it?"""
    segment = path.rstrip("/").rsplit("/", 1)[-1]
    if not segment:
        return False
    literal = re.compile(r"""['"]/?""" + re.escape(segment) + r"""/?['"]""")
    for _, keep in route_lines(repo):
        for k, ln in enumerate(keep):
            if literal.search(ln.split(":", 1)[1]) and any("basic" in x.lower() for x in keep[k:k + 3]):
                return True
    return False


FORM_LOGIN = re.compile(r"request\.form\b|request\.POST\b|LoginView|AuthenticationForm|validate_on_submit|"
                        r"Form\(|OAuth2PasswordRequestForm|Form\(\.\.\.\)|= Form\(")
ROUTE_DECL = re.compile(r"route\(|\.(get|post|put|api_route)\(|\bpath\(|re_path\(|url\(|add_url_rule\(")


def route_reads_form(repo: Path, path: str) -> bool:
    """Does the view declared for the last segment of `path` read an HTML form (request.form, a Form
    class, Django's LoginView, FastAPI's OAuth2PasswordRequestForm) in the lines after it?"""
    segment = path.rstrip("/").rsplit("/", 1)[-1]
    if not segment:
        return False
    literal = re.compile(r"""['"/]""" + re.escape(segment) + r"""/?['"]""")  # "/login", "accounts/login/"
    for f in sorted(Path(repo).rglob("*.py"))[:4000]:
        if any(part in {"tests", "test", "venv", ".venv", "node_modules", "site-packages", "migrations"}
               for part in f.parts):
            continue
        try:
            lines = f.read_text("utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for k, ln in enumerate(lines):
            if ROUTE_DECL.search(ln) and literal.search(ln) and FORM_LOGIN.search("\n".join(lines[k:k + 30])):
                return True
    return False


PLACEHOLDERS = {"user", "username", "password", "pass", "passwd", "secret", "user@example.com", "your-password"}


def check_spec(repo: str | Path, toml_text: str) -> list[str]:
    """Mistakes in doppel.toml that are certain to fail, found without booting anything."""
    try:
        data = tomllib.loads(toml_text)
    except tomllib.TOMLDecodeError as e:
        return [f"{SPEC_FILE} is not valid TOML: {e}"]
    app, problems = data.get("app", {}), []
    install = app.get("install", "")
    if re.search(r"-r\s+\S*pyproject\.toml", install):
        problems.append("install runs `pip install -r pyproject.toml`, but pip can only read requirements "
                        "files that way, not pyproject.toml")
    dropped = compiled_deps(repo)
    if dropped and WHOLE_PROJECT.search(install):
        problems.append(f"install `{install}` installs everything the project lists, including "
                        f"{', '.join(dropped)}, which needs a C compiler the image doesn't have. "
                        f"Use: {derived_install(repo)}")
    for key in ("ready",):
        if app.get(key) and not str(app[key]).startswith("/"):
            problems.append(f"[app] {key} must be a path starting with /, got {app[key]!r}")
    if app.get("seed_requests") and str(app["seed_requests"]) != SEED_REQUESTS_FILE:
        problems.append(f"[app] seed_requests must be \"{SEED_REQUESTS_FILE}\" (the file you return)")
    if "<repo>" in toml_text:
        problems.append("doppel.toml contains '<repo>': that is a placeholder, not a path. Every command runs in the "
                        "repo root, so write paths relative to it (db.sqlite3, instance/app.sqlite)")
    login = data.get("personas", {}).get("login") or {}
    creds = [str(x) for x in (login.get("auth") or [])] + [str(v) for v in (login.get("json") or {}).values()] \
        + [str(v) for v in (login.get("form") or {}).values()]
    if login.get("path") and str(login.get("method", "POST")).upper() == "POST" and not login.get("form") \
            and (login.get("json") or login.get("auth")) and route_reads_form(Path(repo), str(login["path"])):
        problems.append(f"[personas.login] {login['path']} reads an HTML form (request.form / a Form class / a login "
                        "view), so JSON or HTTP Basic arrives empty: send form = { <its field names> = ... } instead, "
                        "with the field names the view reads, and no save/header (the session cookie is kept)")
    copied = [c for c in creds if c.lower() in PLACEHOLDERS or (c.startswith("<") and c.endswith(">"))]
    if copied:
        problems.append(f"[personas.login] sends {copied}, placeholder text copied from the template, not a real "
                        "account: use a username and the password that doppel_seed.py actually creates")
    if login.get("path") and not login.get("auth") and route_uses_basic(Path(repo), str(login["path"])):
        problems.append(f"[personas.login] {login['path']} authenticates with HTTP Basic (see its decorator), so the "
                        "login needs auth = [\"<username>\", \"<password>\"], not a JSON body or a header")
    if login.get("path") and not str(login["path"]).startswith("/"):
        problems.append(f"[personas.login] path must start with /, got {login['path']!r}")
    return problems


# ---------------------------------------------------------------- what already worked

STAGES = ("install", "seed", "start", "login", "users")


def stage_reached(error: str) -> int:
    """How many stages passed before this error (0 = install failed, 4 = only the users fell short)."""
    if error.startswith(("The files were checked", f"{SEED_FILE} was checked", f"{SPEC_FILE} ")):
        return -1  # caught before booting: says nothing about what works
    if "install (base) failed" in error or "install (head) failed" in error:
        return 0
    if "seed failed" in error or "migrate" in error.split("\n", 1)[0]:
        return 1
    if "the login step" in error:
        return 3
    if error.startswith("the twin boots, but"):
        return 4
    return 2  # the app didn't start or answer


def kept_note(best: int) -> str:
    if best <= 0:
        return ""
    worked = ", ".join(STAGES[:best])
    keep = {1: "the `install` command", 2: "`install`, `seed`, `migrate` and doppel_seed.py",
            3: "`install`, `seed`, `migrate`, `start`, `ready` and doppel_seed.py",
            4: "everything in [app] and doppel_seed.py"}[min(best, 4)]
    return (f"Already proven to work in an earlier attempt: {worked}. Keep {keep} exactly as in the "
            f"previous files below, and change only what the error points at.\n")


ROUTE_FILES = re.compile(r"(^|/)(urls|routes|routing|router|routers|api)\.py$|(^|/)(urls|routes)/")


ROUTE_LINE = re.compile(r"\b(re_)?path\(|\burl\(|\binclude\(|\.register\(|@action\b|url_path|"
                        r"@\w+\.(route|get|post|put|patch|delete|api_route)\(|\bAPIRouter\(|"
                        r"add_url_rule|^\s*class \w+(ViewSet|View|Resource)\b|Blueprint\(|prefix=")


def route_lines(repo: Path) -> list[tuple[str, list[str]]]:
    """(file, route-declaring lines) for every file that declares routes. Routing files come first
    (urls.py, routes.py), then any module with route decorators (microblog: api/tokens.py)."""
    found = []
    for path in _py_files(repo):
        rel = path.relative_to(repo).as_posix()
        lines = path.read_text("utf-8", "replace").splitlines()
        picked: set[int] = set()
        for i, ln in enumerate(lines):
            if ROUTE_LINE.search(ln):
                picked.add(i)
                # a call split over lines (`path(\n "api/user/", include(...))`) or a decorator
                # (`@action(...)` then `def profile`): the next lines carry the route itself
                if ln.rstrip().endswith(("(", ",")) or ln.lstrip().startswith("@"):
                    picked.update(range(i + 1, min(i + 3, len(lines))))
        if picked:
            found.append((rel, [f"{i + 1:>5}: {lines[i].rstrip()}" for i in sorted(picked)]))
    return sorted(found, key=lambda f: (not ROUTE_FILES.search(f[0]), f[0].rsplit("/", 1)[-1] != "urls.py", f[0]))


def _blocks(found: list[tuple[str, list[str]]], limit: int) -> str:
    out, used = [], 0
    for rel, keep in found:
        block = f"### {rel} (route lines only)\n" + "\n".join(keep) + "\n"
        if used + len(block) > limit:
            block = _clip(block, max(0, limit - used))
        out.append(block)
        used += len(block)
        if used >= limit:
            break
    return "".join(out)


def route_source(repo: Path, limit: int = 8_000) -> str:
    """The app's routes for a 404 (the model guessed a path that isn't routed): only the lines that
    declare routes, so a long views module doesn't crowd out urls.py."""
    text = _blocks(route_lines(repo), limit)
    return ("\nThe app's routes (a 404 means that path is not routed; pick one from here):\n" + text) if text else ""


LOGIN_WORDS = re.compile(r"token|login|log_in|signin|sign_in|session|auth|password", re.I)


def login_routes(repo: Path, limit: int = 3_000) -> str:
    """Route declarations whose own line mentions tokens, login or sessions (an app's credential
    issuers), each with the two lines after it (the decorator that says HTTP Basic, the view name).
    Protected routes that merely *use* a token don't count."""
    found = []
    for rel, keep in route_lines(repo):
        numbered = [(int(ln.split(":", 1)[0]), ln) for ln in keep]
        hits: list[str] = []
        for k, (n, ln) in enumerate(numbered):
            code = ln.split(":", 1)[1]
            if ROUTE_LINE.search(code) and LOGIN_WORDS.search(code) and "admin" not in rel:
                hits.append(ln)
                hits += [nl for m, nl in numbered[k + 1:k + 3] if m - n <= 2 and nl not in hits]
        if hits:
            found.append((rel, list(dict.fromkeys(hits))))
    return _blocks(found, limit)


# ---------------------------------------------------------------- auth, for a refused login

AUTH_FILE = re.compile(r"(^|/)(auth\w*|authentication|permissions|security|tokens?)\.py$")
AUTH_SETTING = re.compile(r"AUTHENTICATION|AUTH_USER_MODEL|TOKEN|JWT|SESSION_COOKIE|LOGIN_URL", re.I)


def auth_source(repo: Path, limit: int = 8_000) -> str:
    """For a 401/403: how this app really authenticates. The auth code, the auth settings, and every
    model with "token" in its name (linkding stores API tokens in its own ApiToken, not DRF's Token)."""
    blocks: list[str] = []
    for path in _py_files(repo):
        rel = path.relative_to(repo).as_posix()
        text = path.read_text("utf-8", "replace")
        if AUTH_FILE.search(rel):
            blocks.append(f"### {rel}\n{_clip(text, 3_000)}")
        elif "settings" in rel or rel.endswith("config.py"):
            hits = [f"{n:>5}: {ln}" for n, ln in enumerate(text.splitlines(), 1) if AUTH_SETTING.search(ln)]
            if hits:
                blocks.append(f"### {rel} (auth settings)\n" + "\n".join(hits[:40]))
        tree = _parse(path)
        for node in (tree.body if tree else []):
            if isinstance(node, ast.ClassDef) and "token" in node.name.lower() and not AUTH_FILE.search(rel):
                seg = ast.get_source_segment(text, node) or ""
                blocks.append(f"### {rel}: class {node.name}\n{_clip(seg, 2_000)}")
    out, used = [], 0
    for b in blocks:
        if used + len(b) > limit:
            continue
        out.append(b)
        used += len(b)
    return ("\nHow this app authenticates (a 401/403 means the seeded credentials don't match this; "
            "create tokens with the model shown here, not a library default):\n" + "\n\n".join(out) + "\n"
            ) if out else ""


# ---------------------------------------------------------------- framework versions

FRAMEWORKS = ("django", "djangorestframework", "flask", "fastapi", "starlette", "sqlalchemy", "pydantic",
              "flask-sqlalchemy", "werkzeug", "marshmallow", "celery", "huey")
# APIs gone in versions the model may not know well: (module, name) -> (package, major it was removed in, use)
REMOVED_APIS = {
    ("django.utils.timezone", "utc"): ("django", 5, "datetime.timezone.utc"),
    ("django.conf.urls", "url"): ("django", 4, "django.urls.re_path"),
    ("django.utils.encoding", "force_text"): ("django", 4, "django.utils.encoding.force_str"),
    ("django.utils.encoding", "smart_text"): ("django", 4, "django.utils.encoding.smart_str"),
    ("django.utils.translation", "ugettext"): ("django", 4, "django.utils.translation.gettext"),
    ("django.utils.translation", "ugettext_lazy"): ("django", 4, "django.utils.translation.gettext_lazy"),
    ("flask", "_app_ctx_stack"): ("flask", 3, "flask.g"),
    ("flask", "_request_ctx_stack"): ("flask", 3, "flask.request"),
    ("pydantic", "BaseSettings"): ("pydantic", 2, "pydantic_settings.BaseSettings"),
}


def requirement_of(repo: str | Path, package: str) -> str:
    return next((r for r in dependencies(repo)[0] if _req_name(r) == package), "")


def allows_major(spec: str, major: int) -> bool:
    """Can this requirement resolve to `major` or later? An upper bound like <5 says no."""
    upper = re.search(r"<\s*=?\s*(\d+)", spec)
    return not (upper and int(upper.group(1)) <= major)


def versions_note(repo: str | Path) -> str:
    pins = [r for r in dependencies(repo)[0] if _req_name(r) in FRAMEWORKS]
    if not pins:
        return ""
    gone = [f"{mod}.{name} (removed in {pkg} {major}; use {use})"
            for (mod, name), (pkg, major, use) in REMOVED_APIS.items()
            if use and requirement_of(repo, pkg) and allows_major(requirement_of(repo, pkg), major)]
    note = ("Framework versions this app requires: " + ", ".join(pins) + ". These may be newer than most "
            "code you have seen, so prefer the standard library where it does the job (datetime.timezone.utc, "
            "not a framework alias) and only use framework APIs that exist in these versions.")
    if gone:
        note += " In particular these no longer exist: " + "; ".join(gone) + "."
    return note + "\n\n"


def removed_api_uses(repo: str | Path, tree: ast.Module) -> list[str]:
    """Seed lines that use an API the app's framework version no longer has."""
    live = {k: v for k, v in REMOVED_APIS.items()
            if v[2] and requirement_of(repo, v[0]) and allows_major(requirement_of(repo, v[0]), v[1])}
    modules = {mod for mod, _ in live}
    aliases: dict[str, str] = {}  # local name -> module, for `from django.utils import timezone`
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                full = f"{node.module}.{a.name}"
                if full in modules:
                    aliases[a.asname or a.name] = full
                if (node.module, a.name) in live:
                    pkg, major, use = live[(node.module, a.name)]
                    problems.append(f"{SEED_FILE} line {node.lineno}: {node.module}.{a.name} was removed in "
                                    f"{pkg} {major}, which this app uses; use {use}")
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name in modules:
                    aliases[a.asname or a.name] = a.name
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            base = ast.unparse(node.value)
            mod = aliases.get(base, base)
            if (mod, node.attr) in live:
                pkg, major, use = live[(mod, node.attr)]
                problems.append(f"{SEED_FILE} line {node.lineno}: {mod}.{node.attr} was removed in {pkg} {major}, "
                                f"which this app uses; use {use}")
    return problems


def same_error(a: str, b: str) -> bool:
    """Two error lines say the same thing once temp paths, ports and ids are blanked out."""
    def norm(line: str) -> str:
        line = re.sub(r"(?:[A-Za-z]:)?[\\/][^\s'\":]*doppel-[^\s'\":]*", "<tmp>", line)
        return re.sub(r"\d+", "N", line).strip()
    return bool(a and b) and norm(a) == norm(b)


def headline(error: str) -> str:
    """One line for the attempt history. A failed command's cause is at the bottom of its output; a smoke
    or coverage failure says it in the first line (what follows is evidence: routes, scans, seed logs)."""
    core = error.split("\nThe login step sends:")[0].split("\nWhat the databases held")[0]
    first = core.splitlines()[0][:160] if core.strip() else "?"
    if core.startswith(("the login step", "the twin boots", "GET ", "POST ")):
        return first
    return f"{first} -> {last_line(core)}"


def last_line(error: str) -> str:
    lines = [ln.strip() for ln in error.strip().splitlines() if ln.strip()]
    return lines[-1][:300] if lines else ""


def draft(repo: str | Path, error: str = "", previous: dict | None = None, chat=nemotron_chat,
          model: str | None = None, history: list[str] | None = None, repeated: int = 0,
          best_stage: int = 0, on_exchange=None, facts: str = "") -> dict:
    """Ask Nemotron for the two files. Returns {"files": {name: text}, "notes": str}. `facts` is the
    probe's brief (doppel.probe.facts_text): ground truth from the running app."""
    model = model or os.getenv("INIT_MODEL", "nvidia/Nemotron-3-Ultra-550b-a55b")
    feedback = ""
    if error:
        shown = "\n".join(f"--- {k}\n{v}" for k, v in (previous or {}).items())
        earlier = ""
        if history:
            earlier = ("Earlier attempts, oldest first. Each changed something and still failed, so do "
                       "not go back to them:\n" + "\n".join(f"- {h}" for h in history) + "\n")
        repeat = (f"The last attempt failed with the SAME error as attempt {repeated}: the change made in "
                  "between did not reach the cause. Do not adjust the same thing again; read the source "
                  "below and take a different approach.\n") if repeated else ""
        feedback = FEEDBACK.format(kept=kept_note(best_stage), history=earlier, repeat=repeat, error=_clip_error(error),
                                   source=related_source(repo, error), files=shown)
    facts_block = (f"FACTS FROM THE RUNNING APP. Doppel installed this app in the sandbox and imported it the way its "
                   f"framework does; these are true. Where they disagree with your reading of the code below, they "
                   f"win (database location, route paths, what each view reads, the app's own commands):\n"
                   f"{facts}\n\n") if facts else ""
    prompt = PROMPT.format(feedback=feedback, facts=facts_block, install_hint=install_hint(repo),
                           versions=versions_note(repo),
                           hints=gather_hints(repo),
                           index=api_index(repo),
                           code=gather_code(repo))
    # not lower: at 0.2 Nemotron fell into a loop repeating env vars until it hit max_tokens
    text, _, _ = chat(model, prompt, temperature=0.6)
    if on_exchange:
        on_exchange(prompt, text)
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


TOKEN_TRAPS = re.compile(r"\bjwt\b|encode\(|hash|expir|signer|signing|itsdangerous|hmac", re.I)
# the route never read the credentials at all (DRF's NotAuthenticated, and the usual wordings elsewhere)
NOT_PROVIDED = re.compile(r"credentials were not provided|no credentials|not authenticated|missing (auth|token)"
                          r"|authorization header (is )?(missing|required)", re.I)
API_AUTH = re.compile(r"AUTHENTICATION_CLASSES|authentication_classes|Authentication\b|BasicAuth")


def basic_ignored_advice(repo: Path | None) -> str:
    """HTTP Basic sent, and the app answered as if nothing was sent: no auth class reads Basic here.

    linkding, 8 Oct: four attempts tweaked the password while the API only reads "Token <key>"
    (bookmarks.api.auth.LinkdingTokenAuthentication) and has no route that trades a password for a token."""
    classes = ""
    if repo:
        hits = []
        for path in _py_files(repo):
            rel = path.relative_to(repo).as_posix()
            if "test" in rel:
                continue
            for n, ln in enumerate(path.read_text("utf-8", "replace").splitlines(), 1):
                if API_AUTH.search(ln) and not ln.lstrip().startswith("#"):
                    hits.append(f"{rel}:{n}: {ln.strip()}")
        if hits:
            classes = "\nWhere this app sets up API authentication:\n" + "\n".join(hits[:20])
    return ("\nThe app answered as if NO credentials were sent: the route never read the HTTP Basic header, so the "
            "password is not the problem and changing it will not help. Use the scheme the app's authentication "
            "classes below actually read. If that is a token stored in the app's database and no route trades a "
            "password for one, seed a token with a FIXED key in the app's own token model and log in with only a "
            "header: [personas.login] with a cheap authenticated GET, header = { Authorization = \"<keyword> "
            "<the fixed key>\" } (the keyword the auth class accepts, such as Token or Bearer), and no auth, json "
            "or save." + classes + (auth_source(repo) if repo else ""))


def login_advice(spec: TwinSpec, repo: Path | None, body: str = "") -> str:
    """What to try instead, by the shape of the login that was refused (the same 401 three times in a
    row on microblog: the model kept sending a seeded token the app could never accept)."""
    login = spec.login or {}
    if login.get("auth") and NOT_PROVIDED.search(body or ""):
        return basic_ignored_advice(repo)
    issuing = login_routes(repo) if repo else ""
    where = f"\nRoutes that look like they issue credentials:\n{issuing}" if issuing else ""
    if not login.get("auth") and not login.get("json") and not login.get("save"):
        traps = ""
        if repo:
            models = re.findall(r"class (\w*[Tt]oken\w*)", api_index(repo))
            src = "\n".join(_definition(repo, m, lines=60) for m in models)
            if TOKEN_TRAPS.search(src):
                traps = (" This app's token model signs, hashes or expires its tokens (see jwt/hash/expiration "
                         "in it), so a value written by the seed is never accepted as-is.")
        return ("\nYour login sends a fixed token from the seed." + traps + " If the app has a route that "
                "issues tokens or logs in, use it instead: [personas.login] with that route's method and path, "
                "auth = [\"<a seeded username>\", \"<its password>\"] (HTTP Basic) or json = {...}, "
                "save = { token = \"<field in its answer>\" }, "
                "and header = { Authorization = \"Bearer {token}\" } (or the scheme the auth code expects)." + where)
    return ("\nThe app refused the seeded credentials. Check, in this order: that the seed and the server use the "
            "SAME database (set its location once in [app] env, not only for the seed); that the seed sets the "
            "password through the app's own hashing (a set_password method or a password property); that the "
            "login sends the field the auth code looks users up by (username or email); and that this route is "
            "the one that accepts them." + (database_config(repo) if repo else "") + where)


USERISH = re.compile(r"(^|_)(users?|accounts?|members?|auth_user|profiles?)$", re.I)


def login_sent(login: dict | None) -> str:
    """What the login step actually sends, in words."""
    if not login:
        return ""
    how = (f"HTTP Basic as user {login['auth'][0]!r}" if login.get("auth")
           else f"a JSON body with {', '.join(sorted(login['json']))}" if isinstance(login.get("json"), dict)
           else f"an HTML form with {', '.join(sorted(login['form']))}" if isinstance(login.get("form"), dict)
           else "only a header" if login.get("header") else "no credentials")
    return f"\nThe login step sends: {login.get('method', 'POST')} {login.get('path')} with {how}."


def _login_identity(login: dict | None) -> str:
    if not login:
        return ""
    if login.get("auth"):
        return str(login["auth"][0])
    body = login.get("json") if isinstance(login.get("json"), dict) else \
        login.get("form") if isinstance(login.get("form"), dict) else {}
    return str(next((v for k, v in body.items() if k.lower() in ("username", "email", "login", "user")), ""))


def seed_evidence(seed_log: str, login: dict | None = None, ignored: bool = False) -> str:
    """What the seed printed and what the databases held right after it: facts, not guesses, for the
    next draft (microblog: five refused logins in a row before anyone looked inside)."""
    if not seed_log.strip():
        return ""
    scans = [ln for ln in seed_log.splitlines() if ln.startswith("[dbscan]")]
    printed = [ln for ln in seed_log.splitlines() if ln.strip() and not ln.startswith("[dbscan]")][-15:]
    verdict = ""
    user_counts = re.findall(r"(\S+?)=(\d+)", " ".join(ln.split(": ", 1)[-1] for ln in scans))
    users = [(t, int(n)) for t, n in user_counts if USERISH.search(t)]
    samples = [ln for ln in scans if ln.startswith("[dbscan]   ")]
    who = _login_identity(login)
    known = re.findall(r"(?:username|user_name|login|email|name)=(\S+)", " ".join(samples))
    if any("PLAIN TEXT" in ln for ln in samples):
        verdict = ("\nPasswords are stored as PLAIN TEXT, so a login that hashes and compares can never match. "
                   "Set them through the app's own hashing (a password property, set_password, create_user).")
    elif who and known and who not in known:
        verdict = (f"\nThe login uses {who!r}, but the seeded users are: {', '.join(dict.fromkeys(known))}. "
                   "Log in as one of them, or seed that user.")
    elif who and who in known and ignored:
        verdict = (f"\nUser {who!r} exists and its password is hashed: the account is fine. The login failed "
                   "because the route never read the credentials (see above), not because of the password.")
    elif who and who in known:
        verdict = (f"\nUser {who!r} exists and its password is hashed, so the account is fine: the PASSWORD the "
                   "seed hashed differs from the one the login sends, or the login sends it in a form this route "
                   "does not read (HTTP Basic vs a JSON body). Make both use the same literal password.")
    elif users and all(n == 0 for _, n in users):
        verdict = ("\nEvery user table is EMPTY after the seed: nothing it created was saved. Commit the session "
                   "(db.session.commit(), `with db.begin()`, transaction.atomic exits), and make sure the seed "
                   "uses the app's real config, not a test or in-memory one.")
    elif len([ln for ln in scans if re.search(r"(^|[ ,])\w*users?=", ln)]) > 1:
        verdict = ("\nMore than one database has a user table: check which one the server opens (its config "
                   "shown above) and make the seed write exactly that one, through [app] env.")
    out = "\nWhat the databases held right after the seed:\n" + "\n".join(scans) if scans else ""
    if printed:
        out += "\nWhat the seed printed (last lines):\n" + "\n".join(printed)
    return verdict + out


MISSING_TABLE = re.compile(r"no such table|relation \"?\w+\"? does not exist|Table '[\w.]+' doesn't exist", re.I)


def smoke_problem(recording: dict, spec: TwinSpec, repo: Path | None = None) -> str:
    """Why the smoke run is not good enough, or "" if it is."""
    steps = recording.get("steps", [])
    for s in steps:
        if s["status"] == 0 or s["status"] >= 500:
            log = recording.get("app_log", "")
            told = (f"\nThe app's own log (its last lines) says why:\n{log[-3000:]}" if log
                    else "\n(the app's log was empty: it printed no traceback)")
            if MISSING_TABLE.search(log):
                told += ("\nA table is missing although the seed creates the schema: the server opened a DIFFERENT "
                         "database file than the one the seed filled (compare the path in the scan below with where "
                         "the app's config puts it, e.g. Flask's instance folder sits next to the package, not "
                         "inside it). Point both at the same file, or let the seed use the app's own factory/config."
                         + (database_config(repo) if repo else ""))
            return f"{s['request']} answered {s['status'] or 'nothing'} (the app crashed).{told}"
    from doppel.rehearsal import login_step_count
    n = login_step_count(spec.login) if spec.login else 0
    if spec.login and len(steps) >= n:
        s = steps[n - 1]
        if s["status"] >= 400:
            advice = login_advice(spec, repo, str(s.get("body"))) if s["status"] in (401, 403) else ""
            if s["status"] == 400 and not spec.login.get("form") and repo and \
                    route_reads_form(repo, str(spec.login.get("path", ""))):
                advice = ("\nThis login view reads an HTML form, so a JSON body or HTTP Basic arrives as missing "
                          "fields (Flask answers 400 Bad Request). "
                          "Send form = { <field names the view reads> } instead.")
            return (f"the login step {s['request']} was refused with {s['status']}: "
                    f"{str(s.get('body'))[:500]}. The seeded account or the login settings are wrong.{advice}")
        if spec.login.get("form") and still_on_login_form(s.get("body")):
            shown = " | ".join((s["body"]["html"].get("text") or [])[:12])
            return (f"the login form {s['request']} came back instead of logging in (status {s['status']}), so the "
                    f"app rejected the credentials. The page said: {shown[:500]}. Check the field names the view "
                    "reads and that the seed hashed the same password the login sends.")
    return ""


def still_on_login_form(body) -> bool:
    """An HTML page that still offers a form with a password field: the login did not happen."""
    page = body.get("html") if isinstance(body, dict) else None
    return bool(page) and any(any("pass" in name.lower() for name in (f.get("fields") or {}))
                              for f in page.get("forms") or [])


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


ANSWERED = (400, 409, 422)  # the app read the request and said why it refused: validation working


def coverage(recordings: list[dict]) -> float:
    """Share of requests the app really handled: successes, plus validation answers (400, 409, 422),
    which careless users are *meant* to get. Counting those as failures pushed init toward users who
    never send bad input, and bad input is how the twin found the shop's quantity-0 bug."""
    steps = [s for r in recordings for s in r["steps"]]
    return sum(1 for s in steps if 0 < s["status"] < 400 or s["status"] in ANSWERED) / len(steps) if steps else 0.0


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


def _infrastructure(e: Exception) -> bool:
    """A network or sandbox-API failure says nothing about the drafted setup, so redrafting can't fix it."""
    import subprocess

    from doppel.backends.nebius import _transient
    return _transient(e) and not isinstance(e, RuntimeError | subprocess.TimeoutExpired)


def _run_twin(backend: str):
    if backend == "local":
        from doppel.backends.local import run_twin
    else:
        from doppel.backends.nebius import run_twin
    return run_twin


MIN_COVERAGE = 0.4  # below this the twin boots but tests almost nothing: a setup that "works" in name only


def refusal_summary(recordings: list[dict], examples: int = 6) -> str:
    """Status counts and a few refused requests with the app's answer, for the model to act on."""
    from collections import Counter
    steps = [s for r in recordings for s in r["steps"]]
    counts = Counter(s["status"] for s in steps)
    bad = [s for s in steps if not 0 < s["status"] < 400][:examples]
    lines = [f"status counts: {dict(sorted(counts.items()))}"]
    lines += [f"- {s['request']} -> {s['status']}: {_clip(str(s.get('body')), 200)}" for s in bad]
    return "\n".join(lines)


def try_files(repo: Path, files: dict, backend: str = "nebius", run_twin=None, chat=nemotron_chat,
              personas: int = 6, min_coverage: float = MIN_COVERAGE, calibrate: bool = True) -> dict:
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
            return {"ok": False, "error": f"{type(e).__name__}: {e}", "infra": _infrastructure(e)}
        seen = {"seed_log": smoke.get("seed_log", ""),
                "smoke_steps": [{"request": s["request"], "status": s["status"]}
                                for r in smoke.get("base", []) for s in r["steps"]]}
        if smoke.get("failed"):
            return {"ok": False, "error": smoke["failed"][0]["error"], **seen}
        problem = smoke_problem(smoke["base"][0], spec, work) if smoke["base"] else "the smoke persona never ran"
        if problem:
            ignored = bool((spec.login or {}).get("auth")) and bool(NOT_PROVIDED.search(problem))
            crash = "(the app crashed)" in problem  # a crash is not a wrong password: no login verdict
            return {"ok": False, "error": problem + login_sent(spec.login)
                    + seed_evidence(smoke.get("seed_log", ""), None if crash else spec.login, ignored), **seen}
        if not calibrate:
            return {"ok": True, "error": "", "files": files, "noise": [], "flaky": [], "coverage": None,
                    "personas": 0, "repaired": [], "calibrated": False, **seen}
        # calibrate: invented users against the same code twice; what differs is noise
        from doppel.personas import generate
        from doppel.rehearsal import repair
        users = generate(work, n=personas, guide=spec.persona_guide, chat=chat)["personas"]
        twice = run_twin(work, work, spec, users)
        # rehearse, like real runs do: users that never logged in get the seeded login (no model call),
        # other stuck ones go back to Nemotron once; then replay the repaired set twice
        fixed = repair(users, twice["base"], work, guide=spec.persona_guide, login=spec.login, chat=chat)
        if fixed["repaired"]:
            twice = run_twin(work, work, spec, fixed["personas"])
    served = coverage(twice["base"])
    seen["users"] = [{"persona": r["persona"], "served": f"{coverage([r]):.0%}"} for r in twice["base"]]
    if served < min_coverage:
        return {"ok": False, "files": files, "coverage": served, **seen,
                "error": (f"the twin boots, but the invented users got only {served:.0%} of their requests "
                          f"served (need {min_coverage:.0%}), even after giving them the seeded login. "
                          "Usually the login, the token in the seed, or the [personas] guide is wrong, or "
                          "the seed lacks the records users ask for. What the app answered:\n"
                          + refusal_summary(twice["base"]))}
    noise = noisy_keys(twice["base"], twice["head"])
    files = {**files, SPEC_FILE: with_ignore(files[SPEC_FILE], noise)}
    return {"ok": True, "error": "", "files": files, "noise": noise,
            "flaky": flaky_statuses(twice["base"], twice["head"]),
            "coverage": served, "personas": len(twice["base"]), "repaired": fixed["repaired"], **seen}


# ---------------------------------------------------------------- the loop


TRACE_DIR = ".doppel-trace"


def probe_brief(repo: Path, backend: str, prober=None, log=lambda m: None) -> str:
    """Install and import the app once, before any draft, and return the facts for the prompt. A probe
    that cannot run at all leaves init where it was before: drafting from the code alone."""
    from doppel import probe as probing
    log("[init] probing the app: installing it and importing it the way its framework does")
    try:
        facts = (prober or probing.probe)(repo, backend=backend, install=derived_install(repo))
    except Exception as e:  # noqa: BLE001 - the probe is a bonus, never a blocker
        log(f"[init] the probe could not run ({type(e).__name__}: {str(e)[:160]}); drafting from the code alone")
        return ""
    fw = facts.get("framework") or "unknown"
    db = facts.get("database") or {}
    log(f"[init] probe: {fw}, {len(facts.get('routes') or [])} routes, {len(facts.get('models') or [])} models"
        + (f", database {probing.relative_paths(json.dumps(db, default=str))[:120]}" if db else "")
        + (f", {len(facts['errors'])} import problem(s)" if facts.get("errors") else "")
        + (f"; {facts['python_mismatch']}" if facts.get("python_mismatch") else ""))
    return probing.facts_text(facts)


class Tracer:
    """`doppel init --trace`: every step of every attempt as a file you can open and read.

        attempt-1/1-prompt.txt     what Nemotron was asked
                  2-reply.txt      what it wrote back (and the two files it drafted)
                  3-checks.txt     each pre-boot check and what it found
                  4-boot.txt       what the twin did: smoke requests, statuses, the outcome
                  5-evidence.txt   the seed's output and the database scan
                  6-calibration.txt  invented users, coverage, noisy keys (when it works)
        summary.txt                one line per attempt: how far it got and what init decided

    Recording only: a traced run decides exactly what an untraced one would."""

    def __init__(self, root: Path | None):
        self.root = root
        self.attempt = 0
        if root:
            if root.exists():
                shutil.rmtree(root, ignore_errors=True)
            root.mkdir(parents=True)

    def start(self, attempt: int) -> None:
        self.attempt = attempt
        if self.root:
            (self.root / f"attempt-{attempt}").mkdir(exist_ok=True)

    def write_root(self, name: str, text: str) -> None:
        if self.root:
            self.root.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text(text, encoding="utf-8")

    def write(self, name: str, text: str) -> None:
        if self.root:
            (self.root / f"attempt-{self.attempt}" / name).write_text(text, encoding="utf-8")

    def note(self, line: str) -> None:
        if self.root:
            with open(self.root / "summary.txt", "a", encoding="utf-8") as f:
                f.write(line + "\n")


def check_seed_requests(spec_text: str, files: dict) -> list[str]:
    """doppel_seed.json: present when doppel.toml names it, valid JSON, steps the runner can send."""
    try:
        wants = bool(tomllib.loads(spec_text).get("app", {}).get("seed_requests"))
    except tomllib.TOMLDecodeError:
        return []
    text = files.get(SEED_REQUESTS_FILE)
    if wants and text is None:
        return [f"doppel.toml sets seed_requests but no {SEED_REQUESTS_FILE} was returned"]
    if text is None:
        return []
    from doppel.spec import load_seed_steps
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / SEED_REQUESTS_FILE).write_text(text, encoding="utf-8")
        try:
            steps = load_seed_steps(Path(tmp), SEED_REQUESTS_FILE)
        except ValueError as e:
            return [str(e)]
    problems = []
    for i, step in enumerate(steps, 1):
        if str(step.get("method", "GET")).upper() not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            problems.append(f"{SEED_REQUESTS_FILE} step {i}: unknown method {step.get('method')!r}")
        if "form" in step and not isinstance(step["form"], dict):
            problems.append(f"{SEED_REQUESTS_FILE} step {i}: \"form\" must be an object of field -> value")
    if not wants:
        problems.append(f"{SEED_REQUESTS_FILE} was returned but doppel.toml has no seed_requests line, so it "
                        f"would never run: add seed_requests = \"{SEED_REQUESTS_FILE}\" under [app]")
    return problems


def run_checks(repo: Path, files: dict) -> list[tuple[str, list[str]]]:
    """Every pre-boot check by name, with what each found (an empty list means it passed)."""
    spec, seed = files.get(SPEC_FILE, ""), files.get(SEED_FILE, "")
    return [("check_spec: install, paths, login shape", check_spec(repo, spec)),
            ("check_seed_requests: the front-door seed", check_seed_requests(spec, files)),
            ("check_env: seed and server share settings", check_env(repo, spec, seed)),
            ("check_login_matches_seed: same password",
             check_login_matches_seed(spec, seed, files.get(SEED_REQUESTS_FILE, ""))),
            ("check_seed: names, fields, commits, hashes", check_seed(repo, seed) if seed else [])]


def checks_report(results: list[tuple[str, list[str]]]) -> str:
    lines = []
    for name, found in results:
        lines.append(f"{'PASS' if not found else 'FAIL'}  {name}")
        lines += [f"      - {p}" for p in found]
    return "\n".join(lines) + "\n"


def boot_report(result: dict) -> str:
    lines = [f"outcome: {'works' if result.get('ok') else 'failed'}"]
    for s in result.get("smoke_steps", []):
        lines.append(f"  smoke: {s['request']} -> {s['status']}")
    if result.get("error"):
        lines.append("\nerror sent back to the model:\n" + result["error"])
    return "\n".join(lines) + "\n"


STATE_FILE = ".doppel-init.json"  # progress kept between runs, removed once a setup works


def load_state(repo: Path) -> dict:
    try:
        state = json.loads((repo / STATE_FILE).read_text("utf-8"))
        return state if isinstance(state.get("best"), dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(repo: Path, best: dict, history: list[str]) -> None:
    try:
        (repo / STATE_FILE).write_text(json.dumps({"best": best, "history": history[-12:]}, indent=1), "utf-8")
    except OSError:
        pass  # progress is a convenience; never fail init over it


def calibration_report(result: dict) -> str:
    lines = [f"requests served: {result['coverage']:.0%} (the bar is {MIN_COVERAGE:.0%})"]
    lines += [f"  user {u['persona']}: {u['served']} served" for u in result.get("users", [])]
    lines.append(f"given the seeded login: {', '.join(result.get('repaired') or []) or 'nobody needed it'}")
    lines.append(f"noisy keys (differed between two identical runs): {', '.join(result['noise']) or 'none'}")
    lines.append(f"requests that changed status between identical runs: {', '.join(result['flaky']) or 'none'}")
    return "\n".join(lines) + "\n"


def check(repo: str | Path, backend: str = "nebius", calibrate: bool = False, run_twin=None,
          chat=nemotron_chat, log=lambda msg: print(msg, file=sys.stderr)) -> dict:
    """`doppel init --check`: test the doppel.toml and doppel_seed.py already in the repo, with no model
    drafting. For a human finishing the last step, and for seeing what each safety net catches."""
    repo = Path(repo).resolve()
    files = {n: (repo / n).read_text("utf-8") for n in (SPEC_FILE, SEED_FILE) if (repo / n).is_file()}
    if SPEC_FILE not in files:
        return {"ok": False, "error": f"no {SPEC_FILE} in {repo}", "checks": []}
    checked = run_checks(repo, files)
    if any(found for _, found in checked):
        return {"ok": False, "error": "checks failed before booting", "checks": checked}
    log(f"[check] all checks passed; booting the twin ({backend})")
    result = try_files(repo, files, backend=backend, run_twin=run_twin, chat=chat, calibrate=calibrate)
    return {**result, "checks": checked}


def init(repo: str | Path, backend: str = "nebius", tries: int = 2, chat=nemotron_chat, run_twin=None,
         personas: int = 6, log=lambda msg: print(msg, file=sys.stderr),
         min_coverage: float = MIN_COVERAGE, resume: bool = True, trace: Path | None = None,
         prober=None, agent: bool = True, workspace=None) -> dict:
    """Draft, verify, repair, calibrate. Returns the last attempt with "attempts" and "notes" added;
    "files" holds the calibrated files when "ok" is true. Only writes its progress file (STATE_FILE).

    Never moves backwards: each new draft starts from the attempt that got furthest (install, seed,
    start, login, users), with the parts it proved told to stay as they are. Static checks of the
    spec and the seed run before any sandbox boots. Progress carries over to the next run."""
    repo = Path(repo).resolve()
    tracer = Tracer(Path(trace) if trace else None)
    state = load_state(repo) if resume else {}
    best = state.get("best") or {"stage": 0, "files": None, "error": ""}
    history: list[str] = list(state.get("history") or [])
    brief = probe_brief(repo, backend, prober, log)
    tracer.write_root("0-probe.txt", brief or "(the probe learned nothing; drafting from the code alone)\n")
    error, previous = best["error"], best["files"]
    if previous:
        log(f"[init] resuming from the last run: got past {', '.join(STAGES[:best['stage']]) or 'nothing'} "
            f"(delete {STATE_FILE} or pass --fresh to start over)")
    result: dict = {"ok": False, "error": "no attempt made"}
    attempt, drafts, free = 0, 0, tries  # a draft caught by the pre-boot checks costs no sandbox: redraft free
    while attempt < tries:
        attempt += 1
        drafts += 1
        log(f"[init] attempt {attempt}/{tries}: asking Nemotron for {SPEC_FILE} and {SEED_FILE}")
        tracer.start(drafts)

        def exchange(prompt: str, text: str) -> None:
            tracer.write("1-prompt.txt", prompt)
            tracer.write("2-reply.txt", text)
        try:
            ends = [re.sub(r"^attempt \d+: (reached \w+: )?", "", h) for h in history[:-1]]
            repeated = next((i + 1 for i, e in enumerate(ends) if error and same_error(e, headline(error))), 0)
            try:
                drafted = draft(repo, error, previous, chat=chat, history=history[:-1], repeated=repeated,
                                best_stage=best["stage"], on_exchange=exchange, facts=brief)
            except ValueError as cut_off:  # a reply cut off or without the files: ask once more, no boot spent
                log(f"[init] the reply could not be used ({str(cut_off)[:80]}...); asking again")
                drafted = draft(repo, error, previous, chat=chat, history=history[:-1], repeated=repeated,
                                best_stage=best["stage"], on_exchange=exchange, facts=brief)
        except Exception as e:  # noqa: BLE001
            error, result = f"the reply could not be used: {e}", {"ok": False, "error": str(e), "attempts": attempt}
            log(f"[init] {error}")
            history.append(f"attempt {drafts}: unusable reply -> {str(e)[:160]}")
            tracer.note(history[-1])
            error, previous = (best["error"], best["files"]) if best["files"] else (error, previous)
            continue
        files = drafted["files"]
        for name, text in files.items():
            tracer.write(f"2-drafted-{name}", text)
        checked = run_checks(repo, files)
        tracer.write("3-checks.txt", checks_report(checked))
        problems = [p for _, found in checked for p in found]
        if problems:
            error = ("The files were checked against the repo before booting and would fail:\n"
                     + "\n".join(f"- {p}" for p in problems))
            previous = files
            result = {"ok": False, "error": error, "attempts": attempt, "notes": drafted["notes"], "files": files}
            log(f"[init] checks found {len(problems)} problem(s), no sandbox needed: {problems[0][:160]}")
            history.append(f"attempt {drafts}: checked before booting -> {problems[0][:200]}")
            tracer.note(history[-1] + "  [no sandbox used]")
            if free > 0:
                free -= 1
                attempt -= 1
                log(f"[init] redrafting without spending an attempt ({free} free redraft(s) left)")
            continue
        log(f"[init] booting the twin ({backend}) to check it")
        result = try_files(repo, files, backend=backend, run_twin=run_twin, chat=chat, personas=personas,
                           min_coverage=min_coverage)
        result.update(attempts=attempt, notes=drafted["notes"])
        result.setdefault("files", files)
        tracer.write("4-boot.txt", boot_report(result))
        if result.get("seed_log"):
            tracer.write("5-evidence.txt", result["seed_log"])
        if result["ok"]:
            log(f"[init] works: {result['personas']} users replayed, {result['coverage']:.0%} of requests "
                f"served, ignoring {len(result['noise'])} noisy key(s)")
            (repo / STATE_FILE).unlink(missing_ok=True)
            tracer.write("6-calibration.txt", calibration_report(result))
            tracer.note(f"attempt {drafts}: WORKS, {result['coverage']:.0%} served, noise {result['noise']}")
            return result
        error = result["error"]
        log(f"[init] failed: {error.splitlines()[0][:200] if error else 'unknown error'}")
        for line in (ln for ln in error.splitlines() if ln.startswith("[dbscan]")):
            log(f"[init]   inside: {line[len('[dbscan] '):]}")
        if result.get("infra"):
            log("[init] that is a network or sandbox problem, not the setup: trying the same files once more")
            result = try_files(repo, files, backend=backend, run_twin=run_twin, chat=chat, personas=personas,
                               min_coverage=min_coverage)
            result.update(attempts=attempt, notes=drafted["notes"])
            result.setdefault("files", files)
            tracer.write("4-boot.txt", boot_report(result))
            if result["ok"]:
                log(f"[init] works: {result['personas']} users replayed, {result['coverage']:.0%} of requests "
                    f"served, ignoring {len(result['noise'])} noisy key(s)")
                (repo / STATE_FILE).unlink(missing_ok=True)
                tracer.write("6-calibration.txt", calibration_report(result))
                tracer.note(f"attempt {drafts}: WORKS after a network retry, {result['coverage']:.0%} served")
                return result
            error = result["error"]
            if result.get("infra"):
                log("[init] the network failed again: stopping instead of redrafting (the setup was never tried)")
                tracer.note(f"attempt {drafts}: network failed twice, stopped")
                return result
            log(f"[init] failed: {error.splitlines()[0][:200] if error else 'unknown error'}")
        stage = stage_reached(error)
        history.append(f"attempt {drafts}: reached {STAGES[max(stage, 0)]}: {headline(error)}")
        if stage >= best["stage"]:
            best = {"stage": stage, "files": files, "error": error}
            previous = files
            tracer.note(history[-1] + "  [kept: the next draft starts from this one]")
        else:  # went backwards: continue from the attempt that got furthest, not from this one
            tracer.note(history[-1] + f"  [dropped: went backwards, keeping the attempt that reached "
                                      f"{STAGES[best['stage']]}]")
            log(f"[init] that went backwards (failed at {STAGES[stage]}); continuing from the best attempt, "
                f"which got past {', '.join(STAGES[:best['stage']])}")
            error, previous = best["error"], best["files"]
        save_state(repo, best, history)
    if agent and not result.get("ok") and not result.get("infra"):
        result = hand_to_agent(repo, backend, brief, history, best, result, attempt, chat, run_twin, personas,
                               min_coverage, log, tracer, workspace)
    return result


def hand_to_agent(repo, backend, brief, history, best, result, attempt, chat, run_twin, personas, min_coverage,
                  log, tracer, workspace) -> dict:
    """The drafts failed: let the setup agent (doppel.agent) work it out in a live sandbox."""
    from doppel import agent as setup_agent
    log("[init] the drafts did not work: handing over to the setup agent, which works in a live sandbox")

    def try_setup(files: dict) -> dict:
        problems = [p for _, found in run_checks(repo, files) for p in found]
        if problems:
            return {"ok": False, "error": "checked before booting (no sandbox used):\n- " + "\n- ".join(problems)}
        r = try_files(repo, files, backend=backend, run_twin=run_twin, chat=chat, personas=personas,
                      min_coverage=min_coverage)
        r.setdefault("files", files)
        return r
    try:
        handed = setup_agent.fix(repo, backend, brief, history, best.get("files"), best.get("error") or
                                 result.get("error", ""), try_setup, chat, log=log, tracer=tracer, workspace=workspace)
    except Exception as e:  # noqa: BLE001 - the agent is a second chance; its failure leaves the drafts' result
        log(f"[init] the setup agent could not run: {type(e).__name__}: {str(e)[:200]}")
        return {**result, "agent_error": f"{type(e).__name__}: {e}"}
    handed.update(attempts=attempt, agent=True)
    handed.setdefault("notes", result.get("notes", ""))
    if handed.get("ok"):
        (repo / STATE_FILE).unlink(missing_ok=True)
        tracer.write_root("6-calibration.txt", calibration_report(handed))
        tracer.note(f"agent: WORKS after {handed.get('agent_steps')} step(s), {handed['coverage']:.0%} served")
        log(f"[init] works: {handed['personas']} users replayed, {handed['coverage']:.0%} of requests served, "
            f"ignoring {len(handed['noise'])} noisy key(s)")
        return handed
    tracer.note(f"agent: did not get it working in {handed.get('agent_steps')} step(s)")
    return {**result, **{k: v for k, v in handed.items() if k != "ok"}, "ok": False}


def write(repo: str | Path, files: dict, force: bool = False) -> list[Path]:
    repo = Path(repo)
    clash = [repo / n for n in files if (repo / n).exists()]
    if clash and not force:
        raise FileExistsError(f"{', '.join(p.name for p in clash)} already exist; use --force to overwrite")
    for name, text in files.items():
        (repo / name).write_text(text, encoding="utf-8")
    return [repo / n for n in files]


# ---------------------------------------------------------------- the repo's real API, for the seed
#
# A big repo doesn't fit in the prompt, so the model used to write seed code against functions it had
# never seen (linkding: create_bookmark(url=...) when the real signature is (bookmark, tag_string,
# current_user)). Two fixes: every signature always goes into the prompt as a compact index, and the
# drafted seed is checked against those signatures before a sandbox is booted (milliseconds, not minutes).

MAX_INDEX = 40_000
QUERY_CREATORS = {"create", "get_or_create", "update_or_create"}


def _py_files(repo: Path) -> list[Path]:
    return sorted(p for p in repo.rglob("*.py")
                  if p.is_file() and not (SKIP_CODE & set(p.relative_to(repo).parts)))


def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text("utf-8", "replace"))
    except (SyntaxError, ValueError):
        return None


def _signature(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    return f"{fn.name}({ast.unparse(fn.args)})"


def _fields(cls: ast.ClassDef, source: str) -> list[str]:
    """Class-level `name = Something(...)` lines: model fields in Django, SQLAlchemy, Pydantic..."""
    out = []
    for node in cls.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                and isinstance(node.value, ast.Call):
            out.append(_clip(ast.get_source_segment(source, node) or node.targets[0].id, 110).splitlines()[0])
    return out


def api_index(repo: str | Path, limit: int = MAX_INDEX) -> str:
    """Every top-level function and class in the repo, one line each, plus model fields."""
    repo = Path(repo).resolve()
    lines = []
    for path in _py_files(repo):
        tree = _parse(path)
        if tree is None:
            continue
        source = path.read_text("utf-8", "replace")
        entries = []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and not node.name.startswith("_"):
                entries.append(f"  def {_signature(node)}")
            elif isinstance(node, ast.ClassDef):
                bases = ", ".join(ast.unparse(b) for b in node.bases)
                entries.append(f"  class {node.name}({bases})")
                entries += [f"    {f}" for f in _fields(node, source)]
        if entries:
            lines.append(f"{'.'.join(path.relative_to(repo).with_suffix('').parts)}:")
            lines += entries
    return _clip("\n".join(lines), limit)


def _module_file(repo: Path, dotted: str) -> Path | None:
    base = repo.joinpath(*dotted.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _defines(tree: ast.Module) -> dict[str, ast.AST]:
    names: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names[node.name] = node
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names[t.id] = node
        elif isinstance(node, ast.Import | ast.ImportFrom):
            for a in node.names:
                names[(a.asname or a.name).split(".")[0]] = node
    return names


def _class_attrs(cls: ast.ClassDef) -> set[str]:
    """Names a model constructor accepts: `x = Field(...)` (Django, SQLAlchemy 1), annotated
    `x: Mapped[int] = mapped_column()` or bare `x: int` (SQLAlchemy 2, Pydantic, dataclasses), and
    properties with setters (`password` in microblog hashes on assignment)."""
    names = set()
    for n in cls.body:
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call):
            names |= {t.id for t in n.targets if isinstance(t, ast.Name)}
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and not n.target.id.startswith("__"):
            names.add(n.target.id)
        elif isinstance(n, ast.FunctionDef) and any(
                ast.unparse(d) == "property" or ast.unparse(d).endswith(".setter") for d in n.decorator_list):
            names.add(n.name)
    return names


def _model_fields(repo: Path, cls: ast.ClassDef, module: Path, depth: int = 0) -> set[str] | None:
    """Field names a model accepts as keywords, or None when we can't tell (unknown base class, or a
    class whose fields we can't see): an unsure checker must stay quiet, not block a good seed."""
    if depth > 5:
        return None
    fields = _class_attrs(cls)
    defs = _defines(_parse(module) or ast.Module(body=[], type_ignores=[]))
    for base in cls.bases:
        name = ast.unparse(base)
        if name.endswith("models.Model") or name in ("Model", "db.Model", "Base", "SQLModel", "BaseModel"):
            continue
        parent = defs.get(name)
        if not isinstance(parent, ast.ClassDef):
            return None  # inherits fields from somewhere we don't read
        inherited = _model_fields(repo, parent, module, depth + 1)
        if inherited is None:
            return None
        fields |= inherited
    if depth == 0 and not fields:
        return None
    return fields | {f + "_id" for f in fields} | {"id", "pk"}


STRINGY = ("CharField", "TextField", "SlugField", "EmailField", "URLField", "FilePathField",
           "FileField", "ImageField", "GenericIPAddressField")
NO_COLUMN = ("ManyToManyField", "AutoField", "BigAutoField", "SmallAutoField", "GeneratedField")


def required_django_fields(cls: ast.ClassDef) -> list[str]:
    """Django fields that become NOT NULL columns with no default: creating a row without them fails."""
    out = []
    for node in cls.body:
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and isinstance(node.targets[0], ast.Name)):
            continue
        kind = ast.unparse(node.value.func).split(".")[-1]
        if not (kind.endswith("Field") or kind in ("ForeignKey", "OneToOneField")) \
                or kind.endswith(STRINGY) or kind in NO_COLUMN:
            continue
        kws = {k.arg: k.value for k in node.value.keywords if k.arg}
        if any(k in kws for k in ("default", "auto_now", "auto_now_add", "primary_key", "db_default")):
            continue
        if isinstance(kws.get("null"), ast.Constant) and kws["null"].value is True:
            continue
        out.append(node.targets[0].id)
    return out


def _check_call(call: ast.Call, fn: ast.FunctionDef | ast.AsyncFunctionDef, shown: str) -> list[str]:
    a = fn.args
    positional = [p.arg for p in a.posonlyargs + a.args]
    named = set(positional[len(a.posonlyargs):]) | {p.arg for p in a.kwonlyargs}
    problems = []
    if len(call.args) > len(positional) and not a.vararg and not any(isinstance(x, ast.Starred) for x in call.args):
        problems.append(f"{shown}() takes {len(positional)} positional argument(s), the seed passes {len(call.args)}")
    given = set(positional[:len(call.args)])
    for kw in call.keywords:
        if kw.arg is None:
            return problems  # **kwargs at the call site: can't check further
        if kw.arg not in named and not a.kwarg:
            problems.append(f"{shown}() has no parameter '{kw.arg}'")
        given.add(kw.arg)
    defaults = len(a.defaults)
    required = positional[:len(positional) - defaults] if defaults else positional
    required += [k.arg for k, d in zip(a.kwonlyargs, a.kw_defaults, strict=True) if d is None]
    missing = [r for r in required if r not in given and r not in ("self", "cls")]
    if missing and not any(isinstance(x, ast.Starred) for x in call.args):
        problems.append(f"{shown}() is missing required argument(s): {', '.join(missing)}")
    return problems


def _assignments_named(repo: Path, name: str) -> str:
    """Module-level `name = Something(...)` in the repo, as importable lines: the objects behind a name."""
    out = []
    for path in _py_files(repo):
        tree = _parse(path)
        for node in (tree.body if tree else []):
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                module = ".".join(path.relative_to(repo).with_suffix("").parts).removesuffix(".__init__")
                out.append(f"`from {module} import {name}` ({ast.unparse(node)[:60]})")
    return " or ".join(out[:3])


def check_seed(repo: str | Path, seed: str) -> list[str]:
    """Problems that would crash the seed, found by reading it against the repo's real code."""
    repo = Path(repo).resolve()
    try:
        tree = ast.parse(seed)
    except SyntaxError as e:
        return [f"{SEED_FILE} line {e.lineno}: syntax error: {e.msg}"]
    problems: list[str] = removed_api_uses(repo, tree)
    if "<repo>" in seed:
        problems.append(f"{SEED_FILE} contains '<repo>': a placeholder, not a path. The seed runs in the repo root, "
                        "so use paths relative to it")
    if "<repo>" in seed:
        problems.append(f"{SEED_FILE} contains '<repo>': that is a placeholder, not a path. The seed runs in the "
                        "repo root, so use paths relative to it")
    if re.search(r"\.add(_all)?\(", seed) and re.search(r"session", seed, re.I) \
            and not re.search(r"\.commit\(|\.begin\(|atomic", seed):
        problems.append(f"{SEED_FILE} adds rows to a session but never commits, so nothing is saved and every "
                        "login will be refused; call session.commit() (or use `with db.begin():`)")
    for node in ast.walk(tree):
        plain = None
        if isinstance(node, ast.Call):
            plain = next((k for k in node.keywords if k.arg and "hash" in k.arg.lower()
                          and isinstance(k.value, ast.Constant) and isinstance(k.value.value, str)), None)
            name = plain.arg if plain else ""
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Attribute) \
                and "hash" in node.targets[0].attr.lower() and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            plain, name = node, node.targets[0].attr
        if plain is not None:
            problems.append(f"{SEED_FILE} line {node.lineno}: writes a plain string into {name}; a login hashes "
                            "the password it gets and compares, so this can never match. Set the password "
                            "through the app's own hashing (password=..., set_password(...), create_user(...))")
    functions: dict[str, tuple[ast.AST, str, Path]] = {}  # local name -> (def or class, shown name, file)
    modules: dict[str, Path] = {}                          # local alias -> repo module file
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            mod_file = _module_file(repo, node.module)
            if mod_file is None:
                continue  # a library, not the repo
            mod_tree = _parse(mod_file)
            defined = _defines(mod_tree) if mod_tree else {}
            for alias in node.names:
                local = alias.asname or alias.name
                sub = _module_file(repo, f"{node.module}.{alias.name}")
                if sub is not None:
                    modules[local] = sub
                elif alias.name not in defined and alias.name != "*":
                    problems.append(f"{SEED_FILE} line {node.lineno}: {node.module} has no '{alias.name}'")
                elif isinstance(defined.get(alias.name), ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                    functions[local] = (defined[alias.name], alias.name, mod_file)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                mod_file = _module_file(repo, alias.name)
                if mod_file is not None and alias.asname:
                    modules[alias.asname] = mod_file

    # a name imported from the repo that is a function, used as if it were an object (microblog: the seed
    # took `db` from microblog.py, where it is a CLI command group, instead of `db = Alchemical()` in api/app.py)
    callable_attrs = {"__name__", "__doc__", "__wrapped__", "command", "group", "cli", "name", "delay", "s"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in functions:
            found, shown, mod_file = functions[node.value.id]
            if isinstance(found, ast.FunctionDef | ast.AsyncFunctionDef) and node.attr not in callable_attrs:
                deco = ", ".join("@" + ast.unparse(d) for d in found.decorator_list) or "no decorator"
                others = _assignments_named(repo, found.name)
                hint = f"; the object you want is probably {others}" if others else ""
                where = mod_file.relative_to(repo).as_posix()
                problems.append(f"{SEED_FILE} line {node.lineno}: `{shown}` from {where} is a function ({deco}), "
                                f"so it has no `.{node.attr}`{hint}")

    def target(func: ast.expr):
        if isinstance(func, ast.Name) and func.id in functions:
            return functions[func.id]
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in modules:
            mod_file = modules[func.value.id]
            node = _defines(_parse(mod_file) or ast.Module(body=[], type_ignores=[])).get(func.attr)
            if node is None:
                problems.append(f"{SEED_FILE} line {func.lineno}: {func.value.id} has no '{func.attr}'")
                return None
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                return node, f"{func.value.id}.{func.attr}", mod_file
        return None

    for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
        hit = target(call.func)
        if hit and isinstance(hit[0], ast.FunctionDef | ast.AsyncFunctionDef):
            problems += [f"{SEED_FILE} line {call.lineno}: {p}; the real signature is {_signature(hit[0])}"
                         for p in _check_call(call, hit[0], hit[1])]
            continue
        # Model(...) and Model.objects.create / get_or_create / update_or_create(...)
        cls_hit, func = hit, call.func
        if cls_hit is None and isinstance(func, ast.Attribute) and func.attr in QUERY_CREATORS \
                and isinstance(func.value, ast.Attribute) and isinstance(func.value.value, ast.Name | ast.Attribute):
            cls_hit = target(func.value.value)
        if not cls_hit or not isinstance(cls_hit[0], ast.ClassDef):
            continue
        fields = _model_fields(repo, cls_hit[0], cls_hit[2])
        if fields is None:
            continue
        given = {kw.arg for kw in call.keywords if kw.arg}
        creating = not isinstance(func, ast.Attribute) or func.attr == "create"
        if creating and None not in [kw.arg for kw in call.keywords] and not call.args:
            missing = [f for f in required_django_fields(cls_hit[0]) if f not in given and f + "_id" not in given]
            if missing:
                problems.append(f"{SEED_FILE} line {call.lineno}: creating {cls_hit[1]} without "
                                f"{', '.join(missing)}, which the database requires (NOT NULL, no default)")
        for kw in call.keywords:
            if kw.arg and "__" not in kw.arg and kw.arg != "defaults" and kw.arg not in fields:
                known = ", ".join(sorted(f for f in fields if not f.endswith("_id") and f not in ("id", "pk")))
                problems.append(f"{SEED_FILE} line {call.lineno}: {cls_hit[1]} has no field '{kw.arg}' "
                                f"(its fields: {known})")
    return problems
