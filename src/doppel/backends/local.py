"""Local backend: the twin runs as processes on this machine.

For development and tests only (it runs the app's code on your computer). The Nebius
backend does the same thing inside sandbox checkpoints, which is the real product.

Flow, mirroring what the sandbox backend does with checkpoints:
  1. seed:   copy the base code, run `seed` once  -> the shared starting state
  2. worlds: for base and head, copy that seeded state and lay the world's code on top,
             run `migrate`, start the app on a free port, replay every persona, stop it
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

from doppel import runner
from doppel.spec import TwinSpec
from doppel.timeline import Timeline

SKIP = shutil.ignore_patterns(".git", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules")


def _argv(command: str) -> list[str]:
    """Split a spec command; 'python' means the interpreter running Doppel (same venv,
    and no shell, so stopping the app really stops it on Windows too)."""
    args = shlex.split(command, posix=os.name != "nt")
    if args and args[0] in ("python", "python3"):
        args[0] = sys.executable
    return args


SHELL_CHARS = ("&&", "||", "|", ";", "$", ">", "<", "`")


def _popen_args(command: str) -> dict:
    """Plain commands run without a shell (so stopping the app really stops it); commands that
    need one ("flask db upgrade && python seed.py") run through the system shell."""
    if not any(c in command for c in SHELL_CHARS):
        return {"args": _argv(command)}
    chained = any(op in command for op in ("&&", "||", ";"))
    # a single command (e.g. "gunicorn -b :$PORT app:app") replaces the shell, so terminate() reaches it
    return {"args": command if os.name == "nt" or chained else f"exec {command}", "shell": True}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run(command: str, cwd: Path, what: str, env: dict | None = None) -> str:
    if not command:
        return ""
    p = subprocess.run(**_popen_args(command), cwd=cwd, capture_output=True, text=True, timeout=600,
                       env={**os.environ, **(env or {})})
    if p.returncode != 0:
        raise RuntimeError(f"{what} failed ({command}) in {cwd}:\n{p.stdout}{p.stderr}")
    return p.stdout + p.stderr


def _overlay(src: Path, dst: Path) -> None:
    """Copy src's files over dst; files only in dst (seeded data such as a .db) survive."""
    shutil.copytree(src, dst, ignore=SKIP, dirs_exist_ok=True)


def _wait_ready(app: subprocess.Popen, base_url: str, path: str, timeout: float) -> bool:
    """Poll the ready route, but give up at once if the app has already exited (a crash on start)."""
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if app.poll() is not None:
            return False
        if runner.wait_ready(base_url, path, timeout=1):
            return True
    return False


def _trace_env(world: Path, watch: dict | None) -> dict:
    """Env that loads Doppel's line tracer (doppel.reach) in every Python process the app starts."""
    if not watch:
        return {}
    from doppel import reach
    hook = world / reach.TRACE_DIR
    hook.mkdir(exist_ok=True)
    (hook / "sitecustomize.py").write_text(reach.TRACER, encoding="utf-8")
    (hook / "targets.json").write_text(json.dumps(watch), encoding="utf-8")
    current = os.environ.get("PYTHONPATH", "")
    return {"PYTHONPATH": str(hook) + (os.pathsep + current if current else ""),
            reach.TARGETS_ENV: str(hook / "targets.json"), reach.OUT_ENV: str(hook / "reached.txt"),
            reach.ROOT_ENV: str(world)}


