"""doppel init on session-login apps: a form login is offered, checked and recognized when it fails."""
import textwrap
from pathlib import Path

from doppel import bootstrap
from doppel.rehearsal import login_step_count, with_login
from tests.test_init import ScriptedModel, reply

APP = '''
import json, os, secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

SESSIONS = {}
FORM = ('<html><head><title>Log in</title></head><body>{msg}<form method="post">'
        '<input name="username"><input type="password" name="password"><button>Log in</button></form></body></html>')


class App(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def page(self, status, html, headers=()):
        data = html.encode()
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def who(self):
        sid = dict(p.strip().split("=", 1) for p in self.headers.get("Cookie", "").split(";") if "=" in p).get("sid")
        return SESSIONS.get(sid)

    def do_GET(self):
        if self.path == "/health":
            return self.page(200, "<html><body>ok</body></html>")
        if self.path == "/login":
            return self.page(200, FORM.format(msg=""))
        if self.path == "/":
            return self.page(200, f"<html><body><p>Hello {self.who() or 'stranger'}</p></body></html>")
        self.page(404, "<html><body>Not Found</body></html>")

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        if self.path != "/login":
            return self.page(404, "<html><body>Not Found</body></html>")
        if self.headers.get("Content-Type") != "application/x-www-form-urlencoded":
            return self.page(400, "<html><body><h1>Bad Request</h1></body></html>")   # like Flask's request.form
        form = {k: v[0] for k, v in parse_qs(body).items()}
        users = json.load(open("users.json"))
        if users.get(form.get("username")) != form.get("password"):
            return self.page(200, FORM.format(msg="<p>Incorrect password.</p>"))  # 200, like most apps
        sid = secrets.token_hex(8)
        SESSIONS[sid] = form["username"]
        self.page(302, "", [("Set-Cookie", f"sid={sid}; Path=/"), ("Location", "/")])


ThreadingHTTPServer(("127.0.0.1", int(os.environ["PORT"])), App).serve_forever()
'''
SEED = 'import json\njson.dump({"alice": "doppel-pass", "bob": "doppel-pass"}, open("users.json", "w"))\n'
SPEC = '''[app]
start = "python app.py"
ready = "/health"
seed = "python doppel_seed.py"

[personas.login]
method = "POST"
path = "/login"
{creds}
'''
USERS = [{"name": "reader", "steps": [{"method": "GET", "path": "/"}]},
         {"name": "login page", "steps": [{"method": "GET", "path": "/login"}]}]


def session_app(tmp_path: Path) -> Path:
    app = tmp_path / "blog"
    app.mkdir()
    (app / "app.py").write_text(APP, encoding="utf-8")
    return app


def spec(creds: str) -> str:
    return SPEC.format(creds=creds)


JSON_LOGIN = spec('json = { username = "alice", password = "doppel-pass" }')
FORM_LOGIN = spec('form = { username = "alice", password = "doppel-pass" }')
WRONG_FORM = spec('form = { username = "alice", password = "nope" }')


def test_a_form_login_loads_its_page_first_like_a_browser():
    login = {"method": "POST", "path": "/login", "form": {"username": "a", "password": "b"}}
    steps = with_login({"name": "p", "steps": [{"method": "GET", "path": "/"}]}, login)["steps"]
    assert [(s["method"], s["path"]) for s in steps] == [("GET", "/login"), ("POST", "/login"), ("GET", "/")]
    assert login_step_count(login) == 2 and login_step_count({"path": "/t", "auth": ["a", "b"]}) == 1


def test_init_moves_a_json_login_to_the_form_the_view_reads(tmp_path):
    app = session_app(tmp_path)
    model = ScriptedModel([reply(JSON_LOGIN, SEED), reply(FORM_LOGIN, SEED)], personas=USERS)
    result = bootstrap.init(app, backend="local", tries=3, chat=model, log=lambda m: None)
    assert result["ok"] and result["attempts"] == 2
    assert "refused with 400" in model.prompts[1] and "a JSON body with password, username" in model.prompts[1]
    assert "form = {" in model.prompts[0]                       # the template offers form logins


