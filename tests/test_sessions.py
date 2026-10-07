"""Personas behave like one browser each: session cookies carry over, forms post, CSRF tokens go back."""
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import pytest

from doppel import runner
from doppel.personas import validate


class SessionApp(BaseHTTPRequestHandler):
    """Django-like: GET /login sets csrftoken; POST /login (a form) needs it back as X-CSRFToken and
    sets a session cookie; /me and POST /notes need the session; logging in redirects to /me."""
    sessions: dict = {}

    def log_message(self, *a):
        pass

    def cookies(self):
        raw = self.headers.get("Cookie", "")
        return dict(p.strip().split("=", 1) for p in raw.split(";") if "=" in p)

    def send(self, status, body=None, headers=()):
        data = json.dumps(body).encode() if body is not None else b""
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def user(self):
        return self.sessions.get(self.cookies().get("sessionid"))

    def do_GET(self):
        if self.path == "/login":
            return self.send(200, {"form": "login"}, [("Set-Cookie", "csrftoken=tok123; Path=/")])
        if self.path == "/me":
            return self.send(200, {"user": self.user()}) if self.user() else self.send(403, {"error": "login"})
        self.send(404, {})

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        token = self.cookies().get("csrftoken")
        if not token or self.headers.get("X-CSRFToken") != token:
            return self.send(403, {"error": "CSRF failed"})
        if self.path == "/login":
            form = {k: v[0] for k, v in parse_qs(body).items()}
            if self.headers.get("Content-Type") != "application/x-www-form-urlencoded" or \
                    (form.get("username"), form.get("password")) != ("alice", "pw"):
                return self.send(400, {"error": "bad login"})
            sid = secrets.token_hex(8)
            self.sessions[sid] = "alice"
            return self.send(302, None, [("Set-Cookie", f"sessionid={sid}; Path=/"), ("Location", "/me")])
        if self.path == "/notes":
            return self.send(201, {"by": self.user()}) if self.user() else self.send(403, {"error": "login"})
        self.send(404, {})


@pytest.fixture(scope="module")
def app():
    server = ThreadingHTTPServer(("127.0.0.1", 0), SessionApp)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


LOGIN = [{"method": "GET", "path": "/login"},
         {"method": "POST", "path": "/login", "form": {"username": "alice", "password": "pw"}}]


def statuses(app, persona):
    return [s["status"] for s in runner.replay(app, [persona])[0]["steps"]]


def test_a_session_login_carries_over_to_later_steps(app):
    persona = {"name": "p", "steps": LOGIN + [{"method": "GET", "path": "/me"},
                                              {"method": "POST", "path": "/notes", "json": {"t": "hi"}}]}
    # the login redirect lands on /me (200); the session and the CSRF token work for later requests
    assert statuses(app, persona) == [200, 200, 200, 201]


def test_personas_do_not_share_cookies(app):
    logged_in = {"name": "a", "steps": LOGIN}
    stranger = {"name": "b", "steps": [{"method": "GET", "path": "/me"}]}
    recs = runner.replay(app, [logged_in, stranger])
    assert recs[1]["steps"][0]["status"] == 403


def test_a_form_post_without_the_csrf_cookie_is_refused_like_in_a_browser(app):
    persona = {"name": "p", "steps": [LOGIN[1]]}   # never loaded the login page, so no csrftoken yet
    assert statuses(app, persona) == [403]


def test_validate_keeps_form_steps():
    raw = {"personas": [{"name": "p", "steps": [{"method": "post", "path": "/login",
                                                 "form": {"username": "alice", "n": 3, "tags": ["a"]}}]}]}
    step = validate(raw)[0]["steps"][0]
    assert step == {"method": "POST", "path": "/login", "form": {"username": "alice", "n": 3, "tags": '["a"]'}}
