"""Seven gaps found while documenting the code (17 Oct), each pinned by a test."""
import json

from doppel import agent, bootstrap, cli, viewer
from doppel.diff import Finding
from doppel.judge import Verdict, group, regression_tests
from doppel.setup_plan import plan, tox_substitute
from doppel.traffic import world_env
from tests.test_traffic import FLASK_APP, flask_repo

SHOP = cli.Path(__file__).resolve().parent.parent / "examples" / "shop"


def test_tox_substitutions_are_expanded_as_tox_would_with_no_arguments(tmp_path):
    assert tox_substitute("pytest {posargs:tests} -q") == "pytest tests -q"
    assert tox_substitute("pytest {posargs}") == "pytest"
    assert tox_substitute("-r{toxinidir}/requirements.txt") == "-r./requirements.txt"
    assert tox_substitute("{envpython} -m pytest") == "python -m pytest"
    (tmp_path / "tox.ini").write_text("[testenv]\ndeps = -r{toxinidir}/requirements.txt\n"
                                      "commands = pytest {posargs} -q\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("flask\n", encoding="utf-8")
    p = plan(tmp_path)
    assert "{" not in p.test and p.test.endswith("pytest -q")
    assert not any("{" in step for step in p.install)


def test_the_project_env_cannot_turn_random_order_or_hash_randomization_back_on():
    env = world_env("/hook", "/out", {}, ":", extra={"PYTEST_ADDOPTS": "-x", "PYTHONHASHSEED": "random",
                                                     "TZ": "Asia/Kolkata", "APP_MODE": "test"})
    assert env["PYTEST_ADDOPTS"] == "-p no:randomly -p no:cacheprovider -x"   # added to, never replaced
    assert env["PYTHONHASHSEED"] == "0"                                      # "random" would differ per world
    assert env["TZ"] == "Asia/Kolkata" and env["APP_MODE"] == "test"         # same in every world: kept
    assert world_env("/hook", "/out", {}, ":", extra={"PYTHONHASHSEED": "42"})["PYTHONHASHSEED"] == "42"


def test_a_crash_on_one_route_and_a_crash_on_another_are_judged_separately():
    crashes = [Finding("status", "a", 0, "POST /orders", "status 200 -> 500", 200, 500),
               Finding("status", "b", 0, "GET /users/7", "status 200 -> 500", 200, 500),
               Finding("status", "c", 1, "GET /users/9", "status 200 -> 500", 200, 500)]
    leak = [Finding("body", "a", 1, "GET /api/users", "changed: *.password_hash (added) x3", {}, {}),
            Finding("body", "b", 2, "GET /api/me", "changed: password_hash (added)", {}, {})]
    groups = group(crashes + leak)
    assert sorted(groups.values()) == [[0], [1, 2], [3, 4]]     # /users/7 and /users/9 are one route


def test_a_regression_test_for_an_added_field_asserts_the_field_is_absent():
    f = Finding("body", "reader", 0, "GET /api/users", "changed: data.0.password_hash (added)",
                {"data.0.password_hash": "(absent)"}, {"data.0.password_hash": "scrypt:..."})
    persona = {"name": "reader", "steps": [{"method": "GET", "path": "/api/users"}]}
    text = regression_tests([f], [Verdict("regression", "leaks the hash")], [persona])
    assert "assert extract(step['body'], 'data.0.password_hash') is None" in text
    assert "(absent)" not in text
    compile(text, "test_doppel_regressions.py", "exec")


def test_a_regression_the_twin_confirmed_gets_its_regression_test(tmp_path, monkeypatch):
    import doppel.personas
    import doppel.suspects
    monkeypatch.setattr(doppel.suspects, "suspect", lambda *a, **k: {"suspicions": [
        {"id": "s1", "claim": "coupon orders cost more", "route": "POST /orders", "trigger": "", "inputs": []}]})
    monkeypatch.setattr(doppel.personas, "generate", lambda *a, **k: {"personas": []})
    monkeypatch.setattr(doppel.suspects, "check", lambda *a, **k: {"checks": {
        "s1": {"status": "confirmed", "evidence": "seen", "changes": ["f1"]}}})

    def unsure(model, prompt, temperature=0.7):   # the judge leaves every change to a human
        return json.dumps({"verdicts": [], "checks": []}), 1, 1
    monkeypatch.setattr("doppel.judge.judge.__defaults__", (None, unsure))
    tests = tmp_path / "test_reg.py"
    cli.main(["run", "--base", str(SHOP / "base"), "--head", str(SHOP / "head"), "--personas",
              str(SHOP / "personas.json"), "--pr", str(SHOP / "PR.md"), "--guided", "--tests-out", str(tests)])
    assert tests.read_text(encoding="utf-8").count("\ndef test_") == 1   # escalated before the tests were written


def test_the_seed_check_reports_a_placeholder_path_once(tmp_path):
    problems = bootstrap.check_seed(tmp_path, "open('<repo>/data.db')\n")
    assert sum("<repo>" in p for p in problems) == 1


def test_the_agent_reads_what_its_shell_wrote_and_may_reread_after_a_change(tmp_path):
    repo = flask_repo(tmp_path / "repo", FLASK_APP)
    ws = agent.LocalWorkspace(repo)
    try:
        ws.shell("echo made-in-the-sandbox > notes.txt")
        assert "made-in-the-sandbox" in ws.read("notes.txt")
        assert not (repo / "notes.txt").exists()                     # it was never in the local repo
        assert ws.read("../outside").startswith("no such file or folder")
    finally:
        ws.close()
    assert agent._path_key("./.env") == ".env" != agent._path_key("env")
    assert agent._path_key("a/../app.py") == agent._path_key("app.py") == "app.py"

    actions = iter([{"tool": "read", "path": "app.py"}, {"tool": "read", "path": "./app.py"},
                    {"tool": "shell", "command": "echo changed >> app.py"}, {"tool": "read", "path": "app.py"},
                    {"tool": "give_up", "reason": "test"}])
    prompts = []

    def chat(model, prompt, temperature=0.7):
        prompts.append(prompt)
        return json.dumps(next(actions)), 1, 1
    agent.fix(repo, "local", "", [], None, "boom", lambda files: {"ok": False}, chat)
    assert "You already read ./app.py at step 1" in prompts[2]       # same file, other spelling: refused
    assert prompts[4].count("You already read") == 1 and "changed" in prompts[4]   # after the shell: read again


def _trace_with_agent(root, works=True):
    attempt = root / "attempt-1"
    attempt.mkdir(parents=True)
    (attempt / "4-boot.txt").write_text("outcome: failed\nerror sent back to the model:\nboom\n", encoding="utf-8")
    steps = "\n\n".join(f"=== step {k}\n{{\"tool\": \"shell\"}}\n--- result\nexit 0" for k in (1, 2, 3))
    (root / "agent.txt").write_text(steps, encoding="utf-8")
    line = "agent: WORKS after 3 step(s), 80% served" if works else "agent: did not get it working in 3 step(s)"
    (root / "summary.txt").write_text(f"attempt 1: reached start: boom\n{line}\n", encoding="utf-8")
    (root / "6-calibration.txt").write_text("requests served: 80%\n", encoding="utf-8")
    return root


def test_an_init_the_setup_agent_finished_shows_as_working(tmp_path):
    data = viewer.load_trace(_trace_with_agent(tmp_path / "app" / ".doppel-trace"))
    first, last = data["attempts"]
    assert first["status"] == "kept" and last["agent"] and last["status"] == "works"
    assert data["outcome"] == 2 and "3 steps" in last["decision"] and last["calibration"]
    page = viewer.render(data)
    assert "Setup agent" in page and "the setup agent finished it" in page
    failed = viewer.load_trace(_trace_with_agent(tmp_path / "other" / ".doppel-trace", works=False))
    assert failed["outcome"] is None and failed["attempts"][-1]["status"] == "agent"
