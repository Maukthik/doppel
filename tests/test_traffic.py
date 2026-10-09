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


def test_a_test_that_starts_failing_is_attached_to_the_change_that_broke_it(tmp_path):
    # one change, judged once: the status change carries the failing test as evidence (s03 and s07 in the
    # benchmark were false alarms because the failure was judged as a separate, unannounced change)
    base, head = pair(tmp_path, FLASK_APP.replace("), 201", "), 200"))
    found = [(f.kind, f.detail) for f in check(base, head, backend="local")["findings"]]
    assert found == [("status", "status 201 -> 200 (the base branch's test fails on this)")]


STOPS_EARLY = FLASK_TESTS.replace(
    "    assert rv.status_code == 201\n",
    "    assert rv.status_code == 201\n"
    "    client().get(\"/users/1\")   # never sent on head: the assertion stops the test\n")


def test_requests_missing_only_because_the_test_stopped_are_not_findings(tmp_path):
    base = flask_repo(tmp_path / "b", tests=STOPS_EARLY)
    head = flask_repo(tmp_path / "h", FLASK_APP.replace("), 201", "), 200"), STOPS_EARLY)
    found = [(f.kind, f.request) for f in check(base, head, backend="local")["findings"]]
    assert found == [("status", "POST /orders")]


CONFIG_TEST = FLASK_TESTS + (
    "\n\ndef test_config():\n    import app as module\n"
    "    assert getattr(module, 'VERSION', 1) == 1, 'VERSION must stay 1'\n    client().get('/users/9')\n")


def test_a_failure_no_response_explains_keeps_its_own_message(tmp_path):
    base = flask_repo(tmp_path / "b", tests=CONFIG_TEST)
    head = flask_repo(tmp_path / "h", FLASK_APP + "\nVERSION = 2\n", CONFIG_TEST)
    [f] = check(base, head, backend="local")["findings"]
    assert f.kind == "test" and "VERSION must stay 1" in f.detail


def test_the_head_world_runs_the_base_tests_so_a_pr_cannot_hide_a_change(tmp_path):
    hidden = FLASK_TESTS.replace('rv = client().post("/orders", json={"qty": 2})\n    assert rv.status_code == 201',
                                 'pass')
    base, head = pair(tmp_path, FLASK_APP.replace("), 201", "), 200"), hidden)
    r = check(base, head, backend="local")
    assert any("test fails on this" in f.detail for f in r["findings"])   # the base's test still ran on head


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


# ---- gaps closed: async clients, live servers, detection, large suites, confirmation ----------

def _two(tmp_path, files_base: dict, files_head: dict):
    for name, files in (("b", files_base), ("h", files_head)):
        root = tmp_path / name
        for rel, text in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(textwrap.dedent(text), encoding="utf-8")
    return tmp_path / "b", tmp_path / "h"


ASYNC_FASTAPI = '''
from fastapi import FastAPI
app = FastAPI()

@app.get("/items/{n}")
async def item(n: int):
    return {"n": n, "price": n * PRICE}
'''
ASYNC_TEST = '''
import asyncio
import httpx
from main import app

def test_items_concurrently():
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            return await asyncio.gather(*(c.get(f"/items/{i}") for i in range(3)))
    assert all(r.status_code == 200 for r in asyncio.run(go()))
'''


def test_fastapi_async_tests_through_httpx_are_recorded_one_request_each(tmp_path):
    pytest.importorskip("fastapi")
    base, head = _two(tmp_path, {"main.py": "PRICE = 10\n" + ASYNC_FASTAPI, "test_main.py": ASYNC_TEST},
                      {"main.py": "PRICE = 12\n" + ASYNC_FASTAPI, "test_main.py": ASYNC_TEST})
    r = check(base, head, backend="local")
    assert r["stats"]["recorded_requests"] == 3                       # concurrent requests: each recorded once
    assert sorted(f.request for f in r["findings"]) == ["GET /items/1", "GET /items/2"]   # 0 * price is 0


def test_django_async_client_is_recorded(tmp_path):
    pytest.importorskip("django")
    common = {"proj/__init__.py": "", "proj/settings.py": '''
                SECRET_KEY = "x"; ROOT_URLCONF = "proj.urls"; ALLOWED_HOSTS = ["*"]
                INSTALLED_APPS = ["django.contrib.contenttypes", "django.contrib.auth"]
                DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
                ''',
              "proj/tests.py": '''
                from django.test import SimpleTestCase
                class Api(SimpleTestCase):
                    async def test_api(self):
                        response = await self.async_client.get("/api/")
                        self.assertEqual(response.status_code, 200)
                ''',
              "manage.py": '''
                import os, sys
                os.environ.setdefault("DJANGO_SETTINGS_MODULE", "proj.settings")
                from django.core.management import execute_from_command_line
                execute_from_command_line(sys.argv)
                '''}
    urls = ('from django.http import JsonResponse\nfrom django.urls import path\n'
            'urlpatterns = [path("api/", lambda r: JsonResponse({"v": %d}))]\n')
    base, head = _two(tmp_path, {**common, "proj/urls.py": urls % 1}, {**common, "proj/urls.py": urls % 2})
    r = check(base, head, backend="local")
    assert [(f.request, f.detail) for f in r["findings"]] == [("GET /api/", "changed: v")]


