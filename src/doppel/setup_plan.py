"""How to install and run a project's own tests, from what the project says about itself.

A SetupPlan holds everything a sandbox needs, each value with where it came from: the test command, install
steps, system packages, environment, the folder to run in, and the services the project's CI starts.

Where the test command comes from, most trusted first:
  1. doppel.toml [tests]
  2. tox.ini [testenv]
  3. the CI job that runs the tests: the workflow YAML is parsed and ONE job is chosen (the one whose steps run
     the tests, not lint or docs). Its install steps, env, apt packages, working directory and services belong
     together; installs from other jobs never mix in (redash got black and ruff from its lint job, nothing else)
  4. a test script, the Makefile test target, a noxfile, manage.py, a tests folder

The install always follows the test command's source. When that source installs only tools, or nothing, the
project's own dependencies come first: requirements files, pyproject dependencies and test extras, PEP 735
dependency groups (exactly, through uv.lock, or with pip --group), Poetry, Pipfile.

Screening ten real apps (11 Oct 2026) found every gap this replaces: lint-job installs (mealie, redash,
label-studio), CI env and apt packages ignored (wger, healthchecks), dependency groups unread (flaskbb), a
Makefile recipe cut at its line continuation (CTFd).
"""
from __future__ import annotations

import configparser
import re
import shlex
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEV_REQUIREMENTS = ("requirements-dev.txt", "requirements_dev.txt", "requirements-test.txt", "requirements_test.txt",
                    "test-requirements.txt", "dev-requirements.txt", "requirements/dev.txt", "requirements/test.txt",
                    "requirements/testing.txt")
TEST_LINE = re.compile(r"(?:^|\s|&&|;)((?:python3?\s+-m\s+)?pytest\b[^\n&;|]*|python3?\s+manage\.py\s+test\b[^\n&;|]*|"
                       r"python3?\s+-m\s+unittest\b[^\n&;|]*)")
TEST_SCRIPTS = ("scripts/test.sh", "scripts/tests.sh", "test.sh", "run_tests.sh", "scripts/run_tests.sh", "bin/test")
TEST_GROUPS = ("test", "tests", "testing", "dev")
TOOLING = {"black", "ruff", "flake8", "mypy", "pre-commit", "isort", "pylint", "bandit", "coveralls", "codecov",
           "wheel", "setuptools", "pip", "build", "twine", "tox", "nox", "coverage", "pytest-cov", "codespell",
           "pyright", "types-requests", "pip-tools", "uv", "poetry", "pipenv", "hatch", "pdm"}
SKIP_WORDS = ("lint", "format", "style", "docs", "doc ", "typecheck", "type-check", "mypy", "pre-commit", "precommit",
              "release", "deploy", "publish", "codeql", "security", "e2e", "playwright", "frontend", "docker",
              "label", "stale", "bump", "translation", "i18n", "smokeshow", "zizmor", "issue", "conflict")
RUNNERS = ({"uv", "run"}, {"poetry", "run"}, {"pdm", "run"}, {"hatch", "run"}, {"pipenv", "run"}, {"rye", "run"})
VALUED_FLAGS = {"--group", "--extra", "--with", "--python", "-p", "--env-file", "--directory", "--project",
                "--package", "--only-group", "--source", "--rcfile", "--include", "--omit", "--concurrency",
                "--data-file", "--context", "--parallel-mode"}
SHELL_STOPS = {"&&", "||", ";", "|"}
SERVICE_NAMES = ("postgres", "postgis", "mysql", "mariadb", "redis", "valkey", "mongo", "rabbitmq", "elasticsearch",
                 "opensearch", "memcached", "minio", "kafka", "mailpit", "mailhog")
APT = "DEBIAN_FRONTEND=noninteractive apt-get"
REQUIREMENTS_OUT = "/tmp/doppel-requirements.txt"


