from doppel.setup_plan import _sub, convert_install
from doppel.setup_plan import clean_test_command as clean

PREFECT = ("pytest ${{ matrix.test-type.modules }} -m 'not windows' --numprocesses auto --dist worksteal "
           "--disable-docker-image-builds --durations 26")


def test_flag_values_go_with_their_flags():
    out = clean("pytest tests/ -n auto --dist worksteal --durations 26 --junitxml out.xml -x")
    assert out.split() == ["python", "-m", "pytest", "tests/", "-x"]


def test_unfilled_expressions_never_reach_pytest():
    out = clean(PREFECT)
    assert "${{" not in out and "}}" not in out and "matrix." not in out
    assert "worksteal" not in out.split() and "26" not in out.split() and "auto" not in out.split()


def test_nested_matrix_values_are_filled():
    matrix = {"test-type": {"name": "Server", "modules": "tests/server/"}, "python-version": "3.12"}
    assert _sub("pytest ${{ matrix.test-type.modules }}", matrix, {}) == "pytest tests/server/"
    assert _sub("${{ matrix.python-version }}", matrix, {}) == "3.12"
    assert _sub("${{ matrix.test-type }}", matrix, {}) == "${{ matrix.test-type }}"   # a mapping: left


def test_uv_pip_install_drops_the_ci_python(tmp_path):
    assert convert_install("uv pip install --python 3.12 --system -e .[proxy]", tmp_path) == \
        ["pip install --quiet -e .[proxy]"]
    assert convert_install("uv pip install -p 3.12 fastapi", tmp_path) == ["pip install --quiet fastapi"]


def test_version_ranges_are_quoted(tmp_path):
    (step,) = convert_install("pip install --upgrade -e .[dev] pydantic>=2.4,<3", tmp_path)
    assert "'pydantic>=2.4,<3'" in step


def test_artifacts_built_by_earlier_ci_steps_are_dropped(tmp_path):
    assert convert_install("pip install rust-bridge-dist/*.whl", tmp_path) == []          # litellm
    assert convert_install("pip install --no-deps dist/*.whl", tmp_path) == []
    (tmp_path / "wheels").mkdir()
    (tmp_path / "wheels" / "x-1.0-py3-none-any.whl").write_text("")
    assert convert_install("pip install wheels/*.whl", tmp_path) == ["pip install --quiet wheels/*.whl"]
    assert convert_install("pip install -e .[dev]", tmp_path) == ["pip install --quiet -e .[dev]"]


def test_ci_shell_variables_are_dropped():
    from doppel.setup_plan import clean_test_command as clean
    litellm = ("pytest '${pytest_args[@]}' --tb=short -vv '--maxfail=${MAX_FAILURES}' '${xdist_args[@]}' "
               "--reruns '${RERUNS}' --reruns-delay 1 '--timeout=${TEST_TIMEOUT_SECONDS}' "
               "--rerun-except 'from pytest-timeout'")
    assert clean(litellm) == "python -m pytest --tb=short -vv --reruns-delay 1 --rerun-except 'from pytest-timeout'"
    assert clean("pytest -x $EXTRA tests/") == "python -m pytest -x tests/"     # -x is not waiting for a value
    assert clean("pytest tests/ -k smoke") == "python -m pytest tests/ -k smoke"
