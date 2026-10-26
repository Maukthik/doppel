"""Nebius backend: the twin runs in Nebius Token Factory Sandboxes (ConTree).

Checkpoints (each a non-disposable sandbox image you can run from any number of times):

    image ─ upload base code ─ install ─ seed ──► SEEDED
    SEEDED ─ migrate ───────────────────────────► BASE world
    SEEDED ─ overlay head code ─ install ─ migrate ► HEAD world

Then every persona runs as one disposable run forked from its world: the app starts in the
background, runner.py (stdlib) replays the persona against it, and prints the recording.
Base and head personas all run in parallel, so no persona can see another's side effects.

Needs NEBIUS_API_KEY and NEBIUS_PROJECT_ID (pip install "doppel-check[nebius]").
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shlex
from pathlib import Path

from doppel import sandbox
from doppel.errors import failure_text
from doppel.spec import TwinSpec
from doppel.timeline import Timeline

APP = "/app"
SKIP_PARTS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules", ".ruff_cache", ".mypy_cache",
              ".tox", ".nox"}
RUNNER = Path(__file__).resolve().parent.parent / "runner.py"
PORT = 8000
MAX_PARALLEL = 40          # the beta allows 50 concurrent operations; leave headroom
PERSONA_TIMEOUT = 300
OUTPUT_LIMIT = 8 * 1024 * 1024  # default is 64 KB, too small for a persona's recording
RETRIES = 3


def removed_files(base_dir: str | Path, head_dir: str | Path) -> list[str]:
    """App-relative paths of code files the base has and the head does not (seeded data is not code)."""
    base = {k.split("/", 1)[1] for k in code_files(base_dir)}
    head = {k.split("/", 1)[1] for k in code_files(head_dir)}
    return sorted(base - head)


DOC_FOLDERS = {"docs", "doc"}
DOC_MEDIA = {".gif", ".mp4", ".mov", ".webm", ".avi", ".mkv", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif",
             ".tiff", ".ico", ".psd", ".pdf", ".zip", ".gz", ".tgz", ".7z", ".mp3", ".wav", ".ogg", ".ttf", ".otf",
             ".woff", ".woff2", ".eot"}
HUGE_FILE = 25 * 1024 * 1024


def left_out(rel: Path, path: Path) -> str:
    """Why a file stays out of the sandbox, or "": pictures and videos in a docs folder (label-studio's docs/ was
    418 MB of them) and any single file over 25 MB. Tests don't read either; the log says what was left out."""
    if DOC_FOLDERS & set(rel.parts[:-1]) and path.suffix.lower() in DOC_MEDIA:
        return "documentation media"
    if path.stat().st_size > HUGE_FILE:
        return "files over 25 MB"
    return ""


def _candidates(root: Path):
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if p.is_file() and not SKIP_PARTS & set(rel.parts):
            yield rel, p


def code_files(root: str | Path) -> dict[str, Path]:
    """{"app/relative/path": local path} for every file outside caches, venvs and .git (and see left_out)."""
    root = Path(root).resolve()
    return {f"{APP[1:]}/{rel.as_posix()}": p for rel, p in _candidates(root) if not left_out(rel, p)}


def left_out_summary(root: str | Path) -> str:
    """'312 documentation media (401.2 MB)' for the log, or "" when nothing was left out."""
    counts: dict[str, list[int]] = {}
    for rel, p in _candidates(Path(root).resolve()):
        if why := left_out(rel, p):
            c = counts.setdefault(why, [0, 0])
            c[0] += 1
            c[1] += p.stat().st_size
    return ", ".join(f"{n} {why} ({size / 1e6:.1f} MB)" for why, (n, size) in counts.items())


from doppel.runner import APP_LOG_LINES, APP_LOG_MARKER, attach_app_log  # noqa: E402


