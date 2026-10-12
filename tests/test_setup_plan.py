"""The setup layer: one plan from what the project says about itself. One test per gap the ten-app screening
found (11 Oct 2026), on small made-up repos shaped like the real ones."""
import textwrap
from pathlib import Path

from doppel import setup_plan
from doppel.setup_plan import convert_install, normalize_test, plan
from doppel.traffic import check, detect
from tests.test_traffic import FLASK_APP, flask_repo


def write(root: Path, files: dict) -> Path:
    for rel, text in files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(textwrap.dedent(text), encoding="utf-8")
    return root


PYPROJECT_GROUPS = """\
    [project]
    name = "app"
    version = "1"
    dependencies = ["flask"]
    [dependency-groups]
    dev = ["pytest", "responses"]
    lint = ["ruff"]
    [build-system]
    requires = ["setuptools"]
    build-backend = "setuptools.build_meta"
"""


def test_a_lint_jobs_installs_never_become_the_apps_install(tmp_path):
    # redash, mealie: the only pip lines in CI were black and ruff; the app's own dependencies were never installed
    write(tmp_path, {".github/workflows/lint.yml": """\
        jobs:
          lint:
            steps:
              - run: python -m pip install black==23.1.0 ruff==0.0.287
              - run: ruff check .
        """, "pyproject.toml": PYPROJECT_GROUPS, "uv.lock": "", "tests/test_a.py": "def test_a(): pass\n"})
    p = plan(tmp_path)
    text = " ".join(p.install)
    assert "black" not in text and "ruff" not in text
    assert "uv export --frozen --no-hashes --no-emit-project --group dev" in text      # the exact, locked versions
    assert p.source["test"] == "tests found in the repo"


def test_the_ci_test_jobs_env_system_packages_and_installs_belong_together(tmp_path):
    # wger: DJANGO_SETTINGS_MODULE came from the CI job; healthchecks: apt packages the build needs
    write(tmp_path, {".github/workflows/ci.yml": """\
        env:
          DJANGO_MEDIA_ROOT: /tmp/media
        jobs:
          ci-job:
            runs-on: ubuntu-latest
            env:
              DJANGO_SETTINGS_MODULE: settings.ci
              TOKEN: ${{ secrets.TOKEN }}
            steps:
              - uses: actions/setup-python@v5
                with:
                  python-version: "3.13"
              - run: |
                  sudo apt-get update -qq
                  sudo apt-get install -y libcurl4-openssl-dev libssl-dev
              - run: uv sync --group test
              - run: uv run coverage run --source='.' ./manage.py test --parallel auto
        """, "pyproject.toml": PYPROJECT_GROUPS.replace("dev = ", "test = "), "uv.lock": "", "manage.py": ""})
    found = detect(tmp_path)
    p = found["plan"]
    assert found["test"] == "python manage.py test"                        # coverage, uv run, --parallel gone
    assert p["env"] == {"DJANGO_MEDIA_ROOT": "/tmp/media", "DJANGO_SETTINGS_MODULE": "settings.ci"}   # no secret
    assert p["system"] == ["libcurl4-openssl-dev", "libssl-dev"]
    assert "--group test" in found["install"] and "apt-get" in found["install"]
    assert "CI uses Python 3.13" in p["notes"] and "ci.yml, job ci-job" in p["source"]["test"]


def test_one_matrix_combination_and_its_steps_only(tmp_path):
    # healthchecks: a db axis (sqlite, postgres, mysql) with steps that only run for one database
    write(tmp_path, {".github/workflows/tests.yml": """\
        jobs:
          test:
            strategy:
              matrix:
                python-version: ["3.12", "3.13"]
                db: [postgres, sqlite, mysql]
            services:
              redis:
                image: redis:7
            steps:
              - if: matrix.db == 'postgres'
                run: sudo systemctl start postgresql.service
              - run: pip install -r requirements.txt
              - env:
                  DB: ${{ matrix.db }}
                run: python manage.py test
        """, "requirements.txt": "django\n"})
    p = plan(tmp_path)
    assert p.env == {"DB": "sqlite"} and p.services == ["redis"]          # postgres step skipped: sqlite chosen
    assert p.install == ["pip install --quiet -r requirements.txt"]


def test_dependency_groups_without_a_lock_file_use_pip_group(tmp_path):
    # flaskbb: its test dependencies (responses) live in [dependency-groups], which nothing read
    write(tmp_path, {"pyproject.toml": PYPROJECT_GROUPS, "Makefile": "test:\n\tpytest\n"})
    p = plan(tmp_path)
    assert any(s.startswith("(pip install --quiet -e . ||") for s in p.install)        # deps still go in if not
    assert any("'pip>=25.1'" in s and "--group dev" in s and "lint" not in s for s in p.install)


