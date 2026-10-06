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


def test_a_reply_without_a_spec_counts_as_a_failed_attempt(tmp_path):
    app = shop_without_spec(tmp_path)
    model = ScriptedModel(["sorry, I cannot help", reply(SHOP_SPEC)])
    result = bootstrap.init(app, backend="local", tries=2, chat=model, log=lambda m: None)
    assert result["ok"] and result["attempts"] == 2


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