def persona_command(spec: TwinSpec, trace: bool = False) -> str:
    """Start the app in the background, replay one persona, stop the app, keep runner's exit code.
    With trace, Doppel's line tracer (doppel.reach) loads in the app and its hits go to stderr."""
    from doppel import reach
    hook = f"/doppel/{reach.TRACE_DIR}"
    tracing = (f"export PYTHONPATH={hook}${{PYTHONPATH:+:$PYTHONPATH}} {reach.TARGETS_ENV}={hook}/targets.json "
               f"{reach.OUT_ENV}=/tmp/doppel-reached.txt {reach.ROOT_ENV}={APP}; ") if trace else ""
    report = (f"echo '{reach.MARKER}' >&2; cat /tmp/doppel-reached.txt >&2 2>/dev/null; ") if trace else ""
    # export, not a `PORT=8000 cmd` prefix: a prefix only reaches the command's environment, so a
    # start command that spells the port out ("runserver 0.0.0.0:$PORT") would expand it to nothing
    return (f"cd {APP} && (export PORT={PORT}; {spec.exports()}{tracing}{spec.start} > /tmp/app.log 2>&1 "
            f"& echo $! > /tmp/app.pid); "
            f"python3 /doppel/runner.py --base-url http://127.0.0.1:{PORT} --personas /doppel/persona.json "
            f"--ready-path {shlex.quote(spec.ready)}; rc=$?; "
            f"kill $(cat /tmp/app.pid) 2>/dev/null; sleep 0.2; "
            f"echo '{APP_LOG_MARKER}' >&2; tail -n {APP_LOG_LINES} /tmp/app.log >&2; echo '{APP_LOG_MARKER}' >&2; "
            f"{report}"
            f"if [ $rc -ne 0 ]; then echo '--- app log ---' >&2; tail -n 40 /tmp/app.log >&2; fi; exit $rc")


