"""The introspection probe: what is true about an app, learned by importing it the way its framework does."""
import json
import secrets
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from doppel import bootstrap, runner
from doppel.probe import facts_text, probe_local
from tests.test_init import SHOP_SPEC, ScriptedModel, reply, shop_without_spec


def write(root: Path, files: dict) -> Path:
    for name, text in files.items():
        f = root / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(textwrap.dedent(text), encoding="utf-8")
    return root


def by_path(facts):
    return {r["path"]: r for r in facts["routes"]}


FLASK_PKG = {
    "blog/__init__.py": '''
        import os
        from flask import Flask

        def create_app():
            app = Flask(__name__, instance_relative_config=True)
            app.config.from_mapping(SECRET_KEY="dev", DATABASE=os.path.join(app.instance_path, "blog.sqlite"))
            from . import auth, views
            app.register_blueprint(auth.bp)
            app.register_blueprint(views.bp)

            @app.cli.command("init-db")
            def init_db():
                pass
            return app
        ''',
    "blog/auth.py": '''
        import functools
        from flask import Blueprint, request, session
        from werkzeug.security import check_password_hash
        bp = Blueprint("auth", __name__, url_prefix="/auth")

        def login_required(view):
            @functools.wraps(view)
            def wrapped(**kw):
                return view(**kw)
            return wrapped

        @bp.route("/login", methods=("GET", "POST"))
        def login():
            if request.method == "POST" and check_password_hash("x", request.form["password"]):
                session["user_id"] = 1
            return "ok"

        @bp.route("/register", methods=("GET", "POST"))
        def register():
            return request.form.get("password", "") + " go to auth.login"
        ''',
    "blog/views.py": '''
        from flask import Blueprint, request
        from .auth import login_required
        bp = Blueprint("views", __name__)

        @bp.route("/notes", methods=("POST",))
        @login_required
        def create():
            return request.get_json()
        ''',
}


def test_flask_factory_app(tmp_path):
    pytest.importorskip("flask")
    facts = probe_local(write(tmp_path, FLASK_PKG))
    assert facts["framework"] == "flask" and facts["app"] == "blog" and not facts["errors"]
    assert facts["run"] == "flask --app blog run --host 0.0.0.0 --port $PORT"
    assert facts["database"]["configured"] == "<repo>/instance/blog.sqlite"     # next to the package, not in it
    assert facts["commands"] == ["flask --app blog init-db"]
    routes = by_path(facts)
    assert routes["/auth/login"]["looks_like_login"] and routes["/auth/login"]["reads"] == ["form"]
    assert not routes["/auth/register"].get("looks_like_login")               # hashing a password isn't a login
    assert routes["/notes"]["needs_login"] and routes["/notes"]["reads"] == ["json"]
    assert routes["/notes"]["methods"] == ["POST"]


def test_flask_app_named_in_flaskenv(tmp_path):
    pytest.importorskip("flask")
    write(tmp_path, {**FLASK_PKG, "serve.py": "from blog import create_app as make\napplication = make()\n",
                     ".flaskenv": "FLASK_APP=serve.py:application\n"})
    (tmp_path / "blog" / "__init__.py").write_text(
        (tmp_path / "blog" / "__init__.py").read_text().replace("def create_app", "def build"), encoding="utf-8")
    (tmp_path / "serve.py").write_text("from blog import build\napplication = build()\n", encoding="utf-8")
    facts = probe_local(tmp_path)
    assert facts["app"] == "serve.py:application" and "/auth/login" in by_path(facts)


