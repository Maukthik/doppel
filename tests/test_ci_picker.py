from doppel.setup_plan import _score, _tests_tooling, ci_plan

WEBLATE_SMOKE = "python -m unittest discover -s .devcontainer -p test_environment.py"


def test_devcontainer_check_is_tooling():
    assert _tests_tooling(WEBLATE_SMOKE)
    assert _tests_tooling("pytest ./.github/scripts/test_release.py")


def test_real_suites_are_not_tooling():
    for cmd in ("python -m pytest", "python manage.py test", "pytest tests/ --tb=short", "pytest .venv-free/x"):
        assert not _tests_tooling(cmd), cmd


def test_smoke_jobs_rank_lower():
    assert _score("rundev Run dev smoke Smoke test") < _score("test Test test Unit tests")


def test_ci_plan_skips_the_devcontainer_job(tmp_path):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "rundev.yml").write_text("""name: Run dev
on: push
jobs:
  smoke:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install -r requirements.txt
      - run: python -m unittest discover -s .devcontainer -p test_environment.py
""")
    picked = ci_plan(tmp_path)
    assert picked is None or ".devcontainer" not in picked.test


def test_docs_tooling_is_not_the_app():
    assert _tests_tooling("python -m pytest --no-cov docs/_ext")        # weblate docs.yml
    assert _tests_tooling("pytest ./doc/conf_test.py")
    for cmd in ("pytest tests/docs/test_render.py", "pytest weblate/docs_utils", "pytest --docs-check"):
        assert not _tests_tooling(cmd), cmd


def test_only_unfetched_submodules_are_skipped(tmp_path):
    from doppel.setup_plan import _unfetched_submodule
    (tmp_path / "compat-tests").mkdir()                       # how git leaves an unfetched submodule
    (tmp_path / "full").mkdir()
    (tmp_path / "full" / "test_x.py").write_text("")
    (tmp_path / ".gitmodules").write_text('[submodule "vendored"]\n\tpath = vendored\n\turl = x\n')
    assert _unfetched_submodule(tmp_path, "compat-tests")
    assert _unfetched_submodule(tmp_path, "vendored")          # listed, not there
    assert not _unfetched_submodule(tmp_path, "full")
    assert not _unfetched_submodule(tmp_path, "app")           # a path inside the job's container


def test_ci_plan_skips_a_job_in_an_empty_submodule(tmp_path):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (tmp_path / "compat-tests").mkdir()          # the submodule, not checked out
    (wf / "api-compatibility-tests.yaml").write_text("""name: API compatibility
on: push
jobs:
  compatibility-tests:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: compat-tests
    steps:
      - uses: actions/checkout@v4
      - run: pip install pytest
      - run: python -m pytest -vv
""")
    picked = ci_plan(tmp_path)
    assert picked is None or picked.workdir != "compat-tests"
