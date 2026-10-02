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
import hashlib
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
RETRIES = 3


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
        await self.verify(base, base_dir, head, head_dir)
        return {"base": base, "head": head}

    async def verify(self, base, base_dir, head, head_dir) -> None:
        """Check, inside each world, that the files the PR changes hold that world's version.
        A twin that silently runs the same code twice would report "no changes" for every PR."""
        changed = changed_files(base_dir, head_dir)
        if not changed:
            self.log("[twin] warning: base and head code are identical")
            return
        for label, world, root in (("base", base, base_dir), ("head", head, head_dir)):
            expected = {rel: _sha(Path(root) / rel) for rel in changed}
            out = await world.run(shell=f"cd {APP} && python3 -c {shlex.quote(HASH_SCRIPT)}",
                                  files={"doppel/paths.json": json.dumps(changed).encode()}, timeout=120)
            self._count(out)
            try:
                found = json.loads((out.stdout or "").strip().splitlines()[-1])
            except (ValueError, IndexError):
                raise RuntimeError(f"could not verify the {label} world: {(out.stderr or '')[-500:]}") from None
            wrong = sorted(rel for rel in changed if found.get(rel) != expected[rel])
            if wrong:
                raise RuntimeError(f"the {label} world doesn't contain the {label} code for: {', '.join(wrong[:8])}")
        self.log(f"[twin] verified {len(changed)} changed file(s) in both worlds")

    async def replay_persona(self, world, persona: dict, sem: asyncio.Semaphore) -> dict:
        files = {"doppel/runner.py": RUNNER.read_bytes(),
                 "doppel/persona.json": json.dumps([persona]).encode()}
        for attempt in range(RETRIES):
            try:
                async with sem:
                    out = await world.run(shell=persona_command(self.spec), files=files, timeout=PERSONA_TIMEOUT,
                                          truncate_output_at=OUTPUT_LIMIT)
                break
            except Exception as e:  # noqa: BLE001 - retry network hiccups, re-raise anything else
                if attempt == RETRIES - 1 or not _transient(e):
                    raise
                await asyncio.sleep(2 * (attempt + 1))
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

    async def _replay_all(self, world, personas: list[dict], sem) -> list:
        """One recording (or the exception) per persona; one broken persona doesn't sink the run."""
        return await asyncio.gather(*(self.replay_persona(world, p, sem) for p in personas), return_exceptions=True)

    async def run(self, base_dir, head_dir, personas: list[dict], refine=None) -> dict:
        for attempt in range(RETRIES):  # building talks to the API a lot: retry network hiccups
            try:
                worlds = await self.build_worlds(base_dir, head_dir)
                break
            except Exception as e:  # noqa: BLE001
                if attempt == RETRIES - 1 or not _transient(e):
                    raise
                self.log(f"[twin] {type(e).__name__} while building the worlds, retrying")
                await asyncio.sleep(5 * (attempt + 1))
        sem = asyncio.Semaphore(MAX_PARALLEL)
        rehearsal = None
        if refine is not None:  # rehearse on base, let the caller fix personas that got nowhere
            first = await self._replay_all(worlds["base"], personas, sem)
            rehearsal = [r for r in first if isinstance(r, dict)]
            personas = refine(personas, rehearsal)
            self.log(f"[twin] rehearsal: {len(rehearsal)} personas replayed on base")
        base, head = await asyncio.gather(self._replay_all(worlds["base"], personas, sem),
                                          self._replay_all(worlds["head"], personas, sem))
        out = pair(personas, base, head)
        failed = f" | {len(out['failed'])} personas failed" if out["failed"] else ""
        self.log(f"[twin] {2 * len(personas)} persona runs | {self.runs} sandbox runs | "
                 f"reported cost {self.cost:.4f}{failed}")
        return {**out, "personas": personas, "rehearsal": rehearsal, "sandbox_cost": self.cost}


HASH_SCRIPT = ("import hashlib,json,os;p=json.load(open('/doppel/paths.json'));"
               "print(json.dumps({r:(hashlib.sha256(open(r,'rb').read()).hexdigest() "
               "if os.path.exists(r) else None) for r in p}))")


def _sha(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def changed_files(base_dir, head_dir) -> list[str]:
    """App-relative paths (posix) that differ between the two code trees, added or removed included."""
    a = {k.split("/", 1)[1]: v for k, v in code_files(base_dir).items()}
    b = {k.split("/", 1)[1]: v for k, v in code_files(head_dir).items()}
    return sorted(rel for rel in set(a) | set(b)
                  if rel not in a or rel not in b or a[rel].read_bytes() != b[rel].read_bytes())


def _transient(e: Exception) -> bool:
    name = type(e).__name__.lower()
    return any(k in name for k in ("timeout", "timedout", "connect", "unavailable", "network"))


def pair(personas: list[dict], base: list, head: list) -> dict:
    """Keep personas that replayed in both worlds; report the rest instead of crashing the run."""
    ok_base, ok_head, failed = [], [], []
    for p, rb, rh in zip(personas, base, head, strict=True):
        if isinstance(rb, dict) and isinstance(rh, dict):
            ok_base.append(rb)
            ok_head.append(rh)
        else:
            err = rb if not isinstance(rb, dict) else rh
            failed.append({"persona": p.get("name"), "error": f"{type(err).__name__}: {str(err)[:300]}"})
    return {"base": ok_base, "head": ok_head, "failed": failed}


def make_client():
    from contree_sdk import Contree
    from contree_sdk.config import ContreeConfig
    return Contree(config=ContreeConfig(operation_poll_secs_min=0.05, operation_poll_secs_max=1.0))


def run_twin(base_dir, head_dir, spec: TwinSpec, personas: list[dict], client=None, refine=None) -> dict:
    """Same contract as backends.local.run_twin: {"base": recordings, "head": recordings, "failed": [...],
    "personas": the personas actually replayed (after refine), "rehearsal": base recordings or None}."""
    twin = NebiusTwin(client or make_client(), spec)
    return asyncio.run(twin.run(base_dir, head_dir, personas, refine))