def test_django_project(tmp_path):
    pytest.importorskip("django")
    write(tmp_path, {
        "manage.py": 'import os\nos.environ.setdefault("DJANGO_SETTINGS_MODULE", "proj.settings")\n',
        "proj/__init__.py": "",
        "proj/settings.py": '''
            SECRET_KEY = "x"
            INSTALLED_APPS = ["django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions", "notes"]
            MIDDLEWARE = ["django.middleware.csrf.CsrfViewMiddleware"]
            ROOT_URLCONF = "proj.urls"
            DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": "data/db.sqlite3"}}
            LOGIN_URL = "/accounts/login/"
            ''',
        "proj/urls.py": '''
            from django.contrib.auth import views as auth
            from django.urls import path
            from notes import views
            urlpatterns = [path("accounts/login/", auth.LoginView.as_view()),
                           path("notes/", views.add), path("notes/<int:pk>/", views.Detail.as_view())]
            ''',
        "notes/__init__.py": "",
        "notes/models.py": '''
            from django.conf import settings
            from django.db import models
            class Note(models.Model):
                owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
                title = models.CharField(max_length=80, unique=True)
                body = models.TextField(blank=True)
            ''',
        "notes/views.py": '''
            from django.contrib.auth.decorators import login_required
            from django.contrib.auth.mixins import LoginRequiredMixin
            from django.http import JsonResponse
            from django.views import View
            from django.views.decorators.http import require_POST

            @login_required
            @require_POST
            def add(request):
                return JsonResponse({"title": request.POST["title"]})

            class Detail(LoginRequiredMixin, View):
                def get(self, request, pk):
                    return JsonResponse({})
            ''',
    })
    facts = probe_local(tmp_path)
    assert facts["framework"] == "django" and not facts["errors"]
    assert facts["database"]["default"]["name"] == "data/db.sqlite3"
    assert facts["auth"]["csrf"] and facts["auth"]["username_field"] == "username"
    assert facts["commands"] == ["python manage.py migrate --noinput"]
    routes = by_path(facts)
    login = routes["/accounts/login/"]
    assert login["looks_like_login"] and "form" in login["reads"] and not login["needs_login"]
    assert routes["/notes/"]["methods"] == ["POST"] and routes["/notes/"]["needs_login"]
    assert routes["/notes/<int:pk>/"]["methods"] == ["GET"] and routes["/notes/<int:pk>/"]["needs_login"]
    note = next(m for m in facts["models"] if m["class"] == "notes.Note")
    fields = {f["name"]: f for f in note["fields"]}
    assert fields["owner"]["required"] and fields["owner"]["fk"] == "auth.User"
    assert fields["title"]["unique"] and not fields["body"]["required"]


def test_fastapi_app(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("multipart")
    write(tmp_path, {"api/__init__.py": "", "api/main.py": '''
        from fastapi import Depends, FastAPI
        from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
        app = FastAPI()
        oauth2 = OAuth2PasswordBearer(tokenUrl="token")

        def get_current_user(token: str = Depends(oauth2)):
            return {"name": "alice"}

        @app.post("/token")
        def token(form: OAuth2PasswordRequestForm = Depends()):
            return {"access_token": form.username, "token_type": "bearer"}

        @app.get("/me")
        def me(user=Depends(get_current_user)):
            return user
        '''})
    facts = probe_local(tmp_path)
    assert facts["framework"] == "fastapi" and facts["app"] == "api.main:app" and not facts["errors"]
    assert facts["run"] == "uvicorn api.main:app --host 0.0.0.0 --port $PORT"
    routes = by_path(facts)
    assert routes["/token"]["looks_like_login"] and "form" in routes["/token"]["reads"]
    assert routes["/me"]["needs_login"] and not routes["/token"].get("needs_login")
    assert facts["auth"]["security_schemes"]["OAuth2PasswordBearer"]["flows"]["password"]["tokenUrl"] == "token"


def test_an_app_that_does_not_import_reports_why(tmp_path):
    pytest.importorskip("flask")
    write(tmp_path, {"app.py": "from flask import Flask\napp = Flask(__name__)\ndef broken(:\n    pass\n",
                     "pyproject.toml": '[project]\nname = "x"\nrequires-python = ">=3.99"\n'})
    facts = probe_local(tmp_path)
    assert facts["errors"] and "SyntaxError" in json.dumps(facts["errors"])
    assert ">=3.99" in facts["python_mismatch"]
    assert "PYTHON VERSION" in facts_text(facts)


def test_the_brief_names_what_a_setup_needs():
    facts = {"framework": "flask", "version": "3.1", "app": "blog", "python": "3.12",
             "run": "flask --app blog run --host 0.0.0.0 --port $PORT", "commands": ["flask --app blog init-db"],
             "database": {"configured": "<repo>/instance/blog.sqlite"}, "errors": [],
             "routes": [{"methods": ["GET", "POST"], "path": "/auth/login", "reads": ["form"], "view": "blog/auth.py:9",
                         "needs_login": False, "looks_like_login": True}],
             "models": [{"class": "notes.Note", "table": "notes_note",
                         "fields": [{"name": "owner", "required": True, "fk": "auth.User"}]}]}
    text = facts_text(facts)
    assert "<repo>/instance/blog.sqlite" in text and "flask --app blog init-db" in text
    assert "GET POST /auth/login  [reads form, THIS IS A LOGIN VIEW]" in text
    assert "required owner->auth.User" in text


def test_init_puts_the_probe_facts_in_the_prompt(tmp_path):
    app = shop_without_spec(tmp_path)
    facts = {"framework": "flask", "python": "3.12", "routes": [], "errors": [],
             "database": {"configured": "<repo>/instance/x.sqlite"}}
    model = ScriptedModel([reply(SHOP_SPEC)])
    result = bootstrap.init(app, backend="local", tries=1, chat=model, log=lambda m: None,
                            prober=lambda repo, backend, install: facts)
    assert result["ok"]
    assert "FACTS FROM THE RUNNING APP" in model.prompts[0] and "<repo>/instance/x.sqlite" in model.prompts[0]


def test_a_probe_that_cannot_run_does_not_stop_init(tmp_path):
    def broken(repo, backend, install):
        raise ConnectionError("sandbox unreachable")
    app = shop_without_spec(tmp_path)
    model = ScriptedModel([reply(SHOP_SPEC)])
    logs = []
    result = bootstrap.init(app, backend="local", tries=1, chat=model, log=logs.append, prober=broken)
    assert result["ok"] and "FACTS FROM THE RUNNING APP" not in model.prompts[0]
    assert any("drafting from the code alone" in m for m in logs)


# --- the runner submits hidden form fields back, like a browser ----------------------------

class WTFApp(BaseHTTPRequestHandler):
    """Flask-WTF style: the CSRF token lives in a hidden field and the session, not in a cookie of its own."""
    tokens: dict = {}

    def log_message(self, *a):
        pass

    def sid(self):
        return dict(p.strip().split("=", 1) for p in self.headers.get("Cookie", "").split(";") if "=" in p).get("s")

    def send(self, status, html, headers=()):
        data = html.encode()
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        sid = self.sid() or secrets.token_hex(4)
        token = self.tokens.setdefault(sid, secrets.token_hex(8))
        self.send(200, f'<html><body><form method="post"><input type="hidden" name="csrf_token" value="{token}">'
                       '<input name="title"></form></body></html>', [("Set-Cookie", f"s={sid}; Path=/")])

    def do_POST(self):
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode()).items()}
        ok = self.sid() and form.get("csrf_token") == self.tokens.get(self.sid())
        self.send(200 if ok else 400, f"<html><body><p>{'saved ' + form.get('title', '') if ok else 'CSRF'}</p>"
                                      "</body></html>")


