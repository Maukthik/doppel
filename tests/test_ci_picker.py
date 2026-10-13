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