class NebiusTwin:
    def __init__(self, client, spec: TwinSpec, log=print):
        self.client, self.spec, self.log = client, spec, log
        self.cost = 0.0
        self.runs = 0
        self.seed_log = ""  # what the seed printed, plus a scan of the SQLite databases right after it
        self.tl = Timeline("nebius")  # what happened inside the sandboxes, for doppel view and the site

    async def _install(self, image, repo, what: str):
        """Install with git provided when the dependencies need it; once more with the build tools when it fails
        for want of a compiler or system headers (doppel.sandbox)."""
        shell = sandbox.install_shell(self.spec.install, repo)
        try:
            return await self._step(image, self.spec.exports() + shell, what, shown=self.spec.install)
        except RuntimeError as e:
            if not sandbox.needs_build_tools(str(e)):
                raise
            self.log(f"[twin] {what} needs a compiler or system libraries: installing build tools, trying again")
            return await self._step(image, self.spec.exports() + sandbox.with_build_tools(shell),
                                    what + " (with build tools)", shown=self.spec.install)

    async def _step(self, image, shell: str, what: str, shown: str | None = None, kind: str = "checkpoint"):
        """Non-disposable run: the result is a new checkpoint. `shown` is the command as the user wrote it,
        for the error message (the seed step also carries Doppel's database scan, which nobody needs to read)."""
        with self.tl.span("build", what, kind=kind):
            out = await image.run(shell=f"cd {APP} && {shell}", disposable=False, timeout=1200)
        self._count(out)
        if out.exit_code != 0:
            raise RuntimeError(f"{what} failed ({shown or shell}):\n" + failure_text(out.stdout, out.stderr))
        return out

    async def _front_door_seed(self, image):
        """Start the app on the seeded image, replay seed_requests, keep what they wrote as the new checkpoint."""
        from doppel import frontdoor
        from doppel.dbscan import one_liner
        files = {"doppel/runner.py": RUNNER.read_bytes(),
                 "doppel/persona.json": json.dumps([frontdoor.seed_persona(self.spec)]).encode()}
        with self.tl.span("build", "seed through the app (seed_requests)", kind="checkpoint"):
            out = await image.run(shell=persona_command(self.spec), files=files, disposable=False,
                                  timeout=PERSONA_TIMEOUT, truncate_output_at=OUTPUT_LIMIT)
        self._count(out)
        text = (out.stdout or "").strip()
        try:
            recording = json.loads(text.splitlines()[-1])[0] if text else None
        except (ValueError, IndexError, KeyError):
            recording = None
        if out.exit_code != 0 or not isinstance(recording, dict):
            raise RuntimeError("seed_requests failed: the app did not start for them:\n"
                               + failure_text(out.stdout, out.stderr))
        parts = (out.stderr or "").split(APP_LOG_MARKER)
        recording = attach_app_log(recording, parts[1] if len(parts) >= 3 else "")
        problem = frontdoor.seed_problem(recording)
        if problem:
            raise RuntimeError(problem)
        scan = await out.run(shell=f"cd {APP} && {one_liner()} || true", disposable=True, timeout=120)
        self._count(scan)
        self.seed_log = (self.seed_log + "\n" + frontdoor.summary(recording) + "\n"
                         + (scan.stdout or "") + (scan.stderr or ""))[-4000:]
        self.log(f"[twin] {frontdoor.summary(recording)}")
        return out

    def _count(self, image) -> None:
        self.runs += 1
        try:
            self.cost += float(image.result.cost or 0)
        except Exception:  # noqa: BLE001 - cost is informational only
            pass

    async def build_worlds(self, base_dir, head_dir) -> dict:
        s = self.spec
        with self.tl.span("build", f"image {s.image}"):
            img = await self.client.images.use(s.image)
        with self.tl.span("build", "upload base code"):
            img = await sandbox.upload(img, code_files(base_dir), "uploading the base code", self.log)
        self._count(img)
        if s.install:
            img = await self._install(img, base_dir, "install (base)")
        if s.seed:
            from doppel.dbscan import one_liner
            # keep the seed's own exit code; the scan only reports and can never fail the step
            img = await self._step(img, f"{s.exports()}{s.seed}; rc=$?; {one_liner()} || true; exit $rc", "seed",
                                   shown=s.seed)
            self.seed_log = ((img.stdout or "") + (img.stderr or ""))[-4000:]
        if s.seed_steps:
            img = await self._front_door_seed(img)
        seeded = img
        self.log(f"[twin] seeded checkpoint {seeded.uuid}")

        base = await self._step(seeded, s.exports() + s.migrate, "migrate (base)") if s.migrate else seeded
        with self.tl.span("build", "fork head world (PR files on top)", kind="fork"):
            head = await sandbox.upload(seeded, code_files(head_dir), "uploading the head code", self.log)
        self._count(head)
        gone = removed_files(base_dir, head_dir)
        if gone:  # files the PR deletes: laying head's files on top would leave them in place
            head = await self._step(head, "rm -f -- " + " ".join(shlex.quote(r) for r in gone),
                                    f"remove {len(gone)} file(s) the PR deletes")
        if s.install:
            head = await self._install(head, head_dir, "install (head)")
        if s.migrate:
            head = await self._step(head, s.exports() + s.migrate, "migrate (head)")
        self.log(f"[twin] base world {base.uuid} | head world {head.uuid}")
        with self.tl.span("build", "verify both worlds hold their code"):
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

    async def replay_persona(self, world, persona: dict, sem: asyncio.Semaphore, watch: dict | None = None,
                             lane: str = "") -> dict:
        from doppel import reach
        files = {"doppel/runner.py": RUNNER.read_bytes(),
                 "doppel/persona.json": json.dumps([persona]).encode()}
        if watch:
            files[f"doppel/{reach.TRACE_DIR}/sitecustomize.py"] = reach.TRACER.encode()
            files[f"doppel/{reach.TRACE_DIR}/targets.json"] = json.dumps(watch).encode()
        name = persona.get("name", "persona")
        start = self.tl.now()
        for attempt in range(RETRIES):
            try:
                async with sem:
                    start = self.tl.now()
                    out = await world.run(shell=persona_command(self.spec, trace=bool(watch)), files=files,
                                          timeout=PERSONA_TIMEOUT,
                                          truncate_output_at=OUTPUT_LIMIT)
                break
            except Exception as e:  # noqa: BLE001 - retry network hiccups, re-raise anything else
                if attempt == RETRIES - 1 or not _transient(e):
                    if lane:
                        self.tl.add(lane, name, start, ok=False, kind="persona")
                    raise
                await asyncio.sleep(2 * (attempt + 1))
        self._count(out)
        text = (out.stdout or "").strip()
        try:
            recordings = json.loads(text.splitlines()[-1]) if text else None
        except ValueError:
            recordings = None
        if lane:
            ok = out.exit_code == 0 and isinstance(recordings, list) and bool(recordings)
            self.tl.add(lane, name, start, ok=ok, kind="persona",
                        requests=len(recordings[0].get("steps", [])) if ok else 0)
        if out.exit_code != 0 or not isinstance(recordings, list) or not recordings:
            raise RuntimeError(f"persona '{persona.get('name')}' failed (exit {out.exit_code}):\n"
                               f"{text[-1500:]}\n{(out.stderr or '')[-1500:]}")
        if watch:
            recordings[0]["reached"] = reach.parse_hits((out.stderr or "").split(APP_LOG_MARKER)[-1])
        parts = (out.stderr or "").split(APP_LOG_MARKER)
        return attach_app_log(recordings[0], parts[1] if len(parts) >= 3 else "")

    async def _replay_all(self, world, personas: list[dict], sem, watch: dict | None = None,
                          lane: str = "") -> list:
        """One recording (or the exception) per persona; one broken persona doesn't sink the run."""
        return await asyncio.gather(*(self.replay_persona(world, p, sem, watch, lane) for p in personas),
                                    return_exceptions=True)

    async def run(self, base_dir, head_dir, personas: list[dict], refine=None, watch: dict | None = None) -> dict:
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
            first = await self._replay_all(worlds["base"], personas, sem, lane="rehearsal")
            rehearsal = [r for r in first if isinstance(r, dict)]
            personas = refine(personas, rehearsal)
            self.log(f"[twin] rehearsal: {len(rehearsal)} personas replayed on base")
        watch = watch or {}
        base, head = await asyncio.gather(
            self._replay_all(worlds["base"], personas, sem, watch.get("base"), lane="base"),
            self._replay_all(worlds["head"], personas, sem, watch.get("head"), lane="head"))
        out = pair(personas, base, head)
        failed = f" | {len(out['failed'])} personas failed" if out["failed"] else ""
        self.log(f"[twin] {2 * len(personas)} persona runs | {self.runs} sandbox runs | "
                 f"reported cost {self.cost:.4f}{failed}")
        return {**out, "personas": personas, "rehearsal": rehearsal, "sandbox_cost": self.cost,
                "seed_log": self.seed_log, "timeline": {**self.tl.to_dict(), "sandbox_runs": self.runs}}


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
    return any(k in name for k in ("timeout", "timedout", "connect", "unavailable", "network", "transport", "reset"))


