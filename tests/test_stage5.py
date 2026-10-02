"""Stage 5: what a real app needs from the twin. Logins (Basic auth, then a saved Bearer token),
shell commands in the spec, and personas that carry headers through validation."""
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from doppel.backends.local import _popen_args
from doppel.personas import validate
from doppel.runner import build_headers, replay

TOKEN = "t0k3n"


class AuthApp(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # POST /tokens with Basic alice:pw -> {"access_token": ...}
        expected = "Basic " + base64.b64encode(b"alice:pw").decode()
        if self.path == "/tokens" and self.headers.get("Authorization") == expected:
            return self._send(200, {"access_token": TOKEN})
        return self._send(401, {"error": "bad credentials"})

    def do_GET(self):  # GET /me needs the Bearer token
        if self.headers.get("Authorization") == f"Bearer {TOKEN}":
            return self._send(200, {"user": "alice"})
        return self._send(401, {"error": "login first"})


def serve():
    server = ThreadingHTTPServer(("127.0.0.1", 0), AuthApp)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def test_login_then_bearer_token():
    server, url = serve()
    try:
        persona = {"name": "alice", "headers": {"Authorization": "Bearer {token}"},
                   "steps": [{"method": "GET", "path": "/me"},
                             {"method": "POST", "path": "/tokens", "auth": ["alice", "pw"],
                              "save": {"token": "access_token"}},
                             {"method": "GET", "path": "/me"}]}
        steps = replay(url, [persona])[0]["steps"]
    finally:
        server.shutdown()
    assert [s["status"] for s in steps] == [401, 200, 200]
    assert steps[2]["body"] == {"user": "alice"}


def test_step_headers_override_persona_headers_and_auth_wins():
    persona = {"headers": {"X-A": "1", "Authorization": "Bearer {t}"}}
    step = {"headers": {"X-A": "{v}"}, "auth": ["u", "{p}"]}
    h = build_headers(persona, step, {"v": 2, "p": "secret", "t": "x"})
    assert h["X-A"] == "2"
    assert h["Authorization"] == "Basic " + base64.b64encode(b"u:secret").decode()


def test_validate_keeps_headers_and_auth():
    raw = {"personas": [{"name": "p", "headers": {"Authorization": "Bearer {token}"},
                         "steps": [{"method": "post", "path": "/api/tokens", "auth": ["a", "b"],
                                    "save": {"token": "access_token"}},
                                   {"method": "GET", "path": "/api/me", "headers": {"X-Trace": 1}}]}]}
    p = validate(raw)[0]
    assert p["headers"] == {"Authorization": "Bearer {token}"}
    assert p["steps"][0]["auth"] == ["a", "b"]
    assert p["steps"][1]["headers"] == {"X-Trace": "1"}


def test_spec_commands_use_a_shell_only_when_needed():
    assert "shell" not in _popen_args("gunicorn microblog:app")
    assert _popen_args("python seed.py && python more.py")["shell"] is True
    single = _popen_args("gunicorn -b :$PORT app:app")
    assert single["shell"] is True


# --- rehearsal: personas that never get past the login are found, fixed and reported -------------

def test_rehearsal_finds_stuck_personas_and_repairs_them_by_name(tmp_path):
    from doppel.rehearsal import coverage, is_stuck, repair
    server, url = serve()
    wrong = {"name": "json login", "goal": "read my profile",
             "steps": [{"method": "POST", "path": "/tokens", "json": {"username": "alice", "password": "pw"},
                        "save": {"token": "access_token"}},
                       {"method": "GET", "path": "/me", "headers": {"Authorization": "Bearer {token}"}}]}
    fine = {"name": "basic login", "steps": [{"method": "POST", "path": "/tokens", "auth": ["alice", "pw"]}]}
    try:
        recs = replay(url, [wrong, fine])
        assert [is_stuck(r) for r in recs] == [True, False]
        assert coverage(recs)["stuck_personas"] == ["json login"]

        def fake_nemotron(model, prompt, temperature=0.7):
            assert "-> 401" in prompt and "json login" in prompt and "basic login" not in prompt
            fixed = {**wrong, "steps": [{"method": "POST", "path": "/tokens", "auth": ["alice", "pw"],
                                         "save": {"token": "access_token"}}, wrong["steps"][1]]}
            return json.dumps({"personas": [fixed]}), 100, 50

        out = repair([wrong, fine], recs, tmp_path, chat=fake_nemotron)
        assert out["repaired"] == ["json login"] and out["personas"][1] is fine
        assert [s["status"] for s in replay(url, [out["personas"][0]])[0]["steps"]] == [200, 200]
    finally:
        server.shutdown()


def test_report_says_when_it_could_not_test():
    from doppel.report import markdown
    md = markdown([], 12, 54, None, {"requests": 54, "served": 2, "blocked": 44, "stuck_personas": ["a"]})
    assert md.startswith("## Doppel: couldn't test this PR (low coverage)")
    assert "served 2 of 54" in md
    ok = markdown([], 12, 54, None, {"requests": 54, "served": 40, "blocked": 4, "stuck_personas": []})
    assert ok.startswith("## Doppel: no behavior changes")


def test_a_login_token_nobody_saved_is_still_used():
    """Nemotron often writes the login step but forgets "save"; the runner captures the token."""
    server, url = serve()
    try:
        persona = {"name": "forgetful", "headers": {"Authorization": "Bearer {token}"},
                   "steps": [{"method": "POST", "path": "/tokens", "auth": ["alice", "pw"]},
                             {"method": "GET", "path": "/me"}]}
        steps = replay(url, [persona])[0]["steps"]
    finally:
        server.shutdown()
    assert [s["status"] for s in steps] == [200, 200]


def test_auto_capture_leaves_unrelated_names_alone():
    from doppel.runner import auto_capture
    v = {}
    auto_capture({"access_token": "a", "refresh_token": "r", "id": 7}, {"token", "author_id", "id"}, v)
    assert v == {"token": "a", "id": 7}
