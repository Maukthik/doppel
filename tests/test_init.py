"""doppel init, offline: a scripted stand-in for Nemotron and the local backend.

The loop must: turn a model reply into files, boot the twin, feed a failure back, accept the
repaired draft, and learn noisy keys by replaying the same code twice.
"""
import json
import shutil
import textwrap
import tomllib
from pathlib import Path

from doppel import bootstrap
from doppel.spec import TwinSpec

SHOP = Path(__file__).resolve().parent.parent / "examples" / "shop"
SHOP_SPEC = (SHOP / "base" / "doppel.toml").read_text(encoding="utf-8")
SHOP_PERSONAS = json.loads((SHOP / "personas.json").read_text(encoding="utf-8"))


def reply(spec_text: str, seed: str | None = None, notes: str = "") -> str:
    files = [{"path": "doppel.toml", "content": spec_text}]
    if seed is not None:
        files.append({"path": "doppel_seed.py", "content": seed})
    return json.dumps({"files": files, "notes": notes})


class ScriptedModel:
    """Answers setup prompts from a script, persona prompts with fixed users; records every prompt."""

    def __init__(self, setups: list[str], personas=SHOP_PERSONAS[:3]):
        self.setups, self.personas, self.prompts = list(setups), personas, []

    def __call__(self, model, prompt, temperature=0.7):
        self.prompts.append(prompt)
        if prompt.startswith("You set up a web API"):
            return self.setups.pop(0), 100, 100
        return json.dumps({"personas": self.personas}), 100, 100


def shop_without_spec(tmp_path: Path) -> Path:
    app = tmp_path / "shop"
    shutil.copytree(SHOP / "base", app)
    (app / "doppel.toml").unlink()
    return app


# --- the loop ---------------------------------------------------------------------


def test_init_repairs_a_broken_draft_and_returns_working_files(tmp_path):
    app = shop_without_spec(tmp_path)
    broken = SHOP_SPEC.replace('start = "python app.py"', 'start = "python missing.py"')
    model = ScriptedModel([reply(broken), reply(SHOP_SPEC, notes="SQLite, seeded by seed.py")])
    result = bootstrap.init(app, backend="local", tries=3, chat=model, log=lambda m: None)
    assert result["ok"] and result["attempts"] == 2
    assert "missing.py" in model.prompts[1] and "FAILED" in model.prompts[1]   # the error went back
    assert 'start = "python app.py"' in result["files"]["doppel.toml"]
    assert result["personas"] == 3 and result["coverage"] > 0.5
    assert result["noise"] == [] and result["flaky"] == []   # the shop has no timestamps
    assert not (app / "doppel.toml").exists()                 # init itself writes nothing


def test_init_gives_up_after_its_tries_and_keeps_the_last_error(tmp_path):
    app = shop_without_spec(tmp_path)
    broken = reply(SHOP_SPEC.replace('ready = "/health"', 'ready = "/health"\nstart = "x"'))  # invalid TOML
    model = ScriptedModel([broken, broken])
    result = bootstrap.init(app, backend="local", tries=2, chat=model, log=lambda m: None)
    assert not result["ok"] and result["attempts"] == 2 and "not valid TOML" in result["error"]


def test_a_cut_off_reply_is_asked_again_without_spending_an_attempt(tmp_path):
    app = shop_without_spec(tmp_path)
    model = ScriptedModel(["sorry, I cannot help", reply(SHOP_SPEC)])
    result = bootstrap.init(app, backend="local", tries=2, chat=model, log=lambda m: None)
    assert result["ok"] and result["attempts"] == 1


def test_a_network_failure_stops_the_loop_instead_of_redrafting(tmp_path):
    class ApiTimeoutError(Exception):
        pass

    def flaky_api(*args, **kwargs):
        raise ApiTimeoutError("POST /sandboxes/v1/files timed out")

    app = shop_without_spec(tmp_path)
    model = ScriptedModel([reply(SHOP_SPEC), reply(SHOP_SPEC)])
    result = bootstrap.init(app, tries=3, chat=model, run_twin=flaky_api, log=lambda m: None)
    assert not result["ok"] and result["infra"] and result["attempts"] == 1
    assert len(model.prompts) == 1                     # one draft, not three
    assert "doppel.toml" in result["files"]            # the draft is kept for --keep-draft


def test_a_hanging_seed_is_the_setup_s_fault_not_the_network_s():
    import subprocess
    assert not bootstrap._infrastructure(subprocess.TimeoutExpired("python seed.py", 600))
    assert not bootstrap._infrastructure(RuntimeError("app never became ready"))


