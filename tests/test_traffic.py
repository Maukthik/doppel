"""Test mode: the app's own tests as the users, in three worlds (base, base again, head)."""
import json
import textwrap
from pathlib import Path

import pytest

from doppel import traffic
from doppel.traffic import NotComparable, check, detect

FLASK_APP = '''
import itertools, random
from flask import Flask, jsonify, request
app = Flask(__name__)
USERS = {1: {"id": 1, "name": "alice", "secret": "s3cret"}}
COUNTER = itertools.count()

@app.get("/users/<int:uid>")
def user(uid):
    u = USERS.get(uid)
    if not u:
        return jsonify(error="not found"), 404
    return jsonify({"id": u["id"], "name": u["name"], "nonce": random.random()})

@app.post("/orders")
def order():
    qty = int(request.get_json().get("qty", 0))
    if qty <= 0:
        return jsonify(error="qty must be at least 1"), 400
    return jsonify(qty=qty, total=qty * 10), 201
'''
FLASK_TESTS = '''
from app import app

def client():
    return app.test_client()

def test_user():
    rv = client().get("/users/1")
    assert rv.status_code == 200          # never looks at the body

def test_missing_user():
    assert client().get("/users/9").status_code == 404

def test_order():
    rv = client().post("/orders", json={"qty": 2})
    assert rv.status_code == 201

def test_bad_order():
    client().post("/orders", json={"qty": 0})   # asserts nothing at all
'''


def flask_repo(root: Path, app_src: str = FLASK_APP, tests: str = FLASK_TESTS) -> Path:
    pytest.importorskip("flask")
    root.mkdir(parents=True, exist_ok=True)
    (root / "app.py").write_text(textwrap.dedent(app_src), encoding="utf-8")
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests" / "test_app.py").write_text(textwrap.dedent(tests), encoding="utf-8")
    (root / "conftest.py").write_text("import sys, os\nsys.path.insert(0, os.path.dirname(__file__))\n",
                                      encoding="utf-8")
    return root


def pair(tmp_path: Path, head_app: str = FLASK_APP, head_tests: str = FLASK_TESTS):
    return flask_repo(tmp_path / "base"), flask_repo(tmp_path / "head", head_app, head_tests)


# ---- the base must run, and noise must be measured -----------------------------------------------

def test_same_code_twice_is_quiet_and_noise_is_found(tmp_path):
    base, head = pair(tmp_path)
    r = check(base, head, backend="local")
    assert r["findings"] == [] and r["stats"]["passed"] == 4 and r["stats"]["recorded_requests"] == 4
    assert "nonce" in r["noise_keys"]                    # random on every run: found by base vs base again


def test_a_leak_the_assertions_never_look_at_is_caught(tmp_path):
    leaky = FLASK_APP.replace('{"id": u["id"], "name": u["name"],', '{"id": u["id"], "name": u["name"], '
                                                                     '"secret": u["secret"],')
    base, head = pair(tmp_path, leaky)
    r = check(base, head, backend="local")
    found = [(f.kind, f.request, f.detail) for f in r["findings"]]
    assert found == [("body", "GET /users/1", "changed: secret (added)")]
    assert r["findings"][0].head == {"secret": "s3cret"}


def test_lost_validation_shows_as_a_status_change_while_every_test_still_passes(tmp_path):
    base, head = pair(tmp_path, FLASK_APP.replace("if qty <= 0:", "if qty < 0:"))
    r = check(base, head, backend="local")
    assert [(f.kind, f.detail) for f in r["findings"]] == [("status", "status 400 -> 201")]


def test_a_test_that_starts_failing_is_reported(tmp_path):
    base, head = pair(tmp_path, FLASK_APP.replace("), 201", "), 200"))
    kinds = {(f.kind, f.detail) for f in check(base, head, backend="local")["findings"]}
    assert ("test", "the test passed on base and fails on head") in kinds and ("status", "status 201 -> 200") in kinds


def test_the_head_world_runs_the_base_tests_so_a_pr_cannot_hide_a_change(tmp_path):
    hidden = FLASK_TESTS.replace('rv = client().post("/orders", json={"qty": 2})\n    assert rv.status_code == 201',
                                 'pass')
    base, head = pair(tmp_path, FLASK_APP.replace("), 201", "), 200"), hidden)
    r = check(base, head, backend="local")
    assert any(f.kind == "test" for f in r["findings"])   # the base's test still ran against the head code


