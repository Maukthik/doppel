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
    # one change (the leak), found by both modes: one row, with both sources counted
    assert "## Doppel: 1 behavior change, seen in 2 requests" in md
    assert ("| 1 | Responses now include secret | `GET /users/{id}` | "
            "1 request from 1 test, 1 request from 1 user |") in md
    assert "| your tests | test_app.py::test_user | `GET /users/1` | changed: secret (added)" in md
    assert "| invented users | reader | `GET /users/1` | changed: secret (added)" in md
    assert "**Your tests** (test mode, no setup): 4 tests sent 4 requests" in md
    assert "users (from doppel-personas.json)" in md
    data = json.loads(out.read_text())
    assert data["mode"] == "check" and data["regressions"] == 1 and data["regression_requests"] == 2
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


def test_the_report_groups_requests_into_distinct_changes():
    from doppel.check import report
    why = {"qty": "Removing the qty validation lets invalid orders through.",
           "total": "order_total now applies tax before the coupon discount."}
    rows = ([{"source": "invented users", "who": f"user{i}", "request": "POST /orders", "detail": "status 400 -> 201",
              "base": 400, "head": 201, "kind": "status", "label": "regression", "why": why["qty"]} for i in range(6)]
            + [{"source": "invented users", "who": "buyer", "request": req, "detail": "changed: total",
                "base": {"total": 1}, "head": {"total": 2}, "kind": "body", "label": "regression",
                "why": why["total"]} for req in ("POST /orders", "GET /orders/1", "GET /orders/2")])
    md = report({"ran": False, "reason": "no test suite found"}, {"ran": True, "rows": rows, "personas": 7,
                                                                  "source": "from personas.json"})
    assert "## Doppel: 2 regressions the PR doesn't mention, seen in 9 requests" in md
    assert "| 1 | Status 400 -> 201 | `POST /orders` | 6 requests from 6 users |" in md
    assert "`POST /orders`<br>`GET /orders/{id}` | 3 requests from 1 user |" in md   # ids folded into a route
    assert "All 9 requests behind these" in md


def test_a_long_stop_reason_keeps_its_first_line_and_its_end():
    from doppel.errors import head_and_tail
    frames = "\n".join(f'  File "/usr/lib/python3.12/x{i}.py", line {i}, in f' for i in range(300))
    reason = ("the test command ran no test on the unchanged code (exit 1). Its output:\n" + frames +
              "\nModuleNotFoundError: No module named 'environ'")
    kept = head_and_tail(reason, 2500)
    assert len(kept) <= 2500
    assert kept.startswith("the test command ran no test") and kept.endswith("No module named 'environ'")
    assert head_and_tail("short", 2500) == "short"


def test_the_report_of_a_stopped_check_shows_the_final_error():
    from doppel.check import report
    frames = "\n".join(f'  File "/x{i}.py", line {i}' for i in range(300))
    reason = "the test command ran no test on the unchanged code (exit 1). Its output:\n" + frames + \
        "\ndjango.core.exceptions.ImproperlyConfigured: Set the SECRET_KEY environment variable"
    text = report({"ran": False, "reason": reason}, {"ran": False, "reason": "turned off (--no-twin)"})
    assert "ImproperlyConfigured: Set the SECRET_KEY" in text


def test_untested_suspicions_keep_no_regressions_from_reading_as_a_pass():
    from doppel.check import report, untested
    tests = {"ran": False, "reason": "no test suite found"}
    twin = {"ran": True, "rows": [], "personas": 19, "source": "written by Nemotron for this PR",
            "suspicions": [{"id": "s1", "claim": "website escaped with quote=False", "status": "untested"},
                           {"id": "s2", "claim": "edit no longer escapes", "status": "untested"},
                           {"id": "s3", "claim": "total rounded", "status": "refuted"}]}
    assert untested(twin) == (2, 3)
    md = report(tests, twin)
    assert md.startswith("## Doppel: no regressions found, but 2 of the reviewer's 3 suspicions couldn't be tested")
    every = {**twin, "suspicions": [{**s, "status": "refuted"} for s in twin["suspicions"]]}
    assert report(tests, every).startswith("## Doppel: no regressions\n")
    assert untested({"ran": False, "suspicions": twin["suspicions"]}) == (0, 0)   # no twin, nothing claimed