def _serve_and_replay(world: Path, spec: TwinSpec, personas: list[dict], watch: dict | None = None) -> list[dict]:
    port = _free_port()
    env = {**os.environ, **spec.env, "PORT": str(port), "PYTHONDONTWRITEBYTECODE": "1", **_trace_env(world, watch)}
    app = subprocess.Popen(**_popen_args(spec.start), cwd=world, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    base_url = f"http://127.0.0.1:{port}"
    try:
        if not _wait_ready(app, base_url, spec.ready, timeout=30):
            app.kill()
            raise RuntimeError(f"{world}: app never became ready at {base_url}{spec.ready}\n"
                               f"{app.communicate(timeout=5)[1]}")
        recordings = runner.replay(base_url, personas)
    finally:
        if app.poll() is None:
            app.terminate()
            try:
                app.wait(timeout=5)
            except subprocess.TimeoutExpired:
                app.kill()
    if watch:
        from doppel import reach
        hits = reach.parse_hits((world / reach.TRACE_DIR / "reached.txt").read_text("utf-8")
                                if (world / reach.TRACE_DIR / "reached.txt").exists() else "")
        for r in recordings:
            r["reached"] = hits
    return recordings


def build_world(code_dir: Path, seeded: Path, spec: TwinSpec, work: Path) -> Path:
    world = work / "world"
    shutil.copytree(seeded, world, ignore=SKIP)
    _overlay(code_dir, world)
    _run(spec.migrate, world, "migrate", spec.env)
    return world


def replay_world(world: Path, spec: TwinSpec, personas: list[dict], work: Path, tag: str = "",
                 watch: dict | None = None, tl: Timeline | None = None, lane: str = "") -> list:
    """Every persona gets its own fresh copy of the migrated world, so one persona's
    side effects (an extra order, a sold-out item) can't leak into another's results.
    In the sandbox backend each copy is a fork of one checkpoint and they run in parallel.
    Returns one recording, or the exception, per persona."""
    out = []
    for i, persona in enumerate(personas):
        fork = work / f"persona{tag}{i}"
        start = tl.now() if tl else 0.0
        shutil.copytree(world, fork)
        try:
            out += _serve_and_replay(fork, spec, [persona], watch)
            if tl:
                tl.add(lane, persona.get("name", f"persona {i + 1}"), start, kind="persona",
                       requests=len(out[-1].get("steps", [])))
        except Exception as e:  # noqa: BLE001 - one broken persona doesn't sink the run
            out.append(e)
            if tl:
                tl.add(lane, persona.get("name", f"persona {i + 1}"), start, ok=False, kind="persona")
        shutil.rmtree(fork, ignore_errors=True)
    return out


def run_world(code_dir: Path, seeded: Path, spec: TwinSpec, personas: list[dict], work: Path) -> list[dict]:
    world = build_world(code_dir, seeded, spec, work)
    return [r for r in replay_world(world, spec, personas, work) if isinstance(r, dict)]


def run_twin(base_dir: str | Path, head_dir: str | Path, spec: TwinSpec, personas: list[dict],
             refine=None, watch: dict | None = None) -> dict:
    """Returns {"base": recordings, "head": recordings, "failed": [...], "personas": [...],
    "rehearsal": base recordings before refine, or None}. refine(personas, recordings) -> personas."""
    from doppel.backends.nebius import pair
    base_dir, head_dir = Path(base_dir).resolve(), Path(head_dir).resolve()
    tl = Timeline("local")
    with tempfile.TemporaryDirectory(prefix="doppel-", ignore_cleanup_errors=True) as tmp:
        work = Path(tmp)
        seeded = work / "seeded"
        with tl.span("build", "copy base code", kind="step"):
            shutil.copytree(base_dir, seeded, ignore=SKIP)
        with tl.span("build", "seed data (once)", kind="checkpoint"):
            seed_log = _run(spec.seed, seeded, "seed", spec.env)
        if spec.seed:
            from doppel.dbscan import scan
            here = os.getcwd()
            try:
                os.chdir(seeded)
                seed_log += "\n".join(scan(roots=(".",)))
            finally:
                os.chdir(here)
        worlds = {}
        for label, code in (("base", base_dir), ("head", head_dir)):
            (work / label).mkdir()
            with tl.span("build", f"{label} world", kind="fork"):
                worlds[label] = build_world(code, seeded, spec, work / label)
        rehearsal = None
        if refine is not None:
            rehearsal = [r for r in replay_world(worlds["base"], spec, personas, work / "base", "r",
                                                 tl=tl, lane="rehearsal") if isinstance(r, dict)]
            personas = refine(personas, rehearsal)
        watch = watch or {}
        base = replay_world(worlds["base"], spec, personas, work / "base", watch=watch.get("base"),
                            tl=tl, lane="base")
        head = replay_world(worlds["head"], spec, personas, work / "head", watch=watch.get("head"),
                            tl=tl, lane="head")
        return {**pair(personas, base, head), "personas": personas, "rehearsal": rehearsal,
                "seed_log": seed_log[-4000:], "timeline": tl.to_dict()}