def test_the_cause_at_the_bottom_of_a_long_error_reaches_the_model(tmp_path):
    app = shop_without_spec(tmp_path)
    error = "install (base) failed (uv sync --frozen):\n" + "  File x.py, line 1\n" * 2000 + \
        "Exception: you need a C compiler to build uWSGI"
    model = ScriptedModel([reply(SHOP_SPEC)])
    bootstrap.draft(app, error=error, previous={"doppel.toml": "x"}, chat=model)
    assert "you need a C compiler to build uWSGI" in model.prompts[0]
    assert "uv sync --frozen" in model.prompts[0]          # and the command that failed


def test_later_drafts_see_every_earlier_failure_not_just_the_last(tmp_path):
    app = shop_without_spec(tmp_path)
    bad = [SHOP_SPEC.replace('start = "python app.py"', f'start = "python missing{i}.py"') for i in (1, 2)]
    model = ScriptedModel([reply(bad[0]), reply(bad[1]), reply(SHOP_SPEC)])
    result = bootstrap.init(app, backend="local", tries=3, chat=model, log=lambda m: None)
    assert result["ok"] and result["attempts"] == 3
    third = model.prompts[-2]                     # the last setup prompt (the final one is personas)
    assert "Earlier attempts" in third and "attempt 1:" in third and "missing1.py" in third
    assert "missing2.py" in third                 # the last attempt's full error and files
    assert "Earlier attempts" not in model.prompts[1]   # nothing earlier than the last on attempt 2


def test_errors_come_with_the_real_source_they_point_at(tmp_path):
    (tmp_path / "shop" / "services").mkdir(parents=True)
    (tmp_path / "shop" / "services" / "orders.py").write_text(
        "import os\n\n\ndef create_order(order, user, notify=False):\n    return order\n")
    (tmp_path / "shop" / "app.py").write_text("\n".join(f"line{i}" for i in range(1, 41)))
    error = ('Traceback (most recent call last):\n'
             '  File "/app/doppel_seed.py", line 9, in <module>\n'
             '  File "/app/shop/app.py", line 20, in boot\n'
             "TypeError: create_order() got an unexpected keyword argument 'total'")
    src = bootstrap.related_source(tmp_path, error)
    assert "def create_order(order, user, notify=False)" in src       # the definition it got wrong
    assert "shop/app.py (around line 20)" in src and "line20" in src    # the repo frame
    assert "doppel_seed.py" not in src                                  # the model's own file is shown anyway
    assert bootstrap.related_source(tmp_path, "ValueError: nothing to look up") == ""


def test_the_same_error_twice_is_called_out(tmp_path):
    app = shop_without_spec(tmp_path)
    broken = SHOP_SPEC.replace('start = "python app.py"', 'start = "python missing.py"')
    model = ScriptedModel([reply(broken), reply(broken), reply(SHOP_SPEC)])
    result = bootstrap.init(app, backend="local", tries=3, chat=model, log=lambda m: None)
    assert result["ok"]
    assert "SAME error as attempt 1" in model.prompts[2]
    assert "SAME error" not in model.prompts[1]


def test_draft_keeps_only_the_two_allowed_files(tmp_path):
    app = shop_without_spec(tmp_path)
    text = json.dumps({"files": [{"path": "doppel.toml", "content": SHOP_SPEC},
                                 {"path": "doppel_seed.py", "content": "print('seed')"},
                                 {"path": "../../etc/evil", "content": "x"}]})
    out = bootstrap.draft(app, chat=ScriptedModel([text]))
    assert set(out["files"]) == {"doppel.toml", "doppel_seed.py"}


# --- calibration: the same code twice --------------------------------------------

CLOCK_APP = textwrap.dedent('''
    import json, os, time, uuid
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = {"items": [{"id": 1, "name": "lamp", "fetched_at": time.time()}],
                    "request_id": uuid.uuid4().hex, "count": 1}
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    HTTPServer(("127.0.0.1", int(os.environ["PORT"])), H).serve_forever()
''')


def test_calibration_learns_keys_that_change_by_themselves(tmp_path):
    app = tmp_path / "clock"
    app.mkdir()
    (app / "app.py").write_text(CLOCK_APP, encoding="utf-8")
    spec = '[app]\nstart = "python app.py"\nready = "/"\n\n[diff]\nignore = ["etag"]\nslow_ratio = 3.0\n'
    users = [{"name": "reader", "goal": "read twice", "steps": [{"method": "GET", "path": "/items"},
                                                                 {"method": "GET", "path": "/"}]}]
    result = bootstrap.try_files(app, {"doppel.toml": spec}, backend="local",
                                 chat=ScriptedModel([], personas=users))
    assert result["ok"]
    assert result["noise"] == ["fetched_at", "request_id"]   # name and count are stable, so they stay
    written = tomllib.loads(result["files"]["doppel.toml"])
    assert written["diff"]["ignore"] == ["etag", "fetched_at", "request_id"]
    assert written["diff"]["slow_ratio"] == 3.0               # the rest of the file is untouched


