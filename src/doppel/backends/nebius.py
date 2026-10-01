"""Nebius backend: the twin runs in Nebius Token Factory Sandboxes (ConTree).

Checkpoints (each a non-disposable sandbox image you can run from any number of times):

    image ─ upload base code ─ install ─ seed ──► SEEDED
    SEEDED ─ migrate ───────────────────────────► BASE world
    SEEDED ─ overlay head code ─ install ─ migrate ► HEAD world

Then every persona runs as one disposable run forked from its world: the app starts in the
background, runner.py (stdlib) replays the persona against it, and prints the recording.
Base and head personas all run in parallel, so no persona can see another's side effects.

Needs NEBIUS_API_KEY and NEBIUS_PROJECT_ID (pip install "doppel[nebius]").
"""
from __future__ import annotations

import asyncio
import json
import shlex
from pathlib import Path

from doppel.spec import TwinSpec

APP = "/app"
SKIP_PARTS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules", ".ruff_cache"}
RUNNER = Path(__file__).resolve().parent.parent / "runner.py"
PORT = 8000
MAX_PARALLEL = 40          # the beta allows 50 concurrent operations; leave headroom
PERSONA_TIMEOUT = 300
OUTPUT_LIMIT = 8 * 1024 * 1024  # default is 64 KB, too small for a persona's recording


def code_files(root: str | Path) -> dict[str, Path]:
    """{"app/relative/path": local path} for every file outside caches, venvs and .git."""
    root = Path(root).resolve()
    return {f"{APP[1:]}/{p.relative_to(root).as_posix()}": p for p in sorted(root.rglob("*"))
            if p.is_file() and not SKIP_PARTS & set(p.relative_to(root).parts)}


def persona_command(spec: TwinSpec) -> str:
    """Start the app in the background, replay one persona, stop the app, keep runner's exit code."""
    return (f"cd {APP} && (PORT={PORT} {spec.start} > /tmp/app.log 2>&1 & echo $! > /tmp/app.pid); "
            f"python3 /doppel/runner.py --base-url http://127.0.0.1:{PORT} --personas /doppel/persona.json "
            f"--ready-path {shlex.quote(spec.ready)}; rc=$?; "
            f"kill $(cat /tmp/app.pid) 2>/dev/null; "
            f"if [ $rc -ne 0 ]; then echo '--- app log ---' >&2; tail -n 40 /tmp/app.log >&2; fi; exit $rc")


class NebiusTwin:
    def __init__(self, client, spec: TwinSpec, log=print):
        self.client, self.spec, self.log = client, spec, log
        self.cost = 0.0
        self.runs = 0

    async def _step(self, image, shell: str, what: str):
        """Non-disposable run: the result is a new checkpoint."""
        out = await image.run(shell=f"cd {APP} && {shell}", disposable=False, timeout=1200)
        self._count(out)
        if out.exit_code != 0:
            raise RuntimeError(f"{what} failed ({shell}):\n{(out.stdout or '')[-2000:]}{(out.stderr or '')[-2000:]}")
        return out

    def _count(self, image) -> None:
        self.runs += 1
        try:
            self.cost += float(image.result.cost or 0)
        except Exception:  # noqa: BLE001 - cost is informational only
            pass

    async def build_worlds(self, base_dir, head_dir) -> dict:
        s = self.spec
        img = await self.client.images.use(s.image)
        img = await img.apply_files(code_files(base_dir))
        self._count(img)
        if s.install:
            img = await self._step(img, s.install, "install (base)")
        if s.seed:
            img = await self._step(img, s.seed, "seed")
        seeded = img
        self.log(f"[twin] seeded checkpoint {seeded.uuid}")

        base = await self._step(seeded, s.migrate, "migrate (base)") if s.migrate else seeded
        head = await seeded.apply_files(code_files(head_dir))
        self._count(head)
        if s.install:
            head = await self._step(head, s.install, "install (head)")
        if s.migrate:
            head = await self._step(head, s.migrate, "migrate (head)")
        self.log(f"[twin] base world {base.uuid} | head world {head.uuid}")
        return {"base": base, "head": head}

    async def replay_persona(self, world, persona: dict, sem: asyncio.Semaphore) -> dict:
        files = {"doppel/runner.py": RUNNER.read_bytes(),
                 "doppel/persona.json": json.dumps([persona]).encode()}
        async with sem:
            out = await world.run(shell=persona_command(self.spec), files=files, timeout=PERSONA_TIMEOUT,
                                  truncate_output_at=OUTPUT_LIMIT)
        self._count(out)
        text = (out.stdout or "").strip()
        try:
            recordings = json.loads(text.splitlines()[-1]) if text else None
        except ValueError:
            recordings = None
        if out.exit_code != 0 or not isinstance(recordings, list) or not recordings:
            raise RuntimeError(f"persona '{persona.get('name')}' failed (exit {out.exit_code}):\n"
                               f"{text[-1500:]}\n{(out.stderr or '')[-1500:]}")
        return recordings[0]

    async def run(self, base_dir, head_dir, personas: list[dict]) -> dict:
        worlds = await self.build_worlds(base_dir, head_dir)
        sem = asyncio.Semaphore(MAX_PARALLEL)
        jobs = [self.replay_persona(worlds[label], p, sem) for label in ("base", "head") for p in personas]
        results = await asyncio.gather(*jobs)
        n = len(personas)
        self.log(f"[twin] {2 * n} persona runs | {self.runs} sandbox runs | reported cost {self.cost:.4f}")
        return {"base": list(results[:n]), "head": list(results[n:]), "sandbox_cost": self.cost}


def make_client():
    from contree_sdk import Contree
    from contree_sdk.config import ContreeConfig
    return Contree(config=ContreeConfig(operation_poll_secs_min=0.05, operation_poll_secs_max=1.0))


def run_twin(base_dir, head_dir, spec: TwinSpec, personas: list[dict], client=None) -> dict:
    """Same contract as backends.local.run_twin: {"base": recordings, "head": recordings}."""
    twin = NebiusTwin(client or make_client(), spec)
    return asyncio.run(twin.run(base_dir, head_dir, personas))
