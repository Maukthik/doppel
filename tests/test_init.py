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
    assert not result["ok"] and result["attempts"] == 2 and "invalid" in result["error"]


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