def test_noisy_keys_ignores_lists_of_different_length_and_status_changes():
    rec = lambda body, status=200: [{"persona": "p", "steps": [  # noqa: E731
        {"i": 0, "request": "GET /x", "status": status, "body": body, "ms": 1}]}]
    assert bootstrap.noisy_keys(rec({"a": {"token": "x"}}), rec({"a": {"token": "y"}})) == ["token"]
    assert bootstrap.noisy_keys(rec({"a": 1}), rec({"a": 2}, status=404)) == []
    assert bootstrap.flaky_statuses(rec({"a": 1}), rec({"a": 1}, status=404)) == ["p: GET /x 200 vs 404"]


def test_with_ignore_handles_every_shape_of_diff_table():
    assert 'ignore = ["a", "b"]' in bootstrap.with_ignore('[app]\nstart="x"\n[diff]\nignore = ["b"]\n', ["a"])
    assert tomllib.loads(bootstrap.with_ignore('[app]\nstart="x"\n[diff]\nslow_ratio = 2.0\n', ["a"])) \
        ["diff"] == {"ignore": ["a"], "slow_ratio": 2.0}
    assert tomllib.loads(bootstrap.with_ignore('[app]\nstart="x"\n', ["a"]))["diff"]["ignore"] == ["a"]
    multiline = '[app]\nstart="x"\n\n[diff]\nignore = [\n  "b",\n]\n'
    assert tomllib.loads(bootstrap.with_ignore(multiline, ["a"]))["diff"]["ignore"] == ["a", "b"]


# --- verify and gather ------------------------------------------------------------


def test_smoke_check_rejects_a_refused_login_and_server_errors():
    spec = TwinSpec(start="x", ready="/health", login={"method": "POST", "path": "/login", "auth": ["a", "b"]})
    persona = bootstrap.smoke_persona(spec)
    assert [s["path"] for s in persona["steps"]] == ["/login", "/health"]
    step = lambda status, req="POST /login": {"request": req, "status": status, "body": {}}  # noqa: E731
    assert "refused with 401" in bootstrap.smoke_problem({"steps": [step(401), step(200, "GET /health")]}, spec)
    assert "answered 500" in bootstrap.smoke_problem({"steps": [step(200), step(500, "GET /health")]}, spec)
    assert bootstrap.smoke_problem({"steps": [step(200), step(200, "GET /health")]}, spec) == ""