def test_a_makefile_recipe_continued_over_lines_is_read_whole(tmp_path):
    # CTFd: "python -m pytest -rf \" was all that was read, and the shell failed on the dangling backslash
    (tmp_path / "Makefile").write_text("test:\n\tpytest -rf --cov=CTFd \\\n\t\t--ignore=node_modules/ \\\n"
                                       "\t\t-W ignore::DeprecationWarning\n", encoding="utf-8")
    assert detect(tmp_path)["test"] == "python -m pytest -rf --ignore=node_modules/ -W ignore::DeprecationWarning"


def test_tools_only_ci_installs_come_after_the_projects_own_dependencies(tmp_path):
    write(tmp_path, {".github/workflows/t.yml": """\
        jobs:
          tests:
            steps:
              - run: pip install --upgrade pip && pip install uv pre-commit
              - run: pip install pytest-django
              - run: pytest -q
        """, "requirements.txt": "django\n"})
    p = plan(tmp_path)
    assert p.install == ["pip install --quiet -r requirements.txt", "pip install --quiet pytest-django"]
    assert p.source["install"] == "the project's dependencies + the same CI job"


def test_an_app_in_one_subfolder_runs_there_with_the_whole_repo(tmp_path):
    # full-stack-fastapi-template: backend/ read ../.env; uploading only backend/ lost it
    write(tmp_path, {".env": "SECRET_KEY=x\n", "backend/pyproject.toml": PYPROJECT_GROUPS,
                     "backend/tests/test_a.py": "def test_a(): pass\n", "backend/conftest.py": ""})
    p = plan(tmp_path)
    assert p.workdir == "backend" and p.test == "python -m pytest" and any("pip install --quiet -e ." in s
                                                                          for s in p.install)


def test_the_tests_run_in_the_planned_subfolder(tmp_path):
    for side, app in (("base", FLASK_APP), ("head", FLASK_APP.replace('"name": u["name"],',
                                                                      '"name": u["name"], "secret": u["secret"],'))):
        flask_repo(tmp_path / side / "backend", app)
        (tmp_path / side / "README.md").write_text("an app in backend/\n")
    r = check(tmp_path / "base", tmp_path / "head", backend="local")
    assert r["detected"]["plan"]["workdir"] == "backend" and r["stats"]["passed"] > 0
    assert any("secret" in f.detail for f in r["findings"])


def test_ci_lines_that_run_the_tests_become_plain_commands():
    assert normalize_test("uv run pytest -q tests") == "python -m pytest -q tests"
    assert normalize_test("poetry run python -m pytest") == "python -m pytest"
    assert normalize_test("coverage run -m pytest --maxfail=1") == "python -m pytest --maxfail=1"
    assert normalize_test("uv run --frozen --group test pytest") == "python -m pytest"
    assert normalize_test("python3 ./manage.py test app --keepdb") == "python manage.py test app --keepdb"
    assert normalize_test("pytest tests 2>&1 | tee out.txt") == "python -m pytest tests"
    assert normalize_test("ruff check .") == "" and normalize_test("tox -e py312") == ""
    assert normalize_test("DJANGO_ENV=test pytest") == "DJANGO_ENV=test python -m pytest"


def test_install_lines_are_kept_converted_or_recognised_as_tools(tmp_path):
    assert convert_install("python -m pip install --upgrade pip", tmp_path) == []
    assert convert_install("sudo pip install --user -r requirements.txt", tmp_path) == \
        ["pip install --quiet -r requirements.txt"]
    assert convert_install("uv pip install --system -e '.[test]'", tmp_path) == ["pip install --quiet -e .[test]"]
    assert convert_install("pip install -r requirements.txt -r requirements-dev.txt mysqlclient", tmp_path) == \
        ["pip install --quiet -r requirements.txt -r requirements-dev.txt mysqlclient"]
    assert convert_install("echo hello", tmp_path) is None
    assert setup_plan.covers_project(["pip install --quiet -e ."]) and \
        not setup_plan.covers_project(["pip install --quiet pytest-django"])



def test_a_setup_cfg_of_tool_settings_does_not_make_an_app_a_package(tmp_path):
    # CTFd: setup.cfg held flake8/isort settings; `pip install -e .` failed on its flat layout and sank the run
    write(tmp_path, {"setup.cfg": "[flake8]\nmax-line-length = 120\n", "requirements.txt": "flask\n",
                     "pyproject.toml": "[project]\nname = 'ctfd'\nversion = '1'\n", "uv.lock": "",
                     "tests/test_a.py": "def test_a(): pass\n"})
    assert not setup_plan.installable(tmp_path)
    assert not any("-e ." in s for s in plan(tmp_path).install)
    (tmp_path / "setup.cfg").write_text("[metadata]\nname = pkg\n", encoding="utf-8")
    assert setup_plan.installable(tmp_path)
    assert any("--no-deps -e . ||" in s for s in plan(tmp_path).install)   # deps are in; the package is a nicety