@dataclass
class SetupPlan:
    test: str = ""
    install: list[str] = field(default_factory=list)
    system: list[str] = field(default_factory=list)      # apt packages
    env: dict[str, str] = field(default_factory=dict)
    workdir: str = ""                                     # relative to the repo root; "" = the root
    services: list[str] = field(default_factory=list)    # what the project's CI starts (Doppel doesn't, yet)
    source: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def install_shell(self) -> str:
        steps = ([apt_step(self.system)] if self.system else []) + self.install
        return " && ".join(steps)

    def to_dict(self) -> dict:
        return asdict(self)


def apt_step(packages: list[str]) -> str:
    """One package at a time: a name Ubuntu's runner has and Debian lacks must not sink the rest."""
    names = " ".join(shlex.quote(p) for p in packages)
    return (f"({APT} update -qq >/dev/null 2>&1; for p in {names}; do {APT} install -y -qq --no-install-recommends "
            f"\"$p\" >/dev/null 2>&1 || echo \"[doppel] system package not available: $p\"; done)")


# ---------------------------------------------------------------- the test command, wherever it is written

def clean_test_command(cmd: str) -> str:
    """The project's test command, minus what would make worlds differ or slow them for nothing: coverage,
    parallel workers (their split order varies), reports written to files."""
    parts = cmd.strip().split()
    out, skip_next = [], False
    for i, p in enumerate(parts):
        if skip_next:
            skip_next = False
            continue
        if p.startswith(("--cov", "--junitxml", "--html", "--numprocesses", "--dist", "--durations", "--parallel=")):
            continue
        if p in ("-n", "--maxprocesses"):
            skip_next = True
            continue
        if p == "--parallel":   # Django's runner: `--parallel`, `--parallel auto`, `--parallel 4`
            nxt = parts[i + 1] if i + 1 < len(parts) else ""
            skip_next = nxt == "auto" or nxt.isdigit()
            continue
        out.append(p)
    text = " ".join(out)
    return re.sub(r"^pytest\b", "python -m pytest", text)


def _skip_flags(words: list[str], i: int) -> int:
    while i < len(words) and words[i].startswith("-"):
        i += 2 if words[i] in VALUED_FLAGS and "=" not in words[i] else 1
    return i


def normalize_test(segment: str) -> str:
    """A CI line that runs the tests, as a plain command for the project's environment, or "".
    `uv run coverage run --source='.' ./manage.py test --parallel auto` -> `python manage.py test --parallel auto`
    (cleaned later); `poetry run pytest -q` -> `python -m pytest -q`; `coverage run -m pytest` -> pytest."""
    try:
        words = shlex.split(segment)
    except ValueError:
        words = segment.split()
    i = 0
    env = []
    while i < len(words) and re.match(r"^[A-Za-z_]\w*=", words[i]):
        env.append(words[i])
        i += 1
    moved = True
    while moved:
        moved = False
        if set(words[i:i + 2]) in RUNNERS and len(words[i:i + 2]) == 2:
            i, moved = _skip_flags(words, i + 2), True
        if words[i:i + 2] == ["coverage", "run"]:
            i, moved = _skip_flags(words, i + 2), True
        elif words[i:i + 4] in (["python", "-m", "coverage", "run"], ["python3", "-m", "coverage", "run"]):
            i, moved = _skip_flags(words, i + 4), True
    rest = words[i:]
    for n, w in enumerate(rest):
        if w in SHELL_STOPS or w.startswith((">", "2>", "1>")):
            rest = rest[:n]
            break
    py = ("python", "python3")
    if rest[:1] in (["pytest"], ["py.test"]):
        cmd = ["python", "-m", "pytest", *rest[1:]]
    elif rest[:2] == ["-m", "pytest"]:
        cmd = ["python", "-m", "pytest", *rest[2:]]
    elif len(rest) >= 3 and rest[0] in py and rest[1:3] == ["-m", "pytest"]:
        cmd = ["python", "-m", "pytest", *rest[3:]]
    elif rest[:2] in (["manage.py", "test"], ["./manage.py", "test"]):
        cmd = ["python", "manage.py", "test", *rest[2:]]
    elif len(rest) >= 3 and rest[0] in py and rest[1] in ("manage.py", "./manage.py") and rest[2] == "test":
        cmd = ["python", "manage.py", "test", *rest[3:]]
    elif rest[:2] == ["-m", "unittest"] or (len(rest) >= 3 and rest[0] in py and rest[1:3] == ["-m", "unittest"]):
        cmd = ["python", "-m", "unittest", *rest[(2 if rest[0] == "-m" else 3):]]
    else:
        return ""
    return " ".join(shlex.quote(w) for w in env + cmd)