def pair(personas: list[dict], base: list, head: list) -> dict:
    """Keep personas that replayed in both worlds; report the rest instead of crashing the run."""
    ok_base, ok_head, failed = [], [], []
    for p, rb, rh in zip(personas, base, head, strict=True):
        if isinstance(rb, dict) and isinstance(rh, dict):
            ok_base.append(rb)
            ok_head.append(rh)
        else:
            err = rb if not isinstance(rb, dict) else rh
            world = "both" if not isinstance(rb, dict) and not isinstance(rh, dict) else \
                ("base" if not isinstance(rb, dict) else "head")
            failed.append({"persona": p.get("name"), "world": world,
                           "error": f"{type(err).__name__}: {str(err)[:300]}"})
    return {"base": ok_base, "head": ok_head, "failed": failed}


def make_client():
    from doppel.personas import require_nebius
    require_nebius(sandboxes=True)
    try:
        from contree_sdk import Contree
        from contree_sdk.config import ContreeConfig
    except ImportError:
        raise RuntimeError('the Nebius backend needs its extra: pip install -e ".[nebius]" in the doppel checkout '
                           '(or pip install "doppel-check[nebius]")') from None
    # the SDK's default HTTP timeout is 10 s per request: too short to upload a few-hundred-kB file
    # (a screenshot, a lockfile) on a slow home uplink, which fails the whole build with ApiTimeoutError
    timeout = float(os.getenv("DOPPEL_HTTP_TIMEOUT", "180"))
    return Contree(config=ContreeConfig(operation_poll_secs_min=0.05, operation_poll_secs_max=1.0,
                                        transport_timeout=timeout))


def run_twin(base_dir, head_dir, spec: TwinSpec, personas: list[dict], client=None, refine=None,
             watch: dict | None = None) -> dict:
    """Same contract as backends.local.run_twin: {"base": recordings, "head": recordings, "failed": [...],
    "personas": the personas actually replayed (after refine), "rehearsal": base recordings or None}."""
    twin = NebiusTwin(client or make_client(), spec)
    return asyncio.run(twin.run(base_dir, head_dir, personas, refine, watch))
