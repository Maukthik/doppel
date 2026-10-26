"""Gaps found by reading the whole code base line by line (October 2026): each one pinned here."""
import json
from pathlib import Path

import yaml

from doppel import bootstrap, cli
from doppel.diff import Finding
from doppel.judge import Verdict
from doppel.report import markdown

HASH = "scrypt:32768:8:1$vOQdSi9zHZhK00Np$22cfbfb704f124cb1e05530805196c98f974925ffe12315e77581c4569b7452"
ROOT = Path(__file__).resolve().parent.parent


def run_json(tmp_path: Path) -> Path:
    step = {"i": 0, "request": "GET /api/users", "status": 200, "ms": 3.0}
    raw = {"findings": [{"kind": "body", "persona": "alice", "step": 0, "request": "GET /api/users",
                         "detail": "changed: password_hash (added)", "base": {"password_hash": "(absent)"},
                         "head": {"password_hash": HASH}}],
           "worlds": {"base": [{"persona": "alice", "steps": [{**step, "body": {"id": 1}}]}],
                      "head": [{"persona": "alice", "steps": [{**step, "body": {"id": 1, "password_hash": HASH}}]}]}}
    path = tmp_path / "run.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def test_the_replay_page_the_action_publishes_is_masked(tmp_path):
    # action.yml publishes the twin's run page as replay.html next to the check page: it must be masked too
    path = run_json(tmp_path)
    assert cli.main(["view", str(path), "--no-open", "--out", str(tmp_path / "local.html")]) == 0
    assert HASH in (tmp_path / "local.html").read_text("utf-8")          # your own machine: the raw evidence
    assert cli.main(["view", str(path), "--no-open", "--public", "--out", str(tmp_path / "public.html")]) == 0
    page = (tmp_path / "public.html").read_text("utf-8")
    assert HASH not in page and "scrypt:32768:8:1$" in page             # the leak stays visible, the hash doesn't
    action = (ROOT / "action.yml").read_text(encoding="utf-8")
    run_view = next(line for line in action.splitlines() if "doppel view .doppel/out/run.json" in line)
    assert "--public" in run_view


def test_a_public_run_page_that_would_show_a_credential_is_never_written(tmp_path, monkeypatch):
    path = run_json(tmp_path)
    data = json.loads(path.read_text("utf-8"))
    data["worlds"]["head"][0]["steps"][0]["body"]["note"] = "key nb-live-0123456789abcdef"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("NEBIUS_API_KEY", "nb-live-0123456789abcdef")
    assert cli.main(["view", str(path), "--no-open", "--public", "--out", str(tmp_path / "p.html")]) == 2
    assert not (tmp_path / "p.html").exists()


def test_init_check_reads_the_front_door_seed_file_too(tmp_path, monkeypatch):
    (tmp_path / "app.py").write_text("print('app')\n", encoding="utf-8")
    (tmp_path / "doppel.toml").write_text('[app]\nstart = "python app.py"\nready = "/"\n'
                                          'seed_requests = "doppel_seed.json"\n', encoding="utf-8")
    (tmp_path / "doppel_seed.json").write_text(json.dumps(
        [{"method": "POST", "path": "/api/users", "json": {"username": "alice", "password": "secret"}}]),
        encoding="utf-8")
    monkeypatch.setattr(bootstrap, "try_files", lambda *a, **k: {"ok": True})
    result = bootstrap.check(tmp_path, backend="local", log=lambda m: None)
    found = [p for _, problems in result["checks"] for p in problems]
    assert not any("no doppel_seed.json was returned" in p for p in found)


def test_a_swapped_package_keeps_its_version_pin(tmp_path):
    (tmp_path / "requirements.txt").write_text("flask==3.0\npsycopg2==2.9.9\nuwsgi>=2\n", encoding="utf-8")
    assert bootstrap.derived_install(tmp_path) == "pip install --quiet 'flask==3.0' 'psycopg2-binary==2.9.9'"


def test_401_and_403_are_different_errors_but_ports_and_ids_still_blur():
    assert not bootstrap.same_error("the login step POST /api/tokens answered 401",
                                    "the login step POST /api/tokens answered 403")
    assert bootstrap.same_error("GET /api/users/7 -> 404: not found", "GET /api/users/12 -> 404: not found")
    assert bootstrap.same_error("could not connect to 127.0.0.1:8123", "could not connect to 127.0.0.1:9440")
    assert not bootstrap.same_error("GET /api/me -> 401", "GET /api/me -> 500")


def test_the_heading_counts_distinct_changes_after_the_judges_own_rules():
    # the secret rule gives each finding of one change its own (equal) verdict: still one change, not two
    findings = [Finding("body", f"t{i}", 0, f"GET /api/users/{i}", "changed: password_hash (added)",
                        {"password_hash": "(absent)"}, {"password_hash": HASH}) for i in (1, 2)]
    why = "Changes `password_hash`, a field that looks like it holds a secret."
    verdicts = [Verdict("needs_human", why), Verdict("needs_human", why)]
    md = markdown(findings, 2, 2, {"verdicts": verdicts, "summary": ""}, who="test")
    assert "0 regression · 1 need a human · 0 intended" in md


def test_refuted_suspicions_look_the_same_in_the_comment_and_on_the_page():
    from doppel import report
    assert report.CHECK["refuted"] == "⚪ refuted"


def test_the_example_workflow_can_publish_its_check_page():
    flow = yaml.safe_load((ROOT / "examples" / "demo-repo" / "main" / "doppel.yml").read_text(encoding="utf-8"))
    assert flow["permissions"] == {"contents": "write", "pull-requests": "write"}


def test_declined_try_issues_do_not_use_up_the_day(monkeypatch):
    import datetime as dt
    import importlib.util
    spec = importlib.util.spec_from_file_location("try_pr", ROOT / "try" / "scripts" / "try_pr.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    asked = []
    monkeypatch.setattr(mod, "_request", lambda method, path, body=None: asked.append(path) or {"total_count": 3})
    assert mod.runs_today("me/doppel-try", dt.datetime(2026, 10, 25, tzinfo=dt.UTC)) == 3
    assert "-label%3A%22not%20run%22" in asked[0]


def test_the_replay_site_shows_the_numbers_the_readme_quotes(tmp_path):
    from doppel import site
    grouping = ROOT / "bench" / "results" / "guided-rerun-grouping"
    if not (grouping / "r17-hash-leak.json").exists():
        return   # the recorded benchmark results aren't in this checkout
    s = site.bench_scores(ROOT / "bench" / "results", guided=grouping)
    assert {k: (v["caught"], v.get("alarms")) for k, v in s.items()} == {
        "tests": (12, 0), "review": (17, 1), "doppel_guided": (17, 2), "tests_plus_guided": (20, None)}
    cfg = json.loads((ROOT / "site" / "gallery.json").read_text(encoding="utf-8"))
    assert cfg["bench_guided"] == "bench/results/guided-rerun-grouping"
    chart = (ROOT / cfg["chart"]).read_text(encoding="utf-8")
    assert ">17/20<" in chart and ">2/10<" in chart and ">16/20<" not in chart


def test_a_pr_that_edits_the_front_door_seed_gets_the_setup_warning():
    assert "doppel_seed.json" in cli.SETUP_FILES