def from_tox(repo: Path) -> tuple[str, list[str], bool]:
    f = repo / "tox.ini"
    if not f.exists():
        return "", [], False
    cfg = configparser.ConfigParser(interpolation=None)
    try:
        cfg.read(f, encoding="utf-8")
    except configparser.Error:
        return "", [], False
    sec = "testenv" if cfg.has_section("testenv") else next((s for s in cfg.sections() if s.startswith("testenv")), "")
    if not sec:
        return "", [], False
    commands = [c.strip() for c in cfg.get(sec, "commands", fallback="").splitlines() if c.strip()]
    test = next((m.group(1) for c in commands if (m := TEST_LINE.search(" " + c))), "")
    deps = [d.strip() for d in cfg.get(sec, "deps", fallback="").splitlines() if d.strip()]
    skip_install = cfg.get(sec, "skip_install", fallback="false").strip().lower() == "true"
    return test, deps, skip_install


def _joined_lines(text: str) -> list[str]:
    """Lines with shell continuations joined: CTFd's Makefile test recipe was cut at its trailing backslash."""
    out, cur = [], ""
    for line in text.splitlines():
        if line.rstrip().endswith("\\"):
            cur += line.rstrip()[:-1] + " "
            continue
        out.append(cur + line)
        cur = ""
    if cur:
        out.append(cur)
    return out


def from_makefile(repo: Path) -> str:
    """The pytest/manage.py/unittest line in a Makefile's test target (`make` itself may not exist in a slim
    image, so the recipe line is run directly)."""
    f = next((repo / n for n in ("Makefile", "makefile", "GNUmakefile") if (repo / n).exists()), None)
    if f is None:
        return ""
    target = None
    for line in _joined_lines(f.read_text("utf-8", errors="replace")):
        m = re.match(r"^([A-Za-z0-9_.-]+)\s*:(?!=)", line)
        if m:
            target = m.group(1)
            continue
        if target in ("test", "tests", "check", "unittest", "pytest") and line.startswith(("\t", "    ")):
            body = " ".join(line.strip().lstrip("@-").split())
            hit = TEST_LINE.search(" " + body)
            if hit:
                return hit.group(1).strip()
    return ""


def from_script(repo: Path) -> str:
    """A test script's environment settings and its test line (`export PYTHONPATH=./docs_src` then pytest),
    without the lint, format or coverage steps around them."""
    for name in TEST_SCRIPTS:
        f = repo / name
        if not f.is_file():
            continue
        exports, test = [], ""
        for line in _joined_lines(f.read_text("utf-8", errors="replace")):
            line = line.strip()
            m = re.match(r"^export\s+([A-Za-z_][A-Za-z0-9_]*=\S+)\s*$", line)
            if m and not test:
                exports.append(m.group(1))
            elif not test and (hit := TEST_LINE.search(" " + line)):
                test = re.sub(r"\s*(\$\{?@\}?|\$\*|\$\{?\d\}?)\s*", " ", hit.group(1)).strip()
        if test:
            return " ".join(f"export {e};" for e in exports) + (" " if exports else "") + test
    return ""


