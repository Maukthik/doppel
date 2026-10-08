"""Approach 2 (seed through the app's own routes) and approach 3 (the setup agent in a live sandbox)."""
import json
import sys
import time
from pathlib import Path

import pytest

from doppel import agent, bootstrap
from doppel.backends.local import run_twin
from doppel.spec import TwinSpec, load_seed_steps
from tests.test_init import SHOP_SPEC, ScriptedModel, reply, shop_without_spec

NOTES_APP = '''
import json, os, secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

DB = "data/notes.json"   # the app's own store; only the app knows where it is and how it hashes
SESSIONS = {}


def load():
    return json.load(open(DB)) if os.path.exists(DB) else {"users": {}, "notes": []}


def save(db):
    json.dump(db, open(DB, "w"))


class App(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, status, body, headers=()):
        data = json.dumps(body).encode()
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def user(self):
        c = dict(p.strip().split("=", 1) for p in self.headers.get("Cookie", "").split(";") if "=" in p)
        return SESSIONS.get(c.get("sid"))

    def do_GET(self):
        if self.path == "/health":
            return self.send(200, {"ok": True})
        if self.path == "/notes":
            return self.send(200, load()["notes"])
        self.send(404, {})

    def do_POST(self):
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode()).items()}
        db = load()
        if self.path == "/signup":
            if not form.get("username") or len(form.get("password", "")) < 8:
                return self.send(400, {"error": "username and an 8+ character password required"})
            db["users"][form["username"]] = "hashed:" + form["password"][::-1]
            save(db)
            return self.send(201, {"user": form["username"]})
        if self.path == "/login":
            if db["users"].get(form.get("username")) != "hashed:" + form.get("password", "")[::-1]:
                return self.send(401, {"error": "bad login"})
            sid = secrets.token_hex(4)
            SESSIONS[sid] = form["username"]
            return self.send(200, {"ok": True}, [("Set-Cookie", f"sid={sid}; Path=/")])
        if self.path == "/notes":
            if not self.user():
                return self.send(403, {"error": "log in"})
            db["notes"].append({"by": self.user(), "text": form.get("text", "")})
            save(db)
            return self.send(201, db["notes"][-1])
        self.send(404, {})


os.makedirs("data", exist_ok=True)
ThreadingHTTPServer(("127.0.0.1", int(os.environ["PORT"])), App).serve_forever()
'''
FRONT_DOOR = [
    {"method": "POST", "path": "/signup", "form": {"username": "alice", "password": "doppel-pass"}},
    {"method": "POST", "path": "/login", "form": {"username": "alice", "password": "doppel-pass"}},
    {"method": "POST", "path": "/notes", "form": {"text": "seeded through the app"}},
]
SPEC = '[app]\nstart = "python app.py"\nready = "/health"\nseed_requests = "doppel_seed.json"\n'


def notes_app(tmp_path: Path, steps=FRONT_DOOR) -> Path:
    app = tmp_path / "notes"
    app.mkdir()
    (app / "app.py").write_text(NOTES_APP, encoding="utf-8")
    (app / "doppel.toml").write_text(SPEC, encoding="utf-8")
    (app / "doppel_seed.json").write_text(json.dumps(steps), encoding="utf-8")
    return app


# ---- approach 2: seeding through the front door ----------------------------------------------

