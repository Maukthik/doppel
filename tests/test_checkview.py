"""The doppel check page and the zero-setup results page."""
import json
import sys
from pathlib import Path

from doppel.checkview import headline, is_check, render_check

HASH = "scrypt:32768:8:1$kKr5dNCX1YvSJk1y$64aab80f332b0dfa93895df14a201c7c78b2ec55bbef61138b58520af26d"
ROWS = [{"source": "your tests", "who": f"test_users.py::test_{i}", "request": req, "kind": "body",
         "detail": detail, "base": {"password_hash": "(absent)"}, "head": {"password_hash": HASH},
         "label": "unjudged", "why": ""}
        for i, (req, detail) in enumerate([("GET /api/users", "changed: data.*.password_hash (added) x2"),
                                           ("GET /api/users/7", "changed: password_hash (added)"),
                                           ("GET /api/feed", "changed: data.*.author.password_hash (added) x3")])]
DATA = {"mode": "check", "app": "microblog-api", "pr": {"title": "Return the full user record", "body": ""},
        "rows": ROWS, "seconds": 51.7,
        "tests": {"ran": True, "stats": {"tests": 44, "requests": 117, "confirmation": {"ran": True, "tests": 3}},
                  "test": "python -m pytest -p no:logging", "source": "tox.ini", "noise_keys": ["timestamp"]},
        "twin": {"ran": False, "skipped": True, "reason": "turned off (--no-twin)"}}


def test_one_change_however_many_routes_and_paths_it_shows_up_on():
    page = render_check(DATA)
    assert "1 behavior change, seen in 3 requests" in page
    assert "Responses now include password_hash" in page                      # plain words, not raw paths
    assert page.count('<article class="change') == 1
    assert "3 requests from 3 tests" in page and "GET /api/users/{id}" in page  # ids folded into routes


def test_base_and_head_side_by_side_raw_locally_masked_on_the_public_site():
    assert HASH in render_check(DATA)                                          # local: the evidence itself
    public = render_check(DATA, public=True)
    assert HASH not in public and "[hidden on the site]" in public


def test_the_page_says_what_each_mode_did_and_how_doppel_decides():
    page = render_check(DATA)
    assert "<b>44</b> tests sent <b>117</b> requests" in page and "(from tox.ini)" in page
    assert "turned off (--no-twin)" in page and "Base vs base again" in page


def test_no_differences_and_no_comparison_read_differently():
    same = render_check({**DATA, "rows": []})
    assert "No regressions" in same and "Every response matched" in same
    none = render_check({**DATA, "rows": [], "tests": {"ran": False, "reason": "no test suite found"}})
    assert 'class="banner grey"' in none and "Couldn&#x27;t check this PR" in none


def test_headlines_name_the_change():
    def g(kind, detail):
        return {"example": {"kind": kind, "detail": detail}}
    assert headline(g("body", "changed: total")) == "Values changed: total"
    assert headline(g("body", "changed: in_stock (added), id (removed)")) == \
        "Responses now include in_stock; responses no longer include id"
    crash = headline(g("error", "status 200 -> 0 (the base branch's test fails on this)"))
    assert crash.startswith("Requests now crash")
    assert headline(g("status", "status 400 -> 201")) == "Status 400 -> 201"


def test_doppel_view_recognises_a_check(tmp_path):
    (tmp_path / "check.json").write_text(json.dumps(DATA))
    assert is_check(tmp_path / "check.json") and is_check(tmp_path)
    (tmp_path / "run.json").write_text(json.dumps({"worlds": {}}))
    assert not is_check(tmp_path / "run.json")


def test_the_results_page_lists_every_app(tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(__file__).parent.parent / "bench"))
    import fresh_apps
    monkeypatch.setattr(fresh_apps, "RESULTS", tmp_path)
    for name, status in (("ctfd", "works"), ("redash", "stopped")):
        (tmp_path / name).mkdir()
        (tmp_path / name / "result.json").write_text(json.dumps(
            {"name": name, "framework": "Flask", "status": status, "tests": 10, "requests": 30, "commit": "abc",
             "seconds": 5, "category": "needs a service", "reason": "3 of 4 tests fail on the unchanged code",
             "test_command": "python -m pytest", "source": "tox.ini"}))
    page = fresh_apps.page().read_text()
    assert "1 work" in page and "1 stopped" in page and "needs a service" in page and "<b>30</b> requests" in page


def test_doppel_view_public_masks_what_a_public_page_must_not_show(tmp_path):
    from doppel import cli
    (tmp_path / "check.json").write_text(json.dumps(DATA))
    assert cli.main(["view", str(tmp_path / "check.json"), "--no-open", "--public", "--out",
                     str(tmp_path / "p.html")]) == 0
    assert HASH not in (tmp_path / "p.html").read_text() and "[hidden on the site]" in (tmp_path / "p.html").read_text()


