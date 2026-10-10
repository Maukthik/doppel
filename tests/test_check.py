"""doppel check: both modes at once, one report, one exit code."""
import json

from doppel import cli
from tests.test_traffic import FLASK_APP, flask_repo

LEAKY = FLASK_APP.replace('"name": u["name"],', '"name": u["name"], "secret": u["secret"],')
# as `doppel init` writes it: its calibration (the same code replayed twice) puts the random nonce under ignore
TWIN = '[app]\nstart = "flask --app app run --port $PORT"\nready = "/users/1"\n\n[diff]\nignore = ["nonce"]\n'
USERS = [{"name": "reader", "steps": [{"method": "GET", "path": "/users/1"}]}]


def both(tmp_path, head_app=LEAKY):
    base, head = flask_repo(tmp_path / "b"), flask_repo(tmp_path / "h", head_app)
    for root in (base, head):
        (root / "doppel.toml").write_text(TWIN, encoding="utf-8")
    (base / "doppel-personas.json").write_text(json.dumps(USERS), encoding="utf-8")
    return base, head


def run(*args) -> int:
    return cli.main(["check", "--backend", "local", *map(str, args)])


def test_both_modes_find_the_leak_in_one_report(tmp_path, capsys):
    base, head = both(tmp_path)
    out = tmp_path / "check.json"
    assert run("--base", base, "--head", head, "--strict", "--json", out) == 1
    md = capsys.readouterr().out
    assert "## Doppel: 2 behavior change(s)" in md
    assert "| your tests | test_app.py::test_user | `GET /users/1` | changed: secret (added)" in md
    assert "| invented users | reader | `GET /users/1` | changed: secret (added)" in md
    assert "**Your tests** (test mode, no setup): 4 tests sent 4 requests" in md
    assert "users (from doppel-personas.json)" in md
    data = json.loads(out.read_text())
    assert data["mode"] == "check" and data["regressions"] == 2
    assert {r["source"] for r in data["rows"]} == {"your tests", "invented users"}


def test_no_doppel_toml_is_test_mode_only(tmp_path, capsys):
    base, head = flask_repo(tmp_path / "b"), flask_repo(tmp_path / "h", LEAKY)
    assert run("--base", base, "--head", head, "--strict") == 1
    md = capsys.readouterr().out
    assert "not set up (no doppel.toml)" in md and "| your tests |" in md and "| invented users |" not in md


def test_same_code_is_quiet_in_both_modes(tmp_path, capsys):
    base, _ = both(tmp_path)
    assert run("--base", base, "--head", base, "--strict") == 0
    assert "## Doppel: no regressions" in capsys.readouterr().out


def test_neither_mode_able_to_compare_is_exit_2_not_a_pass(tmp_path, capsys):
    (tmp_path / "empty").mkdir()
    assert run("--base", tmp_path / "empty", "--head", tmp_path / "empty", "--strict") == 2
    assert "## Doppel: couldn't check this PR" in capsys.readouterr().out


def test_the_action_runs_doppel_check_with_base_branch_personas():
    import pytest
    yaml = pytest.importorskip("yaml")
    from pathlib import Path
    action = yaml.safe_load((Path(__file__).parent.parent / "action.yml").read_text(encoding="utf-8"))
    script = next(s["run"] for s in action["runs"]["steps"] if s.get("name") == "Run Doppel")
    assert "doppel check" in script and "--strict" in script and 'PERSONAS="$BASE/' in script
    assert "--no-twin" in script and action["inputs"]["twin"]["default"] == "auto"
