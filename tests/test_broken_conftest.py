from doppel.traffic import broken_conftests, shards
from doppel.traffic import test_files as files_for   # imported as test_files, pytest would collect it

LITELLM = ("ImportError while loading conftest '/app/tests/e2e/llm_translation/conftest.py'.\n"
           "E   ModuleNotFoundError: No module named 'websockets'\n"
           "ImportError while loading conftest '/app/tests/e2e/llm_translation/conftest.py'.")
LANGFLOW = ("ImportError while loading conftest '/app/src/langflow-stepflow/tests/conftest.py'.\n"
            "_pytest.pathlib.ImportPathMismatchError: ('tests.conftest', '/app/src/backend/tests/conftest.py', "
            "PosixPath('/app/src/langflow-stepflow/tests/conftest.py'))")


def _repo(tmp_path, *files):
    for f in files:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("")
    return tmp_path


def test_broken_conftest_folders_are_found_relative_to_the_code(tmp_path):
    repo = _repo(tmp_path, "tests/e2e/llm_translation/conftest.py", "src/langflow-stepflow/tests/conftest.py",
                 "conftest.py")
    assert broken_conftests(LITELLM, repo) == ["tests/e2e/llm_translation"]
    assert broken_conftests(LANGFLOW, repo) == ["src/langflow-stepflow/tests"]
    assert broken_conftests("ImportError while loading conftest '/app/conftest.py'.", repo) == []   # not the root
    assert broken_conftests("all good", repo) == []


def test_test_files_honour_ignore(tmp_path):
    repo = _repo(tmp_path, "tests/a/test_x.py", "tests/e2e/test_y.py", "tests/e2e/deep/test_z.py")
    assert files_for(repo, "python -m pytest tests --ignore=tests/e2e") == ["tests/a/test_x.py"]
    assert files_for(repo, "python -m pytest tests --ignore tests/e2e/") == ["tests/a/test_x.py"]
    assert len(files_for(repo, "python -m pytest tests")) == 3


def test_shards_never_name_ignored_files(tmp_path):
    repo = _repo(tmp_path, *[f"tests/a/test_{i}.py" for i in range(30)], *[f"tests/e2e/test_{i}.py" for i in range(30)])
    for cmd in shards(repo, "python -m pytest tests --ignore=tests/e2e"):
        assert "tests/e2e/test_" not in cmd
