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
from doppel import sandbox
from doppel.backends import nebius
from doppel.spec import TwinSpec

SHOP = Path(__file__).resolve().parent.parent / "examples" / "shop"


# --- fake ConTree ---------------------------------------------------------------

def _unpack(data: bytes) -> dict:
    """{"app/path": a local file holding that entry's bytes}, like the sandbox after `tar xzf`."""
    import io
    import tarfile
    import tempfile
    out, where = {}, Path(tempfile.mkdtemp(prefix="fake-sandbox-"))
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for m in tar.getmembers():
            dest = where / m.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(tar.extractfile(m).read())
            out[m.name] = str(dest)
    return out


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
        return type(self)(self.log, {**self.files, **files}, self.history + ("apply",))

    def run(self, shell=None, disposable=True, files=None, timeout=None, truncate_output_at=None):
        prepared = type(self)(self.log, self.files, self.history)
        prepared.req = (shell, disposable, files or {})
        return prepared

    def __await__(self):
        return self._exec().__await__()

    def _lay(self, held: dict, new: dict) -> dict:
        """Files laid on top of what the image holds: the new ones win, as in a real sandbox."""
        return {**held, **new}

    async def _exec(self):
        shell, disposable, files = self.req
        self.log.append(("run", shell, disposable, sorted(files), self.history))
        files = dict(files)
        parts = sorted(k for k in files if k.startswith(sandbox.UPLOAD_PATH + "."))
        if parts:   # the code as one archive in parts: join and unpack them like `cat ... && tar xzf ...` would
            unpacked = _unpack(b"".join(files[k] for k in parts))
            self.log.append(("apply", sorted(unpacked)))
            held = self._lay(self.files, unpacked)
        else:
            held = {**self.files, **files}
        out = type(self)(self.log, held, self.history + ((shell,) if not disposable else ()))
        if "doppel/paths.json" in files:  # world verification: hash the files as the sandbox holds them
            import hashlib
            paths = json.loads(files["doppel/paths.json"])
            held = {k.split("/", 1)[1]: v for k, v in self.files.items() if k.startswith("app/")}
            out.stdout = json.dumps({r: hashlib.sha256(Path(held[r]).read_bytes()).hexdigest()
                                     if r in held else None for r in paths})
        elif "runner.py" in shell:  # a persona run: answer like runner.py would
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


def test_nebius_twin_refuses_a_head_world_without_the_pr_code(tmp_path):
    """If the sandbox ever ends up with base code in the head world, say so instead of reporting
    "no changes" (this is what a silent no-op patch looked like in the first benchmark runs)."""
    base, head = tmp_path / "base", tmp_path / "head"
    for d, body in ((base, "BASE"), (head, "HEAD")):
        d.mkdir()
        (d / "app.py").write_text(body)

    class DroppingImage(FakeImage):
        def _lay(self, held, new):  # existing files win: the PR's version never lands
            return {**new, **held}

    client = FakeClient()
    client.images = SimpleNamespace(use=lambda tag: _ret(DroppingImage(client.log)))
    twin = nebius.NebiusTwin(client, TwinSpec(start="python app.py"), log=lambda *_: None)
    with pytest.raises(RuntimeError, match="head world doesn't contain the head code"):
        asyncio.run(twin.run(base, head, [{"name": "p", "steps": [{"method": "GET", "path": "/x"}]}]))


async def _ret(value):
    return value


def test_persona_command_stops_the_app_and_keeps_the_exit_code():
    cmd = nebius.persona_command(TwinSpec(start="python app.py", ready="/health"))
    assert "export PORT=8000; python app.py" in cmd and "--ready-path /health" in cmd
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
    # 1 persona of the 3 asked for is too few: Lightning is asked twice, then Ultra once, all priced
    assert out["usd"] == pytest.approx((2 * (20000 * 0.06 + 1500 * 0.24) + 20000 * 1.0 + 1500 * 3.0) / 1e6)


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



def test_documentation_media_and_huge_files_stay_out_of_the_sandbox(tmp_path, monkeypatch):
    # label-studio: 418 MB of gifs and videos in docs/ made a 504 MB upload
    (tmp_path / "docs" / "img").mkdir(parents=True)
    (tmp_path / "docs" / "img" / "demo.gif").write_bytes(b"GIF89a" + b"x" * 100)
    (tmp_path / "docs" / "conf.py").write_text("x = 1\n")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "logo.png").write_bytes(b"png")          # not in docs: kept (a test may serve it)
    (tmp_path / "app" / "big.bin").write_bytes(b"0" * 2000)
    monkeypatch.setattr(nebius, "HUGE_FILE", 1000)
    kept = set(nebius.code_files(tmp_path))
    assert kept == {"app/docs/conf.py", "app/app/logo.png"}
    assert set(nebius.left_out_summary(tmp_path).split(", ")) == {"1 documentation media (0.0 MB)",
                                                                  "1 files over 25 MB (0.0 MB)"}