def test_a_form_login_that_comes_back_to_the_form_is_a_failure_even_with_200(tmp_path):
    app = session_app(tmp_path)
    model = ScriptedModel([reply(WRONG_FORM, SEED), reply(FORM_LOGIN, SEED)], personas=USERS)
    result = bootstrap.init(app, backend="local", tries=3, chat=model, log=lambda m: None)
    assert result["ok"] and result["attempts"] == 2
    assert "came back instead of logging in" in model.prompts[1] and "Incorrect password." in model.prompts[1]


def test_a_json_login_on_a_view_that_reads_a_form_is_caught_before_booting(tmp_path):
    repo = tmp_path / "flaskapp"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "auth.py").write_text(textwrap.dedent('''
        @bp.route("/login", methods=("GET", "POST"))
        def login():
            if request.method == "POST":
                username = request.form["username"]
    '''), encoding="utf-8")
    problems = bootstrap.check_spec(repo, JSON_LOGIN)
    assert any("reads an HTML form" in p for p in problems)
    assert not any("reads an HTML form" in p for p in bootstrap.check_spec(repo, FORM_LOGIN))


def test_the_django_login_view_counts_as_a_form(tmp_path):
    (tmp_path / "urls.py").write_text('urlpatterns = [path("accounts/login/", auth_views.LoginView.as_view())]\n',
                                      encoding="utf-8")
    assert bootstrap.route_reads_form(tmp_path, "/accounts/login/")
    assert not bootstrap.route_reads_form(tmp_path, "/api/tokens")


def test_form_credentials_are_checked_against_hashed_seed_passwords():
    orm = 'User.create(username="alice", password=generate_password_hash("doppel-pass"))\n'
    raw_sql = ('db.execute("INSERT INTO user (username, password) VALUES (?, ?)",\n'
               '           ("alice", generate_password_hash("doppel-pass")))\n')
    for seed in (orm, raw_sql):
        assert bootstrap.check_login_matches_seed(WRONG_FORM, seed)
        assert not bootstrap.check_login_matches_seed(FORM_LOGIN, seed)




CRASHING_SEED = 'import os, json\nos.makedirs("instance", exist_ok=True)\n' \
                'json.dump({"alice": "doppel-pass"}, open("instance/users.json", "w"))\n'   # wrong place


def test_a_crash_sends_the_apps_own_traceback_back_and_no_password_advice(tmp_path):
    app = session_app(tmp_path)
    model = ScriptedModel([reply(FORM_LOGIN, CRASHING_SEED), reply(FORM_LOGIN, SEED)], personas=USERS)
    result = bootstrap.init(app, backend="local", tries=3, chat=model, log=lambda m: None)
    assert result["ok"] and result["attempts"] == 2
    told = model.prompts[1]
    assert "(the app crashed)" in told and "FileNotFoundError" in told and "users.json" in told
    assert "PASSWORD the seed" not in told


def test_a_missing_table_points_at_two_different_database_files():
    from doppel.spec import TwinSpec
    rec = {"steps": [{"request": "POST /login", "status": 500, "body": {}}],
           "app_log": "Traceback ...\nsqlite3.OperationalError: no such table: user"}
    problem = bootstrap.smoke_problem(rec, TwinSpec(start="x"))
    assert "DIFFERENT database file" in problem and "no such table: user" in problem


def test_a_healthy_recording_carries_no_app_log():
    from doppel.runner import attach_app_log
    ok = attach_app_log({"steps": [{"status": 200}, {"status": 404}]}, "lots of access log lines")
    bad = attach_app_log({"steps": [{"status": 200}, {"status": 500}]}, "\n".join(f"l{i}" for i in range(99)))
    assert "app_log" not in ok and bad["app_log"].splitlines() == [f"l{i}" for i in range(39, 99)]


def test_library_frames_in_a_traceback_are_not_mistaken_for_app_files(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    error = ('  File "/usr/lib/python3.12/socketserver.py", line 761, in __init__\n'
             '  File "/app/app.py", line 1, in <module>\n')
    assert bootstrap._repo_file(tmp_path, "/usr/lib/python3.12/socketserver.py") is None
    assert bootstrap._repo_file(tmp_path, "/app/app.py") == tmp_path / "app.py"
    assert "app.py (around line 1)" in bootstrap.related_source(tmp_path, error)
