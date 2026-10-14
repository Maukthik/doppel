from doppel.sandbox import runtime_packages, runtime_pip
from doppel.traffic import NebiusSession

# error text from real runs
ISSO = ("isso/__init__.py:28: in <module>\n    import pkg_resources\n"
        "E   ModuleNotFoundError: No module named 'pkg_resources'")                # isso 2e58889 (Feb 2026)
LANGFLOW = ("E   ImportError: Failed to initialize: Bad git executable.\n"
            "E   The git executable must be specified in one of the following ways:")  # langflow round2


def test_pkg_resources_gets_a_setuptools_that_still_has_it():
    assert runtime_pip(ISSO) == ["setuptools<81"]


def test_no_package_is_guessed_from_another_module_name():
    assert runtime_pip("ModuleNotFoundError: No module named 'websockets'") == []
    assert runtime_pip("") == []


def test_gitpython_without_git_gets_git():
    assert "git" in runtime_packages(LANGFLOW)


def test_add_pip_installs_before_every_later_job():
    session = object.__new__(NebiusSession)          # only the job prefix matters here, no sandbox
    session.exports = "export A=1; "
    session.add_pip(["setuptools<81"])
    assert session.exports.startswith("export A=1; ")
    assert "python -m pip install --quiet 'setuptools<81'" in session.exports
