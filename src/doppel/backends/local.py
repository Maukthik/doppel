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


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run(command: str, cwd: Path, what: str) -> None:
    if not command:
        return
    p = subprocess.run(_argv(command), cwd=cwd, capture_output=True, text=True, timeout=600)
    if p.returncode != 0:
        raise RuntimeError(f"{what} failed ({command}) in {cwd}:\n{p.stdout}{p.stderr}")


def _overlay(src: Path, dst: Path) -> None:
    """Copy src's files over dst; files only in dst (seeded data such as a .db) survive."""
    shutil.copytree(src, dst, ignore=SKIP, dirs_exist_ok=True)


def _serve_and_replay(world: Path, spec: TwinSpec, personas: list[dict]) -> list[dict]:
    port = _free_port()
    env = {**os.environ, "PORT": str(port), "PYTHONDONTWRITEBYTECODE": "1"}
    app = subprocess.Popen(_argv(spec.start), cwd=world, env=env,
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


def run_world(code_dir: Path, seeded: Path, spec: TwinSpec, personas: list[dict], work: Path) -> list[dict]:
    """Every persona gets its own fresh copy of the migrated world, so one persona's
    side effects (an extra order, a sold-out item) can't leak into another's results.
    In the sandbox backend each copy is a fork of one checkpoint and they run in parallel."""
    world = work / "world"
    shutil.copytree(seeded, world, ignore=SKIP)
    _overlay(code_dir, world)
    _run(spec.migrate, world, "migrate")
    recordings = []
    for i, persona in enumerate(personas):
        fork = work / f"persona{i}"
        shutil.copytree(world, fork)
        recordings += _serve_and_replay(fork, spec, [persona])
    return recordings


def run_twin(base_dir: str | Path, head_dir: str | Path, spec: TwinSpec, personas: list[dict]) -> dict:
    """Returns {"base": recordings, "head": recordings}."""
    base_dir, head_dir = Path(base_dir).resolve(), Path(head_dir).resolve()
    with tempfile.TemporaryDirectory(prefix="doppel-", ignore_cleanup_errors=True) as tmp:
        work = Path(tmp)
        seeded = work / "seeded"
        shutil.copytree(base_dir, seeded, ignore=SKIP)
        _run(spec.seed, seeded, "seed")
        out = {}
        for label, code in (("base", base_dir), ("head", head_dir)):
            (work / label).mkdir()
            out[label] = run_world(code, seeded, spec, personas, work / label)
        return out