@pytest.fixture(scope="module")
def wtf():
    server = ThreadingHTTPServer(("127.0.0.1", 0), WTFApp)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_hidden_form_fields_are_submitted_back_like_a_browser(wtf):
    copied = {"title": "x", "csrf_token": "(token)"}   # a value copied from a recording
    p = {"name": "p", "steps": [{"method": "GET", "path": "/notes/new"},
                                {"method": "POST", "path": "/notes/new", "form": {"title": "hi"}},
                                {"method": "POST", "path": "/notes/new", "form": copied}]}
    steps = runner.replay(wtf, [p])[0]["steps"]
    assert [s["status"] for s in steps] == [200, 200, 200]
    assert steps[1]["body"]["html"]["text"] == ["saved hi"]


def test_a_form_never_shown_gets_no_token(wtf):
    p = {"name": "p", "steps": [{"method": "POST", "path": "/notes/new", "form": {"title": "hi"}}]}
    assert runner.replay(wtf, [p])[0]["steps"][0]["status"] == 400


def test_the_probe_image_follows_requires_python(tmp_path):
    from doppel.probe import image_for
    assert image_for(tmp_path) == "python:3.12-slim"
    (tmp_path / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.14,<4.0"\n', encoding="utf-8")
    assert image_for(tmp_path) == "python:3.14-slim"
    (tmp_path / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.9"\n', encoding="utf-8")
    assert image_for(tmp_path) == "python:3.12-slim"


def test_paths_read_the_same_on_windows_linux_and_in_the_sandbox():
    import importlib.util
    spec = importlib.util.spec_from_file_location("probe_app", Path(__file__).parent.parent / "src/doppel/probe_app.py")
    probe_app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe_app)
    root = "C:\\Users\\maukt\\AppData\\Local\\Temp\\doppel-probe-x\\app"
    win = [root]
    facts = {"database": {"configured": root.lower() + "\\instance\\b.db",       # Windows ignores case
                          "uri": "sqlite:///" + root.replace("\\", "/") + "/app.db"},
             "errors": [{"traceback": f'File "{root}\\a.py", line 3'}]}
    out = probe_app.scrub(facts, win)
    assert out["database"] == {"configured": "<repo>/instance/b.db", "uri": "sqlite:///<repo>/app.db"}
    assert out["errors"][0]["traceback"] == 'File "<repo>/a.py", line 3'
    assert probe_app.scrub("/app/instance/x.sqlite", ["/app"]) == "<repo>/instance/x.sqlite"