def from_noxfile(repo: Path) -> str:
    """session.run("pytest", "-q", ...) in a nox session named like tests."""
    f = repo / "noxfile.py"
    if not f.exists():
        return ""
    text = f.read_text("utf-8", errors="replace")
    m = re.search(r"session\.run\(\s*['\"](pytest|python)['\"]((?:\s*,\s*['\"][^'\"]*['\"])*)", text)
    if not m:
        return ""
    args = re.findall(r"['\"]([^'\"]*)['\"]", m.group(2))
    cmd = " ".join([m.group(1), *args])
    return cmd if TEST_LINE.search(" " + cmd) else ""


def uses_pytest(repo: Path) -> bool:
    if (repo / "pytest.ini").exists() or (repo / "conftest.py").exists():
        return True
    for name in ("pyproject.toml", "setup.cfg", "tox.ini"):
        f = repo / name
        if f.exists() and re.search(r"\[(tool[.:])?pytest", f.read_text("utf-8", "replace")):
            return True
    return False


def has_tests(repo: Path) -> bool:
    return any(p for p in repo.rglob("test*.py") if not {".venv", "venv", "node_modules", ".tox"} & set(p.parts))


# ---------------------------------------------------------------- the project's own dependencies

def _pyproject(repo: Path) -> dict:
    f = repo / "pyproject.toml"
    if not f.exists():
        return {}
    try:
        return tomllib.loads(f.read_text("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        return {}


def installable(repo: Path) -> bool:
    """Is the project a package pip can build? A setup.cfg alone is often only tool settings (CTFd's is, and
    `pip install -e .` failed on its flat layout), so it counts only with a [metadata] section."""
    cfg = repo / "setup.cfg"
    return (repo / "setup.py").exists() or "build-system" in _pyproject(repo) or \
        (cfg.exists() and "[metadata]" in cfg.read_text("utf-8", errors="replace"))


SELF_INSTALL_NOTE = ("echo '[doppel] the project itself did not install as a package; its dependencies did, "
                     "and its tests run from its folder'")


def project_install(repo: Path, groups: list[str] | None = None, extras: list[str] | None = None,
                    all_groups: bool = False, all_extras: bool = False) -> list[str]:
    """Install steps for the project's own dependencies, test extras and dependency groups.
    groups/extras: what the CI asked for (`uv sync --group test`); None means the usual test names."""
    steps: list[str] = []
    reqs = (["-r requirements.txt"] if (repo / "requirements.txt").exists() else []) + \
        [f"-r {n}" for n in DEV_REQUIREMENTS if (repo / n).exists()]
    if reqs:
        steps.append("pip install --quiet " + " ".join(reqs))
    pp = _pyproject(repo)
    project = pp.get("project") or {}
    tool = pp.get("tool") or {}
    have_groups = pp.get("dependency-groups") or {}
    have_extras = project.get("optional-dependencies") or {}
    if all_groups:
        pick_groups = list(have_groups)
    elif groups is not None:
        pick_groups = [g for g in groups if g in have_groups]
    else:
        default = (tool.get("uv") or {}).get("default-groups")
        pick_groups = [g for g in TEST_GROUPS if g in have_groups] or \
            [g for g in (default if isinstance(default, list) else []) if g in have_groups]
    if all_extras:
        pick_extras = list(have_extras)
    elif extras is not None:
        pick_extras = [e for e in extras if e in have_extras]
    else:
        pick_extras = [e for e in TEST_GROUPS if e in have_extras]
    can_build = installable(repo)
    if project:
        if (repo / "uv.lock").exists():   # the exact versions the project tests with, its groups included
            flags = " ".join([f"--group {g}" for g in pick_groups] + [f"--extra {e}" for e in pick_extras])
            # uv sync honours the lock's own package indexes (paperless-ngx pins torch==...+cpu from PyTorch's
            # index, which pip cannot see); into the interpreter the tests use, keeping what is already there.
            # If it can't, the exported requirements go in with pip.
            export = f"uv export --frozen --no-hashes --no-emit-project {flags}".rstrip()
            sync = ("UV_PROJECT_ENVIRONMENT=\"$(python -c 'import sys; print(sys.prefix)')\" uv sync --quiet --frozen "
                    f"--inexact --no-install-project {flags}").rstrip()
            steps.append(f"pip install --quiet uv && ({sync} || ({export} -o {REQUIREMENTS_OUT} >/dev/null && "
                         f"pip install --quiet -r {REQUIREMENTS_OUT}))")
            if can_build:   # the dependencies are in already: the project as a package is a nicety
                steps.append(f"(pip install --quiet --no-deps -e . || {SELF_INSTALL_NOTE})")
        else:
            if can_build:   # if it won't build as a package, its dependencies still go in
                target = f"'.[{','.join(pick_extras)}]'" if pick_extras else "."
                flags = " ".join(f"--extra {e}" for e in pick_extras)
                steps.append(f"(pip install --quiet -e {target} || (pip install --quiet uv && uv pip install --quiet "
                             f'--python "$(command -v python)" -r pyproject.toml {flags}'.rstrip() +
                             f" && {SELF_INSTALL_NOTE}))")
            elif project.get("dependencies") or pick_extras:   # an app that isn't a package: just its dependencies
                flags = " ".join(f"--extra {e}" for e in pick_extras)
                steps.append(f'pip install --quiet uv && uv pip install --quiet --python "$(command -v python)" '
                             f"-r pyproject.toml {flags}".rstrip())
            if pick_groups:   # PEP 735: pip reads [dependency-groups] from 25.1
                steps.append("pip install --quiet --upgrade 'pip>=25.1' && pip install --quiet " +
                             " ".join(f"--group {g}" for g in pick_groups))
    elif tool.get("poetry"):
        have = (tool["poetry"].get("group") or {})
        with_groups = [g for g in (groups or TEST_GROUPS) if g in have]
        steps.append("pip install --quiet poetry && poetry config virtualenvs.create false && "
                     "poetry install --no-interaction" + (f" --with {','.join(with_groups)}" if with_groups else ""))
    elif (repo / "Pipfile").exists():
        steps.append("pip install --quiet pipenv && pipenv install --system --dev")
    elif can_build and not reqs:
        steps.append("pip install --quiet -e .")
    return steps


def covers_project(steps: list[str]) -> bool:
    """Do these install steps install the project or its requirements (not only tools)?"""
    text = " ".join(steps)
    return bool(re.search(r"(-r\s|-e\s|--requirement|requirements|\buv export\b|poetry install|pipenv install|"
                          r"\binstall\s+(--quiet\s+)?\.(\s|$|\[)|\s'?\.\[)", text))


def _uv_sync(words: list[str], repo: Path) -> list[str]:
    groups, extras, all_g, all_e, i = [], [], False, False, 0
    while i < len(words):
        w = words[i]
        if w in ("--group", "--extra") and i + 1 < len(words):
            (groups if w == "--group" else extras).append(words[i + 1])
            i += 2
            continue
        if w.startswith(("--group=", "--extra=")):
            (groups if w.startswith("--group") else extras).append(w.split("=", 1)[1])
        all_g |= w == "--all-groups"
        all_e |= w == "--all-extras"
        i += 1
    return project_install(repo, groups or None, extras or None, all_g, all_e)


def convert_install(segment: str, repo: Path) -> list[str] | None:
    """A CI install line as install steps for the sandbox, [] for a tools-only install, None if it isn't one."""
    seg = re.sub(r"^\s*sudo\s+(-H\s+)?", "", segment).strip()
    try:
        words = shlex.split(seg)
    except ValueError:
        words = seg.split()
    if not words or "${{" in seg:
        return None
    if words[:2] == ["uv", "sync"]:
        return _uv_sync(words[2:], repo)
    if words[:2] == ["poetry", "install"]:
        return ["pip install --quiet poetry && poetry config virtualenvs.create false && " +
                " ".join(["poetry", "install", "--no-interaction", *words[2:]])]
    if words[:2] == ["pipenv", "install"]:
        return ["pip install --quiet pipenv && pipenv install --system" + (" --dev" if "--dev" in words else "")]
    m = re.match(r"^(?:python3?\s+-m\s+)?pip3?\s+install\b|^uv\s+pip\s+install\b", seg)
    if not m:
        return None
    after = words.index("install") + 1 if "install" in words else len(words)
    args = [w for w in words[after:] if w not in ("--user", "--system", "--break-system-packages")]
    names = [re.split(r"[<>=!~\[;@ ]", a, maxsplit=1)[0].lower() for a in args if not a.startswith("-")]
    flags_with_files = any(a in ("-r", "-e", "-c", "--requirement", "--editable") or a.startswith(("-r", "-e"))
                           for a in args)
    if names and not flags_with_files and all(n in TOOLING for n in names):
        return []
    if not args:
        return []
    return ["pip install --quiet " + " ".join(shlex.quote(a) if " " in a else a for a in args)]


# ---------------------------------------------------------------- the CI job that runs the tests

def _sub(text: str, matrix: dict, env: dict) -> str:
    def one(m):
        expr = m.group(1).strip()
        if expr.startswith("matrix.") and expr[7:] in matrix:
            return str(matrix[expr[7:]])
        if expr.startswith("env.") and expr[4:] in env:
            return str(env[expr[4:]])
        return m.group(0)
    return re.sub(r"\$\{\{\s*([^}]*?)\s*\}\}", one, text)


def _pick_matrix(job: dict) -> dict:
    """One combination: sqlite when a database axis offers it, Linux, the newest Python, else the first value."""
    m = ((job.get("strategy") or {}).get("matrix")) or {}
    if not isinstance(m, dict):
        return {}
    chosen = {}
    for key, values in m.items():
        if key in ("include", "exclude") or not isinstance(values, list) or not values:
            continue
        texts = [str(v) for v in values]
        if any("sqlite" in t.lower() for t in texts):
            chosen[key] = next(v for v, t in zip(values, texts, strict=True) if "sqlite" in t.lower())
        elif key.lower() in ("os", "platform", "runs-on") or any("ubuntu" in t.lower() for t in texts):
            chosen[key] = next((v for v, t in zip(values, texts, strict=True) if "ubuntu" in t.lower()), values[0])
        elif "python" in key.lower():
            chosen[key] = values[-1]
        else:
            chosen[key] = values[0]
    return chosen


def _runs(step_if, matrix: dict) -> bool:
    """A step's `if:` under the chosen matrix: simple comparisons only; anything else runs."""
    cond = str(step_if or "").strip()
    cond = re.sub(r"^\$\{\{\s*|\s*\}\}$", "", cond)
    if not cond:
        return True
    m = re.fullmatch(r"(matrix\.[\w-]+|runner\.os)\s*(==|!=)\s*['\"]([^'\"]*)['\"]", cond)
    if not m:
        return True
    left = "Linux" if m.group(1) == "runner.os" else str(matrix.get(m.group(1)[7:], ""))
    if m.group(1) != "runner.os" and m.group(1)[7:] not in matrix:
        return True
    return (left == m.group(3)) == (m.group(2) == "==")


def _segments(run: str) -> list[str]:
    out = []
    for line in _joined_lines(run):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out += [s.strip() for s in re.split(r"\s&&\s|;\s", line) if s.strip()]
    return out


def _env_values(*maps) -> dict:
    env = {}
    for m in maps:
        for k, v in (m or {}).items() if isinstance(m, dict) else ():
            v = ("true" if v else "false") if isinstance(v, bool) else str(v)
            if "${{" in v or k.startswith("GITHUB_"):
                continue
            env[str(k)] = v
    return env


def _scan_job(job: dict, wf_env: dict, repo: Path) -> SetupPlan | None:
    matrix = _pick_matrix(job)
    base_env = _env_values({k: _sub(str(v), matrix, {}) for k, v in (wf_env or {}).items()},
                           {k: _sub(str(v), matrix, {}) for k, v in (job.get("env") or {}).items()})
    workdir = str((((job.get("defaults") or {}).get("run") or {}).get("working-directory")) or "")
    plan = SetupPlan(env=dict(base_env), workdir=workdir.strip("./") if workdir not in (".", "./") else "")
    installs: list[str] = []
    for service in (job.get("services") or {}):
        image = str(((job.get("services") or {}).get(service) or {}).get("image", service))
        plan.services.append(next((n for n in SERVICE_NAMES if n in image.lower()), str(service)))
    for step in job.get("steps") or []:
        if not isinstance(step, dict) or not _runs(step.get("if"), matrix):
            continue
        uses = str(step.get("uses") or "")
        if "setup-python" in uses:
            v = _sub(str((step.get("with") or {}).get("python-version", "")), matrix, base_env)
            if v and "${{" not in v:
                plan.notes.append(f"CI uses Python {v}")
        run = step.get("run")
        if not isinstance(run, str):
            continue
        run = _sub(run, matrix, base_env)
        for seg in _segments(run):
            low = seg.lower()
            apt = re.match(r"^(?:sudo\s+)?apt(?:-get)?\s+install\s+(.*)$", seg)
            if apt:
                plan.system += [w for w in apt.group(1).split() if not w.startswith("-") and "${{" not in w]
                continue
            if re.search(r"docker[ -]compose\b.*\bup\b|systemctl\s+start\s|service\s+\S+\s+start", low):
                plan.services += [n for n in SERVICE_NAMES if n in low] or [f"a service the CI starts ({seg[:60]})"]
                continue
            if not plan.test and (test := normalize_test(seg)):
                plan.test = test
                plan.env.update(_env_values({k: _sub(str(v), matrix, base_env)
                                             for k, v in (step.get("env") or {}).items()}))
                wd = str(step.get("working-directory") or "")
                if wd and wd not in (".", "./"):
                    plan.workdir = wd.strip("./")
                break
            if (steps := convert_install(seg, repo / plan.workdir if plan.workdir else repo)) is not None:
                installs += steps
        if plan.test:
            break
    if not plan.test:
        return None
    plan.install = list(dict.fromkeys(installs))
    plan.system = list(dict.fromkeys(plan.system))
    plan.services = list(dict.fromkeys(plan.services))
    return plan


def _score(names: str) -> int:
    low = names.lower()
    return 3 * ("test" in low) - 8 * sum(w in low for w in SKIP_WORDS) - ("coverage" in low)


def ci_plan(repo: Path) -> SetupPlan | None:
    """The CI job that runs the tests, as a plan; None when no workflow job runs them."""
    folder = repo / ".github" / "workflows"
    if not folder.is_dir():
        return None
    try:
        import yaml
    except ImportError:   # pyyaml is a dependency; without it, no CI plan rather than a guess
        return None
    best: tuple[int, SetupPlan] | None = None
    for f in sorted(folder.glob("*.y*ml")):
        try:
            doc = yaml.safe_load(f.read_text("utf-8", errors="replace"))
        except yaml.YAMLError:
            continue
        if not isinstance(doc, dict):
            continue
        for job_id, job in (doc.get("jobs") or {}).items():
            if not isinstance(job, dict) or "uses" in job:
                continue
            plan = _scan_job(job, doc.get("env") or {}, repo)
            if plan is None:
                continue
            score = 10 + _score(f"{f.stem} {doc.get('name', '')} {job_id} {job.get('name', '')}")
            plan.source = {"test": f"CI workflow ({f.name}, job {job_id})"}
            if best is None or score > best[0]:
                best = (score, plan)
    return best[1] if best else None


# ---------------------------------------------------------------- the plan

def _has_manifest(repo: Path) -> bool:
    return (repo / "requirements.txt").exists() or (repo / "setup.py").exists() or \
        bool(_pyproject(repo).get("project")) or (repo / "Pipfile").exists()


def _only_suite_below(repo: Path) -> str:
    from doppel.traffic import suites_below
    below = suites_below(repo)
    return below[0] if len(below) == 1 else ""


def plan(repo: str | Path) -> SetupPlan:
    repo = Path(repo)
    spec = repo / "doppel.toml"
    if spec.exists():
        try:
            table = tomllib.loads(spec.read_text("utf-8")).get("tests", {})
        except tomllib.TOMLDecodeError:
            table = {}
        if table.get("command"):
            install = [table["install"]] if table.get("install") else project_install(repo)
            return SetupPlan(test=table["command"], install=install,
                             source={"test": "doppel.toml [tests]",
                                     "install": "doppel.toml [tests]" if table.get("install") else "the project's "
                                     "dependencies"})
    tox_test, tox_deps, tox_skip = from_tox(repo)
    ci = ci_plan(repo)
    if tox_test:
        p = SetupPlan(test=tox_test, source={"test": "tox.ini"})
        if tox_deps:
            p.install = ["pip install --quiet " + " ".join(d if d.startswith(("-r", "-e")) else f"'{d}'"
                                                           for d in tox_deps)]
            if installable(repo) and not tox_skip and not any(d.startswith("-e") for d in tox_deps):
                p.install.append("pip install --quiet -e .")
            p.source["install"] = "tox.ini deps"
        else:
            p.install, p.source["install"] = project_install(repo), "the project's dependencies"
    elif ci and "tox" not in ci.test:
        p = ci
        root = repo / p.workdir if p.workdir else repo
        if covers_project(p.install):
            p.source["install"] = "the same CI job"
        else:
            own = project_install(root)
            p.notes += ["the CI job installs only tools: the project's own dependencies first"] if p.install else []
            p.install = own + p.install
            p.source["install"] = "the project's dependencies" + (" + the same CI job" if ci.install else "")
    else:
        layout = [(from_script, "test script"), (from_makefile, "Makefile"), (from_noxfile, "noxfile.py")]
        test, source = next(((t, s) for fn, s in layout if (t := fn(repo))), ("", ""))
        if not test and (repo / "manage.py").exists() and not uses_pytest(repo):
            test, source = "python manage.py test", "manage.py (Django's test runner)"
        elif not test and has_tests(repo):
            test, source = "python -m pytest", "tests found in the repo"
        p = SetupPlan(test=test, install=project_install(repo),
                      source={"test": source, "install": "the project's dependencies"})
        sub = _only_suite_below(repo) if source in ("", "tests found in the repo") and not _has_manifest(repo) else ""
        if sub:   # the app lives in one subfolder (backend/): run there, with the whole repo present (its ../.env)
            inner = plan(repo / sub)
            if inner.test:
                inner.workdir = "/".join(x for x in (sub, inner.workdir) if x)
                inner.notes.append(f"the tests and the project's manifest are in {sub}/: run there")
                p = inner
    if ci and p is not ci:   # how the project's tests expect to run, even when the command came from elsewhere
        p.env = {**ci.env, **p.env}
        p.system = list(dict.fromkeys(p.system + ci.system))
        p.services = list(dict.fromkeys(p.services + ci.services))
        if ci.env or ci.system:
            p.source["env"] = ci.source.get("test", "CI workflow")
    if p.test and "pytest" in p.test and "pytest" not in " ".join(p.install):
        p.install.append("pip install --quiet pytest")
    p.install = list(dict.fromkeys(s for s in p.install if s))
    return p
