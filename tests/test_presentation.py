"""What a reader sees in the PR comment and on the check page: plain words, nothing cut mid-thought."""
from doppel.check import _cut, example, report
from doppel.checkview import headline, render_check

HASH = "scrypt:32768:8:1$vOQdSi9zHZhK00Np$22cfbfb704f124cb1e05530805196c98f974925ffe12315e77581c4569b7452"
WHY = ("The PR description only mentions tidying error handlers without announcing the field rename from "
       "'message' to 'name' in the JSON response body, which changes the API contract for every client that "
       "reads the error message, including the web front end and the mobile app.")
RENAME = {"source": "your tests", "who": "test_auth.py::test_bad_token", "request": "POST /api/tokens",
          "kind": "body", "detail": "changed: message (removed), name (added)", "label": "regression", "why": WHY,
          "base": {"message": "Unauthorized", "name": "(absent)"},
          "head": {"message": "(absent)", "name": "Unauthorized"}}
LEAK = {**RENAME, "request": "GET /api/users", "detail": "changed: data.*.password_hash (added)",
        "base": {"data.0.password_hash": "(absent)"}, "head": {"data.0.password_hash": HASH},
        "why": "The diff exposes password hashes."}


def group_of(row):
    return {"example": row, "rows": [row]}


def test_a_field_that_moved_to_a_new_name_is_called_a_rename():
    assert headline(group_of(RENAME)) == "Field renamed in responses: message → name"
    assert headline(group_of(LEAK)) == "Responses now include password_hash"


def test_the_example_says_absent_in_words_and_shows_values_not_markers():
    assert example(RENAME["base"], RENAME["head"]) == '`message: "Unauthorized"` → `name: "Unauthorized"`'
    text = example(LEAK["base"], LEAK["head"])
    assert text == '`no password_hash` → `password_hash: "scrypt:32768:8:1$[hidden]"`'   # public: masked


def test_the_reason_is_not_cut_and_a_long_value_is_cut_at_a_word():
    md = report({"ran": False, "reason": "test mode couldn't run", "rows": [RENAME]},
                {"ran": False, "skipped": True, "reason": "not part of this check (test mode only)"})
    assert WHY in md and "turned off" not in md
    assert _cut("the quick brown fox jumps over", 20) == "the quick brown fox…"


def test_the_check_page_shows_fields_line_by_line_and_how_long_it_took():
    page = render_check({"mode": "check", "rows": [RENAME], "tests": {"ran": True, "stats": {}}, "twin": {},
                         "pr": {"title": "Small cleanup"}, "seconds": 39.4})
    assert "message: &quot;Unauthorized&quot;" in page and "name: not in the response" in page
    assert "Checked in 39 s." in page and "Seconds:" not in page
    assert "word-break:break-all" not in page    # a value wraps at its spaces, not mid-word


def test_no_value_in_the_comment_shows_a_secret_or_the_absent_marker():
    md = report({"ran": False, "reason": "x", "rows": [LEAK]}, {"ran": False, "reason": "off"})
    assert HASH not in md and "(absent)" not in md and "The request behind this" in md


def test_a_python_that_does_not_exist_is_named_not_reported_as_a_failing_suite(tmp_path, capsys):
    from doppel import cli
    from tests.test_traffic import flask_repo
    base = flask_repo(tmp_path / "b")
    code = cli.main(["check", "--backend", "local", "--no-twin", "--base", str(base), "--head", str(base),
                     "--python", str(tmp_path / "nope" / "python")])
    assert code == 2 and "no such interpreter" in capsys.readouterr().out


def test_a_pr_that_stops_the_suite_from_running_is_one_clear_change(tmp_path, capsys):
    from doppel import cli
    from tests.test_traffic import FLASK_APP, flask_repo
    base = flask_repo(tmp_path / "b")
    import textwrap
    head = flask_repo(tmp_path / "h", "import not_installed_anywhere\n" + textwrap.dedent(FLASK_APP))
    code = cli.main(["check", "--backend", "local", "--no-twin", "--base", str(base), "--head", str(head), "--strict"])
    md = capsys.readouterr().out
    assert code == 1
    assert "The tests can't run on the PR's code: ModuleNotFoundError: No module named 'not_installed_anywhere'" in md
    assert "no longer exists in head" not in md and "seen in" not in md.splitlines()[0]
    assert "on the PR's code none ran" in md