LIVE_TEST = '''
import threading
import httpx, requests
from werkzeug.serving import make_server
from app import app

def test_live_server():
    server = make_server("127.0.0.1", 0, app)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.port}"
    try:
        assert requests.get(url + "/users/1").status_code == 200
        assert httpx.get(url + "/users/9").status_code == 404
    finally:
        server.shutdown()
'''


def test_live_server_tests_are_recorded_through_requests_and_httpx(tmp_path):
    leaky = FLASK_APP.replace('"name": u["name"],', '"name": u["name"], "secret": u["secret"],')
    base = flask_repo(tmp_path / "b", tests=LIVE_TEST)
    head = flask_repo(tmp_path / "h", leaky, LIVE_TEST)
    r = check(base, head, backend="local")
    assert r["stats"]["recorded_requests"] == 2
    assert [(f.request, f.detail) for f in r["findings"]] == [("GET /users/1", "changed: secret (added)")]


def test_only_local_destinations_count_as_the_app():
    import importlib.util
    spec = importlib.util.spec_from_file_location("rec", Path(traffic.RECORDER))
    rec = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rec)
    assert all(rec._is_local(h) for h in ("localhost", "127.0.0.1", "[::1]", "0.0.0.0", "testserver"))
    assert not any(rec._is_local(h) for h in ("api.stripe.com", "example.org", "10.0.0.5"))


def test_makefile_noxfile_and_subfolder_detection(tmp_path):
    (tmp_path / "Makefile").write_text("lint:\n\tflake8\n\ntest:\n\t@pytest -q tests --cov=x\n", encoding="utf-8")
    found = detect(tmp_path)
    assert found["test"] == "python -m pytest -q tests" and found["source"]["test"] == "Makefile"
    (tmp_path / "Makefile").unlink()
    (tmp_path / "noxfile.py").write_text('@nox.session\ndef tests(session):\n    session.run("pytest", "-x", '
                                         '"tests")\n', encoding="utf-8")
    assert detect(tmp_path)["test"] == "python -m pytest -x tests"
    mono = tmp_path / "mono"
    (mono / "services" / "api").mkdir(parents=True)
    (mono / "services" / "api" / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    with pytest.raises(NotComparable, match="services/api"):
        check(mono, mono, backend="local")


def test_a_large_suite_is_split_by_file_and_merged_back(tmp_path):
    many = {f"tests/test_m{i:02d}.py": f"from app import app\n\ndef test_{i}():\n"
                                       f"    assert app.test_client().get('/users/1').status_code == 200\n"
            for i in range(traffic.SHARD_FILES + 5)}
    base = flask_repo(tmp_path / "b", tests="def test_x():\n    pass\n")
    for rel, text in many.items():
        (base / rel).write_text(text, encoding="utf-8")
    plan = traffic.shards(base, "python -m pytest -q")
    assert len(plan) == 2 and all("tests/test_m" in cmd for cmd in plan)
    r = check(base, base, backend="local")
    assert r["stats"]["passed"] == traffic.SHARD_FILES + 6 and r["findings"] == []


def test_a_difference_that_does_not_happen_again_is_dropped(tmp_path, monkeypatch):
    flag = tmp_path / "rare-seen"
    monkeypatch.setenv("RARE_FLAG", str(flag))
    rare = FLASK_APP.replace('return jsonify({"id": u["id"], "name": u["name"], "nonce": random.random()})',
                             'import os\n    once = RARE and not os.path.exists(os.environ["RARE_FLAG"])\n'
                             '    if once:\n        open(os.environ["RARE_FLAG"], "w").close()\n'
                             '    return jsonify({"id": u["id"], "name": "glitch" if once else u["name"], '
                             '"nonce": random.random()})')
    base = flask_repo(tmp_path / "b", rare.replace("app = Flask(__name__)", "app = Flask(__name__)\nRARE = False"))
    head = flask_repo(tmp_path / "h", rare.replace("app = Flask(__name__)", "app = Flask(__name__)\nRARE = True"))
    r = check(base, head, backend="local")
    assert r["findings"] == [] and r["stats"]["confirmation"] == {"ran": True, "tests": 1, "dropped": 1}


def test_a_real_difference_survives_confirmation(tmp_path):
    base, head = pair(tmp_path, FLASK_APP.replace("if qty <= 0:", "if qty < 0:"))
    r = check(base, head, backend="local")
    assert len(r["findings"]) == 1 and r["stats"]["confirmation"]["ran"] and not r["stats"]["confirmation"]["dropped"]


def test_confirm_commands_select_single_tests(tmp_path):
    (tmp_path / "tests").mkdir()
    assert traffic.confirm_command(tmp_path, "python -m pytest -p no:logging tests -q", ["tests/a.py::t"]) == \
        "python -m pytest -p no:logging -q tests/a.py::t"
    assert traffic.confirm_command(tmp_path, "python manage.py test blog", ["blog.tests.T.test_a"]) == \
        "python manage.py test blog.tests.T.test_a"
    assert traffic.confirm_command(tmp_path, "make test", ["x"]) == ""