def test_no_tests_is_not_comparable(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(NotComparable, match="no test suite found"):
        check(tmp_path, tmp_path, backend="local")


def test_a_broken_environment_never_gives_a_verdict(tmp_path):
    base, head = pair(tmp_path)
    for root in (base, head):
        (root / "tests" / "test_app.py").write_text(FLASK_TESTS.replace("from app import app", "from app import app\n"
                                                                        "import missing_service_client"))
    with pytest.raises(NotComparable, match="ran no test"):
        check(base, head, backend="local")


def test_most_tests_failing_on_base_stops_the_check(tmp_path):
    failing = FLASK_TESTS.replace("== 200", "== 999").replace("== 404", "== 999").replace("== 201", "== 999")
    base, head = flask_repo(tmp_path / "b", tests=failing), flask_repo(tmp_path / "h", tests=failing)
    with pytest.raises(NotComparable, match="fail on the unchanged code"):
        check(base, head, backend="local")


def test_tests_that_send_no_requests_are_said_plainly(tmp_path):
    plain = "def test_math():\n    assert 1 + 1 == 2\n"
    base, head = flask_repo(tmp_path / "b", tests=plain), flask_repo(tmp_path / "h", tests=plain)
    with pytest.raises(NotComparable, match="sent no request through a test client"):
        check(base, head, backend="local")


def test_flaky_tests_and_varying_requests_are_left_out():
    def world(outcomes, sigs):
        return {"tests": {t: {"outcome": o, "requests": [{"sig": s, "status": 200, "body": {}} for s in sigs[t]]}
                          for t, o in outcomes.items()}, "exit": "0", "output": "", "recorder_errors": ""}
    sigs = {"t1": ["GET /a"], "t2": ["GET /b"], "t3": ["GET /c"]}
    a = world({"t1": "passed", "t2": "passed", "t3": "passed"}, sigs)
    a2 = world({"t1": "failed", "t2": "passed", "t3": "passed"}, {**sigs, "t3": ["GET /c", "GET /c"]})
    nz = traffic.noise(a, a2)
    assert nz["flaky"] == {"t1"} and nz["unstable"] == {"t3"}


# ---- the recorder ---------------------------------------------------------------------------------

def test_the_recorder_never_breaks_a_test(tmp_path, monkeypatch):
    base, head = pair(tmp_path)
    original = traffic.hook_files

    def broken_recorder():
        files = original()
        boom = b"def record(method, *a, **k):\n    raise RuntimeError('boom')\n\n\ndef _unused(method"
        files["doppel_traffic.py"] = files["doppel_traffic.py"].replace(b"def record(method", boom)
        return files
    monkeypatch.setattr(traffic, "hook_files", broken_recorder)
    with pytest.raises(NotComparable, match="sent no request"):   # nothing recorded, but every test passed
        check(base, head, backend="local")


def test_an_existing_sitecustomize_still_runs(tmp_path, monkeypatch):
    # Doppel's sitecustomize comes first on PYTHONPATH and would shadow one the environment already has (in
    # site-packages or on PYTHONPATH): it must hand over to it
    site = tmp_path / "site"
    site.mkdir()
    (site / "sitecustomize.py").write_text("import os\nos.environ['PROJECT_SITE'] = 'yes'\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(site))
    base, head = pair(tmp_path)
    for root in (base, head):
        (root / "tests" / "test_site.py").write_text(
            "import os\nfrom app import app\n\ndef test_site():\n    assert os.environ.get('PROJECT_SITE') == 'yes'\n"
            "    app.test_client().get('/users/1')\n", encoding="utf-8")
    r = check(base, head, backend="local", test="python -m pytest -q")
    assert r["stats"]["passed"] == 5 and not r["excluded"]


def test_django_and_drf_clients_are_recorded(tmp_path):
    pytest.importorskip("django")
    for name in ("b", "h"):
        root = tmp_path / name
        (root / "proj").mkdir(parents=True)
        (root / "proj" / "__init__.py").write_text("")
        (root / "proj" / "settings.py").write_text(textwrap.dedent('''
            SECRET_KEY = "x"; ROOT_URLCONF = "proj.urls"; ALLOWED_HOSTS = ["*"]
            INSTALLED_APPS = ["django.contrib.contenttypes", "django.contrib.auth"]
            DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
            '''))
        version = "2" if name == "h" else "1"
        (root / "proj" / "urls.py").write_text(textwrap.dedent(f'''
            from django.http import JsonResponse
            from django.urls import path
            urlpatterns = [path("api/", lambda r: JsonResponse({{"version": {version}}}))]
            '''))
        (root / "proj" / "tests.py").write_text(textwrap.dedent('''
            from django.test import SimpleTestCase
            class Api(SimpleTestCase):
                def test_api(self):
                    self.assertEqual(self.client.get("/api/").status_code, 200)
            '''))
        (root / "manage.py").write_text(textwrap.dedent('''
            import os, sys
            os.environ.setdefault("DJANGO_SETTINGS_MODULE", "proj.settings")
            from django.core.management import execute_from_command_line
            execute_from_command_line(sys.argv)
            '''))
    r = check(tmp_path / "b", tmp_path / "h", backend="local")
    assert r["test"] == "python manage.py test"                          # Django's own runner, not pytest
    assert [(f.request, f.detail) for f in r["findings"]] == [("GET /api/", "changed: version")]


def test_fastapi_clients_are_recorded(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    for name, limit in (("b", 10), ("h", 5)):
        root = tmp_path / name
        root.mkdir()
        (root / "main.py").write_text(f"from fastapi import FastAPI\napp = FastAPI()\n\n@app.get('/items')\n"
                                      f"def items():\n    return {{'items': list(range({limit}))}}\n")
        (root / "test_main.py").write_text("from fastapi.testclient import TestClient\nfrom main import app\n\n"
                                           "def test_items():\n    assert TestClient(app).get('/items').status_code"
                                           " == 200\n")
    r = check(tmp_path / "b", tmp_path / "h", backend="local")
    assert [f.request for f in r["findings"]] == ["GET /items"] and "items" in r["findings"][0].detail


# ---- reading the repo's own truth ------------------------------------------------------------------

def test_detect_reads_tox_then_ci_then_layout(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_x(): pass\n")
    assert detect(tmp_path)["test"] == "python -m pytest"
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text("jobs:\n  t:\n    steps:\n      - run: pip install -r requirements.txt\n"
                               "      - run: |\n          pytest -q --cov=app -n auto tests\n")
    found = detect(tmp_path)
    assert found["test"] == "python -m pytest -q tests" and found["source"]["test"] == "CI workflow"
    assert found["install"].startswith("pip install --quiet -r requirements.txt")
    (tmp_path / "tox.ini").write_text("[testenv]\ndeps = -r requirements-dev.txt\ncommands = pytest -p no:logging "
                                      "--cov=api\n")
    found = detect(tmp_path)
    assert found["test"] == "python -m pytest -p no:logging" and found["source"]["test"] == "tox.ini"
    assert "-r requirements-dev.txt" in found["install"]
    (tmp_path / "doppel.toml").write_text('[tests]\ncommand = "make test"\n')
    assert detect(tmp_path)["test"] == "make test"


def test_the_report_names_tests_and_what_was_left_out(tmp_path):
    leaky = FLASK_APP.replace('"name": u["name"],', '"name": u["name"], "secret": u["secret"],')
    base, head = pair(tmp_path, leaky)
    md = traffic.report(check(base, head, backend="local"))
    assert "| | Test |" in md and "test_app.py::test_user (request 0)" in md
    assert "of the app's own tests sent" in md and "`nonce`" in md


def test_cli_exit_codes(tmp_path):
    from doppel import cli
    base, head = pair(tmp_path, FLASK_APP.replace("if qty <= 0:", "if qty < 0:"))
    out = tmp_path / "r.json"
    assert cli.main(["tests", "--base", str(base), "--head", str(head), "--backend", "local", "--strict",
                     "--json", str(out)]) == 1
    assert json.loads(out.read_text())["mode"] == "tests"
    same = flask_repo(tmp_path / "same")
    assert cli.main(["tests", "--base", str(same), "--head", str(same), "--backend", "local", "--strict"]) == 0
    (tmp_path / "empty").mkdir()
    assert cli.main(["tests", "--base", str(tmp_path / "empty"), "--head", str(tmp_path / "empty"),
                     "--backend", "local"]) == 2


def test_a_parent_test_run_never_leaks_into_the_worlds():
    env = traffic.world_env("/h", "/o", {"PYTEST_CURRENT_TEST": "outer.py::t (call)", "PYTEST_ADDOPTS": "-x",
                                         "PATH": "/bin", "PYTHONHASHSEED": "7"})
    assert "PYTEST_CURRENT_TEST" not in env and env["PYTEST_ADDOPTS"] == "-p no:randomly -p no:cacheprovider"
    assert env["PATH"] == "/bin" and env["PYTHONHASHSEED"] == "0"
