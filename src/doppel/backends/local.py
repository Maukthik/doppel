"""Local backend: the twin runs as processes on this machine.

For development and tests only (it runs the app's code on your computer). The Nebius
backend does the same thing inside sandbox checkpoints, which is the real product.

Flow, mirroring what the sandbox backend does with checkpoints:
  1. seed:   copy the base code, run `seed` once  -> the shared starting state
  2. worlds: for base and head, copy that seeded state and lay the world's code on top,
             run `migrate`, start the app on a free port, replay every persona, stop it
"""
from __future__ import annotations

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


def _run(command: str, cwd: Path, what: str) -> None:
    if not command:
        return
    p = subprocess.run(**_popen_args(command), cwd=cwd, capture_output=True, text=True, timeout=600)
    if p.returncode != 0:
        raise RuntimeError(f"{what} failed ({command}) in {cwd}:\n{p.stdout}{p.stderr}")


def _overlay(src: Path, dst: Path) -> None:
    """Copy src's files over dst; files only in dst (seeded data such as a .db) survive."""
    shutil.copytree(src, dst, ignore=SKIP, dirs_exist_ok=True)


def _serve_and_replay(world: Path, spec: TwinSpec, personas: list[dict]) -> list[dict]:
    port = _free_port()
    env = {**os.environ, "PORT": str(port), "PYTHONDONTWRITEBYTECODE": "1"}
    app = subprocess.Popen(**_popen_args(spec.start), cwd=world, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    base_url = f"http://127.0.0.1:{port}"
    try:
        if not runner.wait_ready(base_url, spec.ready, timeout=30):
            app.kill()
            raise RuntimeError(f"{world}: app never became ready at {base_url}{spec.ready}\n"
                               f"{app.communicate(timeout=5)[1]}")
        return runner.replay(base_url, personas)
    finally:
        if app.poll() is None:
            app.terminate()
            try:
                app.wait(timeout=5)
            except subprocess.TimeoutExpired:
                app.kill()


def build_world(code_dir: Path, seeded: Path, spec: TwinSpec, work: Path) -> Path:
    world = work / "world"
    shutil.copytree(seeded, world, ignore=SKIP)
    _overlay(code_dir, world)
    _run(spec.migrate, world, "migrate")
    return world


def replay_world(world: Path, spec: TwinSpec, personas: list[dict], work: Path, tag: str = "") -> list:
    """Every persona gets its own fresh copy of the migrated world, so one persona's
    side effects (an extra order, a sold-out item) can't leak into another's results.
    In the sandbox backend each copy is a fork of one checkpoint and they run in parallel.
    Returns one recording, or the exception, per persona."""
    out = []
    for i, persona in enumerate(personas):
        fork = work / f"persona{tag}{i}"
        shutil.copytree(world, fork)
        try:
            out += _serve_and_replay(fork, spec, [persona])
        except Exception as e:  # noqa: BLE001 - one broken persona doesn't sink the run
            out.append(e)
        shutil.rmtree(fork, ignore_errors=True)
    return out


def run_world(code_dir: Path, seeded: Path, spec: TwinSpec, personas: list[dict], work: Path) -> list[dict]:
    world = build_world(code_dir, seeded, spec, work)
    return [r for r in replay_world(world, spec, personas, work) if isinstance(r, dict)]


def run_twin(base_dir: str | Path, head_dir: str | Path, spec: TwinSpec, personas: list[dict],
             refine=None) -> dict:
    """Returns {"base": recordings, "head": recordings, "failed": [...], "personas": [...],
    "rehearsal": base recordings before refine, or None}. refine(personas, recordings) -> personas."""
    from doppel.backends.nebius import pair
    base_dir, head_dir = Path(base_dir).resolve(), Path(head_dir).resolve()
    with tempfile.TemporaryDirectory(prefix="doppel-", ignore_cleanup_errors=True) as tmp:
        work = Path(tmp)
        seeded = work / "seeded"
        shutil.copytree(base_dir, seeded, ignore=SKIP)
        _run(spec.seed, seeded, "seed")
        worlds = {}
        for label, code in (("base", base_dir), ("head", head_dir)):
            (work / label).mkdir()
            worlds[label] = build_world(code, seeded, spec, work / label)
        rehearsal = None
        if refine is not None:
            rehearsal = [r for r in replay_world(worlds["base"], spec, personas, work / "base", "r")
                         if isinstance(r, dict)]
            personas = refine(personas, rehearsal)
        base = replay_world(worlds["base"], spec, personas, work / "base")
        head = replay_world(worlds["head"], spec, personas, work / "head")
        return {**pair(personas, base, head), "personas": personas, "rehearsal": rehearsal}
