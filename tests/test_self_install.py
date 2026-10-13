import os
import subprocess
import sys

from doppel.sandbox import install_shell, scm_version_env
from doppel.setup_plan import SELF_INSTALL_NOTE, SRC_ON_PATH


def test_scm_project_gets_a_version_for_itself_only(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["setuptools>=64", "setuptools_scm>=8"]\n\n'
        '[project]\nname = "fava"\ndynamic = ["version"]\n')
    env = scm_version_env(tmp_path)
    assert env == "export SETUPTOOLS_SCM_PRETEND_VERSION_FOR_FAVA=0.0.0; "
    assert install_shell("pip install -e .", tmp_path).startswith(env)


def test_dist_names_are_normalised(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["hatchling", "hatch-vcs"]\n[project]\nname = "my-pkg.name"\n')
    assert "PRETEND_VERSION_FOR_MY_PKG_NAME=" in scm_version_env(tmp_path)


def test_plain_project_is_left_alone(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "isso"\nversion = "1.0"\n')
    assert scm_version_env(tmp_path) == ""
    assert install_shell("pip install -e .", tmp_path) == "pip install -e ."


def test_failed_self_install_puts_the_source_on_the_path():
    assert "doppel-src.pth" in SELF_INSTALL_NOTE and SRC_ON_PATH in SELF_INSTALL_NOTE


def test_src_on_path_command_is_valid_python(tmp_path):
    code = SRC_ON_PATH.split('-c "', 1)[1].rsplit('"', 1)[0]
    compile(code, "<SRC_ON_PATH>", "exec")