def test_a_secret_the_tests_refuse_to_start_without_gets_a_placeholder(tmp_path):
    # redash: every test file failed to import: "You must set the REDASH_COOKIE_SECRET environment variable"
    guard = ('import os\nif "APP_COOKIE_SECRET" not in os.environ:\n'
             '    raise Exception("You must set the APP_COOKIE_SECRET environment variable")\n')
    for side in ("base", "head"):
        flask_repo(tmp_path / side, FLASK_APP)
        conftest = tmp_path / side / "conftest.py"
        conftest.write_text(guard + conftest.read_text(encoding="utf-8"), encoding="utf-8")
    lines = []
    r = check(tmp_path / "base", tmp_path / "head", backend="local", log=lines.append)
    assert r["stats"]["passed"] > 0
    assert any("the tests need APP_COOKIE_SECRET set: using placeholder values" in m for m in lines)


def test_placeholders_are_for_secrets_and_flags_never_for_addresses():
    from doppel.traffic import PLACEHOLDER, placeholder_settings
    out = ("Exception: You must set the REDASH_COOKIE_SECRET environment variable\n"
           "django.core.exceptions.ImproperlyConfigured: Set the SECRET_KEY environment variable\n"
           "E   DATABASE_URL\nE     Field required [type=missing, input_value={}, input_type=dict]\n"
           "E   PROJECT_NAME\nE     Field required [type=missing, input_value={}, input_type=dict]\n")
    got = placeholder_settings(out, {"SECRET_KEY": "set already"})
    assert got == {"PROJECT_NAME": PLACEHOLDER, "REDASH_COOKIE_SECRET": PLACEHOLDER}   # no DATABASE_URL: a service


def test_a_missing_header_names_the_package_to_install():
    from doppel import sandbox
    out = ("Modules/common.h:15:10: fatal error: lber.h: No such file or directory\n"
           "x.c:1:10: fatal error: sasl/sasl.h: No such file or directory\n")
    assert sandbox.header_packages(out) == ["libldap2-dev", "libsasl2-dev"]
    assert "libldap2-dev" in sandbox.BUILD_TOOLS                  # mealie's python-ldap: now on the first retry
    assert sandbox.header_packages("all fine") == []


def test_a_locked_project_installs_with_uv_sync_first(tmp_path):
    # paperless-ngx: uv.lock pins torch==2.14.0+cpu from PyTorch's own index; pip could not find it
    write(tmp_path, {"pyproject.toml": PYPROJECT_GROUPS.replace("dev = ", "testing = "), "uv.lock": "",
                     "tests/test_a.py": "def test_a(): pass\n"})
    step = next(s for s in plan(tmp_path).install if "uv sync" in s)
    assert "uv sync --quiet --frozen --inexact --no-install-project" in step and "sys.prefix" in step
    assert "|| (uv export --frozen" in step                       # and the pip route if uv sync can't


def test_apt_packages_given_to_an_action_are_system_packages(tmp_path):
    # paperless-ngx installs its system packages through its own action, not an apt-get line
    write(tmp_path, {".github/workflows/ci.yml": """\
        jobs:
          test:
            steps:
              - uses: ./.github/actions/apt-install
                with:
                  packages: unpaper tesseract-ocr poppler-utils
              - run: pytest
        """})
    assert plan(tmp_path).system == ["unpaper", "tesseract-ocr", "poppler-utils"]


def test_a_library_the_tests_cannot_load_names_its_package():
    from doppel import sandbox
    out = "  File \"magic/loader.py\", line 49\nImportError: failed to find libmagic.  Check your installation\n"
    assert sandbox.runtime_packages(out) == ["libmagic1"]
    assert sandbox.runtime_packages("OSError: libGL.so.1: cannot open shared object file") == ["libgl1"]
    assert sandbox.runtime_packages("all good") == []


def test_later_jobs_install_the_repaired_system_packages():
    from types import SimpleNamespace

    from doppel import sandbox
    from doppel.traffic import NebiusSession
    fake = SimpleNamespace(exports="export A=1; ", sandbox=sandbox)
    NebiusSession.add_system(fake, ["libmagic1"])
    assert fake.exports.startswith("export A=1; (") and "install -y -qq --no-install-recommends libmagic1" in \
        fake.exports and fake.exports.endswith("; ")