def test_the_spec_loads_the_front_door_steps(tmp_path):
    app = notes_app(tmp_path)
    spec = TwinSpec.load(app / "doppel.toml")
    assert spec.seed_requests == "doppel_seed.json" and spec.seed_steps == FRONT_DOOR
    (app / "doppel_seed.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        TwinSpec.load(app / "doppel.toml")
    (app / "doppel_seed.json").unlink()
    with pytest.raises(ValueError, match="no such file"):
        TwinSpec.load(app / "doppel.toml")
    with pytest.raises(ValueError, match="every path starting with /"):
        (app / "doppel_seed.json").write_text('[{"method": "POST", "path": "signup"}]', encoding="utf-8")
        load_seed_steps(app, "doppel_seed.json")


def test_both_worlds_start_from_data_the_app_created_itself(tmp_path):
    app = notes_app(tmp_path)
    spec = TwinSpec.load(app / "doppel.toml")
    reader = {"name": "reader", "steps": [{"method": "GET", "path": "/notes"},
                                          {"method": "POST", "path": "/login",
                                           "form": {"username": "alice", "password": "doppel-pass"}}]}
    out = run_twin(app, app, spec, [reader])
    for world in ("base", "head"):
        steps = out[world][0]["steps"]
        assert steps[0]["body"] == [{"by": "alice", "text": "seeded through the app"}]
        assert steps[1]["status"] == 200   # alice exists, with the password as the app stored it
    assert "[seed_requests] POST /signup -> 201; POST /login -> 200; POST /notes -> 201" in out["seed_log"]


def test_a_refused_seed_step_stops_the_build_and_says_which_one(tmp_path):
    bad = [{"method": "POST", "path": "/signup", "form": {"username": "alice", "password": "short"}}]
    app = notes_app(tmp_path, bad)
    with pytest.raises(RuntimeError) as e:
        run_twin(app, app, TwinSpec.load(app / "doppel.toml"), [{"name": "r", "steps": [{"path": "/health"}]}])
    assert "seed_requests failed" in str(e.value) and "step 1: POST /signup -> 400" in str(e.value)
    assert "8+ character password" in str(e.value)


def test_init_accepts_a_front_door_seed(tmp_path):
    app = notes_app(tmp_path)
    for f in ("doppel.toml", "doppel_seed.json"):
        (app / f).unlink()
    files = {"files": [{"path": "doppel.toml", "content": SPEC},
                       {"path": "doppel_seed.json", "content": json.dumps(FRONT_DOOR)}], "notes": "front door"}
    users = [{"name": "reader", "steps": [{"method": "GET", "path": "/notes"}]}]
    model = ScriptedModel([json.dumps(files)], personas=users)
    result = bootstrap.init(app, backend="local", tries=1, chat=model, log=lambda m: None, agent=False)
    assert result["ok"] and set(result["files"]) == {"doppel.toml", "doppel_seed.json"}
    assert "doppel_seed.json" in model.prompts[0] and "PREFERRED" in model.prompts[0]


def test_front_door_checks_before_booting():
    steps = json.dumps(FRONT_DOOR)
    assert bootstrap.check_seed_requests(SPEC, {}) == ["doppel.toml sets seed_requests but no doppel_seed.json "
                                                       "was returned"]
    assert "would never run" in bootstrap.check_seed_requests('[app]\nstart = "x"\n', {"doppel_seed.json": steps})[0]
    assert bootstrap.check_seed_requests(SPEC, {"doppel_seed.json": steps}) == []
    login = SPEC + '[personas.login]\nmethod = "POST"\npath = "/login"\nform = { username = "alice", password = "x" }\n'
    assert bootstrap.check_login_matches_seed(login, "", steps)   # the login's password is not one it signed up with


# ---- approach 3: the setup agent ---------------------------------------------------------------

PY = f'"{sys.executable}"'


def test_the_workspace_keeps_files_and_never_waits_for_a_background_process(tmp_path):
    (tmp_path / "repo").mkdir()
    ws = agent.LocalWorkspace(tmp_path / "repo")
    try:
        assert "exit 0" in ws.shell(f'{PY} -c "open(\'made.txt\', \'w\').write(\'kept\')"')
        assert "kept" in ws.shell(f'{PY} -c "print(open(\'made.txt\').read())"')
        t0 = time.monotonic()
        out = ws.shell(f'{PY} -c "import subprocess, sys; subprocess.Popen([sys.executable, \'-c\', '
                       f'\'import time; time.sleep(60)\']); print(\'started\')"')
        assert "started" in out and time.monotonic() - t0 < 30
    finally:
        ws.close()


def test_init_hands_failing_drafts_to_the_agent_which_fixes_them(tmp_path):
    app = shop_without_spec(tmp_path)
    broken = SHOP_SPEC.replace('start = "python app.py"', 'start = "python server.py"')
    actions = [
        {"tool": "shell", "command": f'{PY} -c "import os; print(sorted(os.listdir()))"'},
        {"tool": "read", "path": "app.py"},
        {"tool": "serve", "start": "python app.py", "ready": "/health",
         "steps": [{"method": "GET", "path": "/products"}]},
        {"tool": "shell", "command": f"{PY} seed.py"},              # the schema and data persist in the sandbox
        {"tool": "serve", "start": "python app.py", "ready": "/health",
         "steps": [{"method": "GET", "path": "/products"}]},
        {"tool": "try", "files": {"doppel.toml": broken}},
        "not json at all",
        {"tool": "try", "files": {"doppel.toml": SHOP_SPEC}},
    ]
    model = ScriptedModel([reply(broken), reply(broken)], agent=actions)

    def chat(m, prompt, temperature=0.7):
        if prompt.startswith("You are setting up") and model.agent and isinstance(model.agent[0], str):
            model.agent_prompts.append(prompt)
            return model.agent.pop(0), 1, 1
        return model(m, prompt, temperature)
    logs = []
    result = bootstrap.init(app, backend="local", tries=2, chat=chat, log=logs.append, trace=tmp_path / "trace")
    assert result["ok"] and result["agent"] and result["attempts"] == 2
    assert result["agent_steps"] == 8 and result["agent_tries"] == 2
    assert 'start = "python app.py"' in result["files"]["doppel.toml"]
    prompts = model.agent_prompts
    assert "python server.py" in prompts[0]                         # it sees the setup that got furthest
    assert "'app.py'" in prompts[1] and "app.py" in prompts[2]       # shell output, then the file it read
    assert "no such table: products" in prompts[3]                   # the running app's own error, from its log
    assert "GET /products -> 200" in prompts[5]                      # after seeding through shell, it answers
    assert "FAILED" in prompts[6]                                    # the wrong try came back with its error
    assert "Reply with exactly one JSON object" in prompts[7]        # a non-JSON reply is nudged, not fatal
    assert any("handing over to the setup agent" in m for m in logs)
    assert (tmp_path / "trace" / "agent.txt").exists()


def test_an_agent_that_gives_up_leaves_the_drafts_error(tmp_path):
    app = shop_without_spec(tmp_path)
    broken = SHOP_SPEC.replace('start = "python app.py"', 'start = "python server.py"')
    model = ScriptedModel([reply(broken)], agent=[{"tool": "give_up", "reason": "needs Redis"}])
    result = bootstrap.init(app, backend="local", tries=1, chat=model, log=lambda m: None)
    assert not result["ok"] and result["agent_gave_up"] == "needs Redis" and "server.py" in result["error"]


def test_the_agent_stops_after_its_tries(tmp_path):
    app = shop_without_spec(tmp_path)
    broken = SHOP_SPEC.replace('start = "python app.py"', 'start = "python server.py"')
    model = ScriptedModel([reply(broken)], agent=[{"tool": "try", "files": {"doppel.toml": broken}}] * 9)
    result = bootstrap.init(app, backend="local", tries=1, chat=model, log=lambda m: None)
    assert not result["ok"] and result["agent_tries"] == agent.MAX_TRIES
    assert len(model.agent_prompts) == agent.MAX_TRIES


def test_a_try_that_fails_the_preboot_checks_costs_no_sandbox(tmp_path):
    app = shop_without_spec(tmp_path)
    broken = SHOP_SPEC.replace('start = "python app.py"', 'start = "python server.py"')
    bad_toml = SHOP_SPEC + "\n[app\n"
    model = ScriptedModel([reply(broken)], agent=[{"tool": "try", "files": {"doppel.toml": bad_toml}},
                                                  {"tool": "try", "files": {"doppel.toml": SHOP_SPEC}}])
    result = bootstrap.init(app, backend="local", tries=1, chat=model, log=lambda m: None)
    assert result["ok"] and "checked before booting" in model.agent_prompts[1]


def _alive(pid: int) -> bool:
    import os
    import subprocess
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_no_app_outlives_its_run_even_behind_a_launcher_or_shell(tmp_path):
    # the app is started by a parent (like a Windows venv's python.exe launcher, or `cmd /c` / `sh -c` for
    # a chained command): stopping only the parent would leave the app running, holding its port and log
    app = notes_app(tmp_path)
    launcher = ('import subprocess, sys, os\n'
                'child = subprocess.Popen([sys.executable, "app.py"])\n'
                'open("app.pid", "w").write(str(child.pid))\n'
                'child.wait()\n')
    (app / "launcher.py").write_text(launcher, encoding="utf-8")
    spec = TwinSpec(start="python launcher.py", ready="/health")
    from doppel.backends.local import _serve_and_replay
    recs = _serve_and_replay(app, spec, [{"name": "r", "steps": [{"method": "GET", "path": "/health"}]}])
    assert recs[0]["steps"][0]["status"] == 200
    pid = int((app / "app.pid").read_text())
    for _ in range(25):
        if not _alive(pid):
            break
        time.sleep(0.2)
    assert not _alive(pid), "the app kept running after its run"
    (app / ".doppel-app.log").unlink()   # nothing holds the log any more


def test_the_agent_keeps_what_it_read_refuses_rereads_and_is_pushed_to_act(tmp_path):
    app = shop_without_spec(tmp_path)
    broken = SHOP_SPEC.replace('start = "python app.py"', 'start = "python server.py"')
    reads = [{"tool": "read", "path": p} for p in ("app.py", "seed.py", "app.py", "PR.md", "app.py")]
    actions = reads + [{"tool": "run", "cmd": f'{PY} -c "print(42)"'},       # "run" and "cmd": understood
                       {"tool": "try", "files": {"doppel.toml": SHOP_SPEC}}]
    model = ScriptedModel([reply(broken)], agent=actions)
    result = bootstrap.init(app, backend="local", tries=1, chat=model, log=lambda m: None)
    assert result["ok"] and result["agent_steps"] == 7
    p = model.agent_prompts
    assert "Step 1 of 20; try calls left: 4." in p[0]
    assert "You already read app.py at step 1" in p[3]                    # no second copy of the file
    assert p[6].count("def ") >= p[1].count("def ")                         # app.py is still there, in full
    assert "stop reading and test" in p[5]                                  # 4 reads in a row: a nudge
    assert "exit 0" in p[6] and "42" in p[6]                                # the aliased shell ran


def test_near_the_end_the_agent_is_told_to_try():
    text = agent._transcript([{"action": {"tool": "read", "path": "a"}, "result": "x", "tool": "read"}] * 18,
                             max_steps=20, tries_left=4)
    assert "make a try NOW" in text


def test_a_post_that_was_accepted_and_redirected_counts_as_served():
    from doppel.rehearsal import coverage
    rec = {"persona": "p", "steps": [
        {"request": "POST /api-auth/login/", "status": 404, "redirected_to": "/accounts/profile/"},   # logged in
        {"request": "GET /missing/", "status": 404, "redirected_to": "/elsewhere/"},                 # a real miss
        {"request": "POST /snippets/", "status": 201}]}
    assert coverage([rec])["served"] == 2
