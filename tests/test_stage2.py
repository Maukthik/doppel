"""Stage 2 offline tests: the Nebius backend against an in-memory fake sandbox, and persona
generation against a fake Nemotron. No network, no credits."""
import asyncio
import json
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from doppel import personas as gen
from doppel.backends import nebius
from doppel.spec import TwinSpec

SHOP = Path(__file__).resolve().parent.parent / "examples" / "shop"


# --- fake ConTree ---------------------------------------------------------------

class FakeImage:
    """Mimics contree_sdk's async image: run() prepares, awaiting executes; apply_files bakes files in."""

    def __init__(self, log, files=None, history=()):
        self.log, self.files, self.history, self.uuid = log, dict(files or {}), tuple(history), uuid.uuid4()
        self.req = None
        self.stdout = self.stderr = ""
        self.exit_code = 0
        self.result = SimpleNamespace(cost=0.001)
        self.elapsed = timedelta(seconds=0.1)

    async def apply_files(self, files):
        self.log.append(("apply", sorted(files)))
        return FakeImage(self.log, {**self.files, **files}, self.history + ("apply",))

    def run(self, shell=None, disposable=True, files=None, timeout=None, truncate_output_at=None):
        prepared = FakeImage(self.log, self.files, self.history)
        prepared.req = (shell, disposable, files or {})
        return prepared

    def __await__(self):
        return self._exec().__await__()

    async def _exec(self):
        shell, disposable, files = self.req
        self.log.append(("run", shell, disposable, sorted(files), self.history))
        out = FakeImage(self.log, {**self.files, **files}, self.history + ((shell,) if not disposable else ()))
        if "runner.py" in shell:  # a persona run: answer like runner.py would
            persona = json.loads(files["doppel/persona.json"])[0]
            app = next(v for k, v in self.files.items() if k == "app/app.py")
            world = "head" if Path(app).read_text() == "HEAD" else "base"
            out.stdout = json.dumps([{"persona": persona["name"], "steps": [
                {"i": 0, "request": "GET /x", "sent": None, "status": 200, "body": {"world": world}, "ms": 5}]}])
        return out


class FakeClient:
    def __init__(self):
        self.log = []
        self.images = SimpleNamespace(use=self._use)

    async def _use(self, tag):
        self.log.append(("use", tag))
        return FakeImage(self.log)


def test_nebius_twin_builds_checkpoints_and_forks_every_persona(tmp_path):
    base, head = tmp_path / "base", tmp_path / "head"
    for d, body in ((base, "BASE"), (head, "HEAD")):
        d.mkdir()
        (d / "app.py").write_text(body)
        (d / "seed.py").write_text("")
    spec = TwinSpec(start="python app.py", ready="/health", install="pip install -r requirements.txt",
                    seed="python seed.py", migrate="python migrate.py")
    client = FakeClient()
    personas = [{"name": f"p{i}", "steps": [{"method": "GET", "path": "/x"}]} for i in range(5)]

    twin = nebius.NebiusTwin(client, spec, log=lambda *_: None)
    worlds = asyncio.run(twin.run(base, head, personas))

    # every persona ran in both worlds, and each world answered as itself
    names = [f"p{i}" for i in range(5)]
    assert [r["persona"] for r in worlds["base"]] == [r["persona"] for r in worlds["head"]] == names
    assert {r["steps"][0]["body"]["world"] for r in worlds["base"]} == {"base"}
    assert {r["steps"][0]["body"]["world"] for r in worlds["head"]} == {"head"}

    runs = [e for e in client.log if e[0] == "run"]
    checkpoints = [e[1] for e in runs if e[2] is False]
    # seed once; install in both worlds; migrate in both worlds
    assert sum("python seed.py" in c for c in checkpoints) == 1
    assert sum("pip install" in c for c in checkpoints) == 2
    assert sum("python migrate.py" in c for c in checkpoints) == 2
    persona_runs = [e for e in runs if "runner.py" in e[1]]
    assert len(persona_runs) == 10 and all(e[2] is True for e in persona_runs)       # disposable forks
    assert all(e[3] == ["doppel/persona.json", "doppel/runner.py"] for e in persona_runs)
    # personas fork from finished worlds: seed happened before every persona run
    assert all(any("seed.py" in h for h in e[4]) for e in persona_runs)
    assert twin.runs >= 16 and twin.cost > 0


def test_persona_command_stops_the_app_and_keeps_the_exit_code():
    cmd = nebius.persona_command(TwinSpec(start="python app.py", ready="/health"))
    assert "PORT=8000 python app.py" in cmd and "--ready-path /health" in cmd
    assert "kill $(cat /tmp/app.pid)" in cmd and cmd.rstrip().endswith("exit $rc")


def test_code_files_skips_caches(tmp_path):
    (tmp_path / "app.py").write_text("x")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "app.pyc").write_text("x")
    assert list(nebius.code_files(tmp_path)) == ["app/app.py"]


# --- personas -------------------------------------------------------------------

def test_validate_keeps_good_steps_and_drops_bad_ones():
    raw = {"personas": [
        {"name": "a", "steps": [{"method": "get", "path": "/products"},
                                {"method": "TRACE", "path": "/x"},
                                {"method": "POST", "path": "orders"},
                                {"method": "POST", "path": "/orders", "json": {"qty": 0}, "save": {"o": "id"}}]},
        {"name": "a", "steps": [{"method": "GET", "path": "/health"}]},
        {"name": "empty", "steps": []},
        "garbage"]}
    out = gen.validate(raw)
    assert [p["name"] for p in out] == ["a", "a'"]
    assert out[0]["steps"] == [{"method": "GET", "path": "/products"},
                               {"method": "POST", "path": "/orders", "json": {"qty": 0}, "save": {"o": "id"}}]


def test_generate_sends_source_and_diff_and_prices_the_call():
    seen = {}

    def fake_chat(model, prompt):
        seen["model"], seen["prompt"] = model, prompt
        return ('Sure! ```json\n{"personas": [{"name": "coupon fan", "goal": "uses SAVE10", '
                '"steps": [{"method": "POST", "path": "/orders", "json": {"user_id": 1, "items": '
                '[{"product_id": 2, "qty": 1}], "coupon": "SAVE10"}}]}]}\n```', 20000, 1500)

    out = gen.generate(SHOP / "base", n=3, head_dir=SHOP / "head", model="nvidia/Nemotron-3_5-Lightning",
                       chat=fake_chat)
    assert out["personas"][0]["name"] == "coupon fan"
    assert "### app.py" in seen["prompt"] and "exactly 3 personas" in seen["prompt"]
    assert "with_tax" in seen["prompt"]                      # the PR diff is in the prompt
    assert out["usd"] == pytest.approx((20000 * 0.06 + 1500 * 0.24) / 1e6)


def test_generate_fails_loudly_on_unusable_output():
    with pytest.raises(RuntimeError):
        gen.generate(SHOP / "base", chat=lambda m, p: ("I cannot help with that", 10, 5))


def test_parse_json_survives_reasoning_before_the_answer():
    reply = ('Here\'s a thinking process:\n1. Return ONLY JSON with format: `{"personas": [persona, ...]}`\n'
             '2. Draft: {"personas": [{"name": "a"}]}\nFinal answer:\n'
             '{"personas": [{"name": "a", "steps": []}, {"name": "b", "steps": []}]}')
    assert [p["name"] for p in gen.parse_json(reply)["personas"]] == ["a", "b"]
    assert gen.parse_json("<think>{\"personas\": 1}</think>{\"personas\": []}") == {"personas": []}
    assert gen.parse_json("no json at all") is None