# --- round one's gaps (held-out, 12 Oct) --------------------------------------------------------------------

def test_a_macos_job_is_never_the_test_job(tmp_path):
    # lnbits: a macOS packaging check (unittest discover -s .github/packaging) was taken for the app's tests
    write(tmp_path, {".github/workflows/macos.yml": """\
        jobs:
          build:
            runs-on: macos-14
            steps:
              - run: python -m unittest discover -s .github/packaging -v
        """, "tests/test_a.py": "def test_a(): pass\n"})
    assert plan(tmp_path).source["test"] == "tests found in the repo"


def test_a_reusable_workflow_runs_with_the_callers_inputs_and_matrix(tmp_path):
    # lnbits: ci.yml calls tests.yml with custom-pytest, which runs pytest through pytest-action
    write(tmp_path, {".github/workflows/ci.yml": """\
        jobs:
          test-api:
            strategy:
              matrix:
                db-url: ["", "postgres://u:p@0.0.0.0:5432/db"]
            uses: ./.github/workflows/tests.yml
            with:
              custom-pytest: "uv run pytest tests/api"
              db-url: ${{ matrix.db-url }}
        """, ".github/workflows/tests.yml": """\
        on:
          workflow_call:
            inputs:
              custom-pytest:
                type: string
              db-url:
                type: string
                default: ""
        jobs:
          tests:
            runs-on: ubuntu-24.04
            steps:
              - if: ${{ inputs.db-url != '' }}
                run: sudo apt-get install -y postgresql-client
              - uses: pavelzw/pytest-action@v2
                env:
                  DB_URL: ${{ inputs.db-url }}
                  WALLET: FakeWallet
                with:
                  custom-pytest: ${{ inputs.custom-pytest }}
        """})
    p = plan(tmp_path)
    assert p.test == "python -m pytest tests/api" and p.system == [] and p.env == {"WALLET": "FakeWallet"}
    assert p.source["test"] == "CI workflow (ci.yml, job test-api -> tests.yml:tests)"


def test_tests_run_inside_the_projects_container_are_unwrapped_and_browser_jobs_score_down(tmp_path):
    # changedetection: every test line is `docker run IMAGE bash -c 'cd app; pytest ...'`; a browser job won
    write(tmp_path, {".github/workflows/t.yml": """\
        jobs:
          pyppeteer-tests:
            steps:
              - run: docker run --rm img bash -c "cd app;pytest --live-server-port=5004 tests/test_request.py"
          unit-tests:
            steps:
              - run: docker run img bash -c 'cd app;pytest tests/unit/'
        """, "requirements.txt": "flask\n"})
    p = plan(tmp_path)
    assert p.test == "python -m pytest tests/unit/" and p.workdir == "app"
    assert p.install[0] == "pip install --quiet -r requirements.txt"     # the repo's, the app folder has none


def test_requirements_next_to_the_code_when_the_root_has_none(tmp_path):
    # inventree: src/backend/requirements*.txt; only pytest was installed, so pytest-django was missing
    write(tmp_path, {"src/backend/requirements.txt": "django\n", "src/backend/requirements-dev.txt": "pytest-django\n",
                     "src/backend/InvenTree/manage.py": "", "docs/requirements.txt": "mkdocs\n",
                     "tests/test_a.py": "def test_a(): pass\n"})
    assert plan(tmp_path).install[0] == \
        "pip install --quiet -r src/backend/requirements.txt -r src/backend/requirements-dev.txt"


def test_a_pipfile_is_used_only_without_requirements_and_locked_first(tmp_path):
    # babybuddy: requirements.txt and a Pipfile without its lock; pipenv refused --system without a lock
    write(tmp_path, {"requirements.txt": "django\n", "Pipfile": "[packages]\ndjango = '*'\n", "manage.py": ""})
    assert not any("pipenv" in s for s in plan(tmp_path).install)
    (tmp_path / "requirements.txt").unlink()
    step = next(s for s in plan(tmp_path).install if "pipenv" in s)
    assert "([ -f Pipfile.lock ] || pipenv lock)" in step and "--ignore-pipfile" in step


def test_the_cis_own_sharding_flags_are_dropped():
    # polar: --splits 4 --group 1 would have run a quarter of the suite
    assert setup_plan.clean_test_command("pytest --splits 4 --group 1 --no-cov --store-durations") == \
        "python -m pytest --no-cov"


def test_git_missing_at_run_time_is_installed():
    from doppel import sandbox
    assert sandbox.runtime_packages("E   FileNotFoundError: [Errno 2] No such file or directory: 'git'") == ["git"]