def test_the_action_publishes_the_page_and_links_it_first():
    import yaml
    action = yaml.safe_load((Path(__file__).parent.parent / "action.yml").read_text(encoding="utf-8"))
    steps = {s.get("name"): s for s in action["runs"]["steps"]}
    names = [s.get("name") for s in action["runs"]["steps"]]
    assert names.index("Run Doppel") < names.index("Publish the check page") < names.index("Comment on the PR")
    assert "--public" in steps["Run Doppel"]["run"] and "gh-pages" in steps["Publish the check page"]["run"]
    assert "exit 0" in steps["Publish the check page"]["run"]          # publishing can never fail the job
    assert "Open the full check page" in steps["Comment on the PR"]["run"]
    assert action["inputs"]["publish-page"]["default"] == "true"


# the live demo page (doppel-demo PR #1): one leak shown as 4 cards, base "hidden" though absent, the hash masked twice
PLACES = [("GET /api/me", "changed: password_hash (added)", {"password_hash": None}, "in user detail endpoints"),
          ("GET /api/users/1", "changed: password_hash (added)", {"password_hash": HASH}, "in user detail endpoints"),
          ("GET /api/users", "changed: data.*.password_hash (added)", {"data.0.password_hash": HASH}, "in user lists"),
          ("GET /api/feed", "changed: data.*.author.password_hash (added) x3", {"data.0.author.password_hash": HASH},
           "in feed author objects"),
          ("GET /api/posts/1", "changed: author.password_hash (added)", {"author.password_hash": HASH},
           "in post author objects")]
LIVE = {**DATA, "rows": [
    {"source": "your tests", "who": f"t{i}", "request": req, "kind": "body", "detail": d,
     "base": {k: "(absent)" for k in head}, "head": head, "label": "regression",
     "why": f"The PR only documents has_password, but the diff exposes password hashes {where}."}
    for i, (req, d, head, where) in enumerate(PLACES)]}


def test_one_leak_judged_place_by_place_is_still_one_regression():
    page = render_check(LIVE)
    assert "1 regression the PR doesn&#x27;t mention, seen in 5 requests" in page
    assert page.count('<article class="change') == 1


def test_the_public_page_shows_absent_as_absent_and_masks_the_hash_once():
    page = render_check(LIVE, public=True)
    assert "(absent)" in page                                         # base really didn't have it
    assert "scrypt:32768:8:1$[hidden on the site]" in page            # the scheme stays visible: it IS a hash
    assert "on the site] on the site]" not in page and HASH not in page


def test_the_example_shows_a_real_value_not_null():
    from doppel.check import group
    [g] = group(LIVE["rows"])
    assert g["example"]["head"] != {"password_hash": None}


def test_masking_twice_changes_nothing():
    from doppel.site import _scrub_text, sanitize
    once = sanitize(LIVE)
    assert sanitize(once) == once
    text = '{"password_hash": "' + HASH + '"}'
    assert _scrub_text(_scrub_text(text)) == _scrub_text(text)


def test_the_head_box_does_not_inherit_the_viewers_head_row_grid():
    page = render_check(DATA)
    assert 'class="w after"' in page and 'class="w head"' not in page   # the viewer's CSS styles .head as a grid row
    assert "What is Doppel?" in page and 'class="what"' in page


def test_the_failing_step_says_how_many_and_where_to_look():
    import yaml
    action = yaml.safe_load((Path(__file__).parent.parent / "action.yml").read_text(encoding="utf-8"))
    step = next(s for s in action["runs"]["steps"] if s.get("name") == "Fail on regressions")
    assert "::error title=Doppel::" in step["run"] and "${PAGE:-" in step["run"] and "regressions" in step["run"]
    assert "align-items:start" in render_check(DATA)       # a short mode card is not stretched into an empty box


def test_the_page_commit_does_not_reference_the_pr():
    import yaml
    action = yaml.safe_load((Path(__file__).parent.parent / "action.yml").read_text(encoding="utf-8"))
    step = next(s for s in action["runs"]["steps"] if s.get("name") == "Publish the check page")
    assert 'PR #' not in step["run"].replace('"#N"', "")   # "#1" in a commit message adds a PR timeline entry


def test_a_stopped_test_mode_shows_the_end_of_its_output_folded(tmp_path):
    from doppel.checkview import write_check_view
    reason = "the test command ran no test on the unchanged code (exit 4). Its output:\n" + \
        "noise\n" * 900 + "E   ModuleNotFoundError: No module named 'responses'"
    (tmp_path / "check.json").write_text(json.dumps({"tests": {"ran": False, "reason": reason},
                                                     "twin": {"ran": False, "reason": "turned off (--no-twin)"},
                                                     "rows": []}))
    write_check_view(tmp_path / "check.json", tmp_path / "p.html")
    page = (tmp_path / "p.html").read_text(encoding="utf-8")
    assert "The end of the output" in page
    assert "ModuleNotFoundError: No module named" in page and "responses" in page.split("ModuleNotFoundError")[1][:40]


def test_untested_suspicions_turn_the_banner_grey():
    sus = [{"id": "s1", "claim": "website escaped with quote=False", "status": "untested"}]
    page = render_check({**DATA, "rows": [], "twin": {"ran": True, "suspicions": sus, "personas": 19}})
    assert 'class="banner grey"' in page
    assert "No regressions found, but 1 of the reviewer&#x27;s 1 suspicion couldn&#x27;t be tested" in page