def test_gather_reads_setup_hints_and_ranks_settings_and_models_first(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    (tmp_path / "uv.lock").write_text("huge")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text("run: pytest")
    for rel in ("app/views.py", "app/settings.py", "app/models.py", "app/tests/test_x.py", "app/zz.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(f"# {rel}\n")
    hints = bootstrap.gather_hints(tmp_path)
    assert "### pyproject.toml" in hints and "### .github/workflows/ci.yml" in hints
    assert "uv.lock exists" in hints and "huge" not in hints
    code = bootstrap.gather_code(tmp_path)
    order = [code.index(f"### app/{n}.py") for n in ("settings", "models", "views", "zz")]
    assert order == sorted(order) and "test_x" not in code


def test_write_refuses_to_overwrite_without_force(tmp_path):
    (tmp_path / "doppel.toml").write_text("old")
    try:
        bootstrap.write(tmp_path, {"doppel.toml": "new"})
        raise AssertionError("should have refused")
    except FileExistsError:
        pass
    bootstrap.write(tmp_path, {"doppel.toml": "new"}, force=True)
    assert (tmp_path / "doppel.toml").read_text() == "new"


# --- the seed is checked against the real code before a sandbox boots ---------------------------

SERVICE = '''
def create_bookmark(bookmark, tag_string: str, current_user, disable_html_snapshot: bool = False):
    return bookmark
'''
MODELS = '''
from django.db import models


class Bookmark(models.Model):
    url = models.CharField(max_length=2048)
    title = models.CharField(max_length=512, blank=True)
    date_added = models.DateTimeField()
    date_seen = models.DateTimeField(null=True)
    unread = models.BooleanField(default=False)
    owner = models.ForeignKey("auth.User", on_delete=models.CASCADE)
    tags = models.ManyToManyField("Tag")
'''


def linkding_like(tmp_path: Path) -> Path:
    for rel, text in {"bookmarks/__init__.py": "", "bookmarks/models.py": MODELS,
                      "bookmarks/services/__init__.py": "", "bookmarks/services/bookmarks.py": SERVICE}.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    return tmp_path


def test_seed_check_catches_invented_parameters_fields_and_names(tmp_path):
    repo = linkding_like(tmp_path)
    seed = textwrap.dedent('''
        from bookmarks.models import Bookmark, Folder
        from bookmarks.services import bookmarks as service
        b = Bookmark.objects.create(url="https://x", owner_id=1, colour="red")
        service.create_bookmark(url="https://x", current_user=None)
        service.delete_everything()
    ''')
    problems = "\n".join(bootstrap.check_seed(repo, seed))
    assert "bookmarks.models has no 'Folder'" in problems
    assert "Bookmark has no field 'colour'" in problems
    assert "without date_added, which" in problems   # only that: date_seen is null=True, owner came as owner_id
    assert "create_bookmark() has no parameter 'url'" in problems
    assert "missing required argument(s): bookmark, tag_string" in problems
    assert "the real signature is create_bookmark(bookmark, tag_string: str" in problems
    assert "service has no 'delete_everything'" in problems


def test_seed_check_accepts_a_correct_seed_and_ignores_libraries(tmp_path):
    repo = linkding_like(tmp_path)
    seed = textwrap.dedent('''
        from datetime import datetime, timezone
        from django.contrib.auth.models import User
        from bookmarks.models import Bookmark
        from bookmarks.services.bookmarks import create_bookmark
        when = datetime(2026, 1, 1, tzinfo=timezone.utc)
        user = User.objects.create_user("alice", "a@x.io", "doppel-pass", is_staff=True)
        b = Bookmark(url="https://x", owner=user, date_added=when)
        create_bookmark(b, "a b", user, disable_html_snapshot=True)
        Bookmark.objects.get_or_create(url="https://y", owner=user, defaults={"date_added": when})
    ''')
    assert bootstrap.check_seed(repo, seed) == []
    assert bootstrap.check_seed(repo, "def broken(:\n")[0].startswith("doppel_seed.py line 1: syntax error")


def test_the_api_index_lists_every_signature_even_when_the_code_is_cut(tmp_path):
    repo = linkding_like(tmp_path)
    index = bootstrap.api_index(repo)
    assert "bookmarks.services.bookmarks:" in index
    assert "def create_bookmark(bookmark, tag_string: str, current_user, disable_html_snapshot: bool=False)" in index
    assert "class Bookmark(models.Model)" in index and "date_added = models.DateTimeField()" in index


def test_a_bad_seed_is_redrafted_without_booting_a_sandbox(tmp_path):
    repo = linkding_like(tmp_path)
    booted = []
    bad = reply(SHOP_SPEC, seed="from bookmarks.services.bookmarks import create_bookmark\n"
                               "create_bookmark(url='x')\n")
    model = ScriptedModel([bad, reply(SHOP_SPEC, seed="print('ok')\n")])
    result = bootstrap.init(repo, tries=2, chat=model, log=lambda m: None,
                            run_twin=lambda *a, **k: booted.append(1) or (_ for _ in ()).throw(RuntimeError("boot")))
    assert len(booted) == 1                                    # only the second, clean draft booted
    assert "has no parameter 'url'" in model.prompts[1]        # and the first draft's problems went back
    assert not result["ok"]


# --- a setup must make the invented users succeed, not just boot ------------------------------

TOKEN_APP = textwrap.dedent('''
    import json, os
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def answer(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/health":
                return self.answer(200, {"ok": True})
            if self.headers.get("Authorization") != "Token " + "a" * 40:
                return self.answer(401, {"detail": "Authentication credentials were not provided."})
            return self.answer(200, {"path": self.path, "user": "alice"})

    HTTPServer(("127.0.0.1", int(os.environ["PORT"])), H).serve_forever()
''')
TOKEN = "a" * 40
STRANGERS = [{"name": f"stranger {i}", "goal": "browse without the token",
              "steps": [{"method": "GET", "path": "/api/bookmarks/"}, {"method": "GET", "path": "/api/tags/"}]}
             for i in range(3)]


def token_app(tmp_path: Path) -> Path:
    app = tmp_path / "tokens"
    app.mkdir()
    (app / "app.py").write_text(TOKEN_APP, encoding="utf-8")
    return app


def test_users_that_never_log_in_get_the_seeded_login_before_calibration(tmp_path):
    spec = ('[app]\nstart = "python app.py"\nready = "/health"\n\n[personas.login]\nmethod = "GET"\n'
            f'path = "/api/user/profile/"\nheader = {{ Authorization = "Token {TOKEN}" }}\n')
    result = bootstrap.try_files(token_app(tmp_path), {"doppel.toml": spec}, backend="local",
                                 chat=ScriptedModel([], personas=STRANGERS))
    assert result["ok"], result["error"]
    assert result["repaired"] == ["stranger 0", "stranger 1", "stranger 2"]   # quick fix, no model call
    assert result["coverage"] == 1.0


def test_a_setup_whose_users_are_all_refused_is_sent_back_with_what_the_app_said(tmp_path):
    spec = '[app]\nstart = "python app.py"\nready = "/health"\n'               # no login for the users
    result = bootstrap.try_files(token_app(tmp_path), {"doppel.toml": spec}, backend="local",
                                 chat=ScriptedModel([], personas=STRANGERS))
    assert not result["ok"] and result["coverage"] == 0.0
    assert "got only 0% of their requests served" in result["error"]
    assert "status counts: {401: 6}" in result["error"]
    assert "Authentication credentials were not provided" in result["error"]


# --- install is derived, the spec is checked, and init never moves backwards ------------------

PYPROJECT = textwrap.dedent('''
    [project]
    name = "linkding"
    dependencies = ["django>=6.0.7", "djangorestframework>=3.16", "uwsgi>=2.0.28", "psycopg2>=2.9",
                    "huey>=2.5"]
''')
LOGIN_SPEC = ('[app]\ninstall = "pip install django"\nstart = "python app.py"\nready = "/health"\n\n'
              '[personas.login]\nmethod = "GET"\npath = "/api/user/"\n')


def test_install_is_derived_from_the_dependency_list_without_compiled_packages(tmp_path):
    (tmp_path / "pyproject.toml").write_text(PYPROJECT)
    cmd = bootstrap.derived_install(tmp_path)
    assert cmd == ("pip install --quiet 'django>=6.0.7' 'djangorestframework>=3.16' 'psycopg2-binary' "
                   "'huey>=2.5'")
    hint = bootstrap.install_hint(tmp_path)
    assert "derived by Doppel from pyproject.toml (uwsgi, psycopg2 removed" in hint and cmd in hint
    (tmp_path / "pyproject.toml").unlink()
    (tmp_path / "requirements.txt").write_text("flask==3.0  # web\n-r extra.txt\ngunicorn\n")
    assert bootstrap.derived_install(tmp_path) == "pip install --quiet 'flask==3.0' 'gunicorn'"


def test_certain_spec_mistakes_are_caught_without_booting(tmp_path):
    (tmp_path / "pyproject.toml").write_text(PYPROJECT)
    spec = lambda install, ready="/health": f'[app]\ninstall = "{install}"\nstart = "x"\nready = "{ready}"\n'  # noqa: E731
    problems = bootstrap.check_spec(tmp_path, spec("pip install -r pyproject.toml"))
    assert "pip can only read requirements files" in problems[0]
    for whole in ("uv sync --no-dev", "pip install -e .", "pip install .", "poetry install"):
        problems = bootstrap.check_spec(tmp_path, spec(whole))
        assert problems and "uwsgi, psycopg2" in problems[0] and "Use: pip install --quiet" in problems[0], whole
    assert "must be a path starting with /" in bootstrap.check_spec(tmp_path, spec("pip install x", "health"))[0]
    assert bootstrap.check_spec(tmp_path, spec(bootstrap.derived_install(tmp_path).replace("'", ""))) == []


def test_stage_reached_reads_how_far_an_error_got():
    assert bootstrap.stage_reached("RuntimeError: install (base) failed (uv sync):\n...") == 0
    assert bootstrap.stage_reached("RuntimeError: seed failed (python doppel_seed.py):\n...") == 1
    assert bootstrap.stage_reached("RuntimeError: persona 'x' failed (exit 2):\napp not ready") == 2
    assert bootstrap.stage_reached("the login step GET /api/user/ was refused with 404: ...") == 3
    assert bootstrap.stage_reached("the twin boots, but the invented users got only 11%") == 4
    assert bootstrap.stage_reached("The files were checked against the repo before booting") == -1  # no boot


def fake_twin(outcomes):
    """A run_twin that plays back outcomes: an exception to raise, or a status for the login step."""
    def run(base, head, spec, personas):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        rec = {"persona": personas[0]["name"], "steps": [
            {"i": 0, "request": "GET /api/user/", "status": outcome, "body": "<html>Page not found</html>", "ms": 1},
            {"i": 1, "request": "GET /health", "status": 200, "body": {}, "ms": 1}]}
        return {"base": [rec], "head": [rec], "failed": []}
    return run


def test_a_draft_that_goes_backwards_is_dropped_for_the_one_that_got_furthest(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "urls.py").write_text("urlpatterns = [path('api/user/profile/', profile)]\n")
    worse = LOGIN_SPEC.replace("pip install django", "pip install nothing-like-this")
    model = ScriptedModel([reply(LOGIN_SPEC), reply(worse), reply(LOGIN_SPEC)])
    run = fake_twin([404, RuntimeError("install (base) failed (pip install nothing-like-this):\nNo matching"), 404])
    logs = []
    bootstrap.init(tmp_path, tries=3, chat=model, run_twin=run, log=logs.append, resume=False)
    third = model.prompts[2]
    assert "pip install django" in third.split("Previous files:")[1]           # attempt 1's files, not 2's
    assert "Already proven to work in an earlier attempt: install, seed, start" in third
    assert "api/user/profile/" in third                                        # the URL config, for the 404
    assert any("went backwards" in line for line in logs)


def test_progress_carries_over_to_the_next_run_and_is_removed_on_success(tmp_path):
    app = shop_without_spec(tmp_path)
    broken = SHOP_SPEC.replace('start = "python app.py"', 'start = "python missing.py"')
    bootstrap.init(app, backend="local", tries=1, chat=ScriptedModel([reply(broken)]), log=lambda m: None)
    state = json.loads((app / bootstrap.STATE_FILE).read_text())
    assert state["best"]["stage"] == 2 and "missing.py" in state["best"]["files"]["doppel.toml"]
    model = ScriptedModel([reply(SHOP_SPEC)])
    logs = []
    result = bootstrap.init(app, backend="local", tries=1, chat=model, log=logs.append)
    assert result["ok"] and "missing.py" in model.prompts[0]                  # it started from the last run
    assert any("resuming from the last run" in line for line in logs)
    assert not (app / bootstrap.STATE_FILE).exists()


# --- refused logins, framework versions, and models the checker must not misread -------------

def test_a_refused_login_comes_with_how_the_app_really_authenticates(tmp_path):
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "auth.py").write_text("class AppTokenAuthentication(TokenAuthentication):\n"
                                              "    model = ApiToken\n")
    (tmp_path / "models.py").write_text("class ApiToken(models.Model):\n    key = models.CharField(max_length=40)\n")
    (tmp_path / "settings.py").write_text("DEBUG = True\nREST_FRAMEWORK = {'DEFAULT_AUTHENTICATION_CLASSES': []}\n")
    source = bootstrap.related_source(tmp_path, "the login step GET /api/me/ was refused with 401: Invalid token")
    assert "class AppTokenAuthentication" in source and "class ApiToken" in source
    assert "DEFAULT_AUTHENTICATION_CLASSES" in source and "DEBUG" not in source
    assert bootstrap.related_source(tmp_path, "seed failed: KeyError") == ""


def test_the_prompt_names_framework_versions_and_the_seed_check_knows_removed_apis(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\ndependencies = ["django>=6.0.7"]\n')
    note = bootstrap.versions_note(tmp_path)
    assert "django>=6.0.7" in note and "django.utils.timezone.utc (removed in django 5" in note
    seed = ("from django.utils import timezone\nimport datetime\n"
            "a = timezone.utc\nb = datetime.timezone.utc\nfrom django.utils.timezone import utc\n")
    problems = bootstrap.check_seed(tmp_path, seed)
    assert [p.split(":")[0] for p in problems] == ["doppel_seed.py line 5", "doppel_seed.py line 3"]
    assert all("use datetime.timezone.utc" in p for p in problems)                  # stdlib one is fine
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\ndependencies = ["django>=3.2,<4.2"]\n')
    assert bootstrap.check_seed(tmp_path, seed) == []                               # old Django still has it


def test_sqlalchemy_2_models_with_annotations_and_property_setters_are_read_correctly(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "models.py").write_text(textwrap.dedent('''
        class Updateable:
            def update(self, data):
                pass

        class User(Updateable, Model):
            __tablename__ = "users"
            id: so.Mapped[int] = so.mapped_column(primary_key=True)
            username: so.Mapped[str] = so.mapped_column(sa.String(64))
            posts: so.WriteOnlyMapped["Post"] = so.relationship(back_populates="author")

            @property
            def password(self):
                raise AttributeError("write only")

            @password.setter
            def password(self, value):
                self.password_hash = value
    '''))
    ok = "from app.models import User\nUser(username='a', password='doppel-pass')\n"
    assert bootstrap.check_seed(tmp_path, ok) == []
    bad = "from app.models import User\nUser(username='a', colour='red')\n"
    assert "User has no field 'colour'" in bootstrap.check_seed(tmp_path, bad)[0]


# --- a refused login says what to try instead -------------------------------------------------

def microblog_like(tmp_path: Path) -> Path:
    files = {
        "api/__init__.py": "",
        "api/app.py": "app.register_blueprint(tokens, url_prefix='/api')\napp.register_blueprint(posts)\n",
        "api/tokens.py": textwrap.dedent('''
            tokens = Blueprint('tokens', __name__)

            @tokens.route('/tokens', methods=['POST'])
            @authenticate(basic_auth)
            def new():
                return {'access_token': 'x'}
        '''),
        "api/posts.py": textwrap.dedent('''
            @posts.route('/posts', methods=['GET'])
            @authenticate(token_auth)
            def all():
                return []
        '''),
        "api/models.py": textwrap.dedent('''
            class Token(Model):
                access_token: so.Mapped[str] = so.mapped_column()
                access_expiration: so.Mapped[datetime]

                @property
                def access_token_jwt(self):
                    return jwt.encode({'token': self.access_token}, key)
        '''),
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    return tmp_path


def test_a_refused_seeded_token_points_at_the_route_that_issues_tokens(tmp_path):
    repo = microblog_like(tmp_path)
    spec = TwinSpec(start="x", ready="/", login={"method": "GET", "path": "/api/users/me",
                                                 "header": {"Authorization": "Bearer abc"}})
    rec = {"steps": [{"request": "GET /api/users/me", "status": 401, "body": {"message": "Unauthorized"}},
                     {"request": "GET /", "status": 200, "body": {}}]}
    problem = bootstrap.smoke_problem(rec, spec, repo)
    assert "Your login sends a fixed token from the seed" in problem
    assert "signs, hashes or expires its tokens" in problem               # the Token model uses jwt + expiry
    assert "@tokens.route('/tokens', methods=['POST'])" in problem and "basic_auth" in problem
    assert "url_prefix='/api'" in problem                                 # so the model can build /api/tokens
    assert "/posts" not in problem                                        # a route that only *uses* a token


def test_refused_credentials_get_password_hashing_advice_instead(tmp_path):
    repo = microblog_like(tmp_path)
    spec = TwinSpec(start="x", ready="/", login={"method": "POST", "path": "/api/tokens",
                                                 "auth": ["alice", "doppel-pass"], "save": {"token": "access_token"}})
    rec = {"steps": [{"request": "POST /api/tokens", "status": 401, "body": {}}]}
    problem = bootstrap.smoke_problem(rec, spec, repo)
    assert "sets the password through the app's own hashing" in problem
    assert "fixed token" not in problem


# --- one set of settings for every command ------------------------------------------------------

ENV_APP = textwrap.dedent('''
    import json, os, sqlite3
    from http.server import BaseHTTPRequestHandler, HTTPServer
    DB = os.environ.get("APP_DB", "default.db")

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            users = [r[0] for r in sqlite3.connect(DB).execute("SELECT name FROM users")]
            data = json.dumps({"db": DB, "users": users}).encode()
            self.send_response(200 if users else 404)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    HTTPServer(("127.0.0.1", int(os.environ["PORT"])), H).serve_forever()
''')
ENV_SEED = ('import os, sqlite3\ndb = sqlite3.connect(os.environ.get("APP_DB", "default.db"))\n'
            'db.execute("CREATE TABLE users (name TEXT)")\ndb.execute("INSERT INTO users VALUES (\'alice\')")\n'
            'db.commit()\n')


def test_app_env_reaches_the_seed_and_the_server_alike(tmp_path):
    from doppel.backends.local import run_twin
    app = tmp_path / "envapp"
    app.mkdir()
    (app / "app.py").write_text(ENV_APP)
    (app / "doppel_seed.py").write_text(ENV_SEED)
    (app / "doppel.toml").write_text('[app]\nstart = "python app.py"\nready = "/"\nseed = "python doppel_seed.py"\n'
                                    'env = { APP_DB = "twin.db" }\n')
    spec = TwinSpec.load(app / "doppel.toml")
    assert spec.env == {"APP_DB": "twin.db"} and spec.exports() == "export APP_DB=twin.db; "
    out = run_twin(app, app, spec, [{"name": "reader", "goal": "g", "steps": [{"method": "GET", "path": "/"}]}])
    body = out["base"][0]["steps"][0]["body"]
    assert body == {"db": "twin.db", "users": ["alice"]}          # the server read the database the seed filled


def test_settings_given_to_the_seed_but_not_the_server_are_caught(tmp_path):
    toml = ('[app]\nstart = "gunicorn app:app"\nseed = "env APP_DB=/data/a.db python doppel_seed.py"\n'
            'migrate = "APP_DB=/data/a.db alembic upgrade head"\n')
    seed = 'import os\nos.environ["APP_DB"] = "/data/a.db"\nos.environ.setdefault("DJANGO_SETTINGS_MODULE", "x.dev")\n'
    problems = "\n".join(bootstrap.check_env(tmp_path, toml, seed))
    assert "`seed` sets APP_DB=/data/a.db but `start` does not" in problems
    assert "`migrate` sets APP_DB=/data/a.db" in problems
    assert "doppel_seed.py line 2 sets APP_DB" in problems
    assert "DJANGO_SETTINGS_MODULE" in problems                       # nobody else sets that default...
    (tmp_path / "manage.py").write_text('os.environ.setdefault("DJANGO_SETTINGS_MODULE", "x.dev")\n')
    assert "DJANGO_SETTINGS_MODULE" not in "\n".join(bootstrap.check_env(tmp_path, toml, seed))  # ...now the app does
    fixed = '[app]\nstart = "gunicorn app:app"\nseed = "python doppel_seed.py"\nenv = { APP_DB = "/data/a.db" }\n'
    assert bootstrap.check_env(tmp_path, fixed, 'import os\nos.environ["APP_DB"] = "/data/a.db"\n') == []


def test_a_plain_string_in_a_password_hash_field_is_caught(tmp_path):
    seed = ("u = User(username='a', password_hash='doppel-pass')\nu.password_hash = 'x'\n"
            "v = User(username='b', password_hash=generate_password_hash('doppel-pass'))\n")
    problems = bootstrap.check_seed(tmp_path, seed)
    assert sorted(p.split(":")[0] for p in problems) == ["doppel_seed.py line 1", "doppel_seed.py line 2"]
    assert all("can never match" in p for p in problems)


def test_pinned_requirements_win_over_loose_pyproject_ranges(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\ndependencies = ["flask"]\n')
    (tmp_path / "requirements.txt").write_text("flask==3.0.3\nuwsgi==2.0.28\n")
    assert bootstrap.derived_install(tmp_path) == "pip install --quiet 'flask==3.0.3'"


# --- looking inside: what the seed printed and what the databases hold ------------------------

def test_the_local_twin_reports_what_the_seed_printed_and_the_databases_after_it(tmp_path):
    from doppel.backends.local import run_twin
    app = shop_without_spec(tmp_path)
    (app / "doppel.toml").write_text(SHOP_SPEC)
    spec = TwinSpec.load(app / "doppel.toml")
    out = run_twin(app, app, spec, SHOP_PERSONAS[:1])
    assert "seeded 10 products" in out["seed_log"]
    assert "[dbscan]" in out["seed_log"] and "products=10" in out["seed_log"]


def test_seed_evidence_names_empty_user_tables_and_competing_databases():
    empty = bootstrap.seed_evidence("seeded 3 users\n[dbscan] /app/db.sqlite: users=0, posts=0\n")
    assert "Every user table is EMPTY" in empty and "seeded 3 users" in empty
    two = bootstrap.seed_evidence("[dbscan] /app/db.sqlite: users=0\n[dbscan] /data/a.db: users=3\n")
    assert "More than one database has a user table" in two
    fine = bootstrap.seed_evidence("[dbscan] /app/db.sqlite: users=3, posts=9\n")
    assert "EMPTY" not in fine and "More than one" not in fine and "users=3" in fine
    assert bootstrap.seed_evidence("") == ""


def test_the_sandbox_seed_step_keeps_the_seed_exit_code_around_the_scan():
    import subprocess

    from doppel.dbscan import one_liner
    line = f"exit 3; rc=$?; {one_liner()} || true; exit $rc".replace("exit 3;", "(exit 3);")
    assert subprocess.run(["sh", "-c", line], capture_output=True).returncode == 3


def test_a_seed_that_never_commits_is_caught():
    no_commit = "from api.app import db\nwith app.app_context():\n    db.session.add(User(username='a'))\n"
    assert "never commits" in bootstrap.check_seed(Path("."), no_commit)[0]
    committed = no_commit + "    db.session.commit()\n"
    assert not any("never commits" in p for p in bootstrap.check_seed(Path("."), committed))
    assert not any("never commits" in p for p in bootstrap.check_seed(Path("."), "Tag.objects.create(name='x')\n"))
