"""Running real installs reliably in Nebius sandboxes: one place for what every sandbox path needs.

    throttle      at most MAX_SANDBOXES sandboxes starting at once (a burst of 24+ made the API drop
                  connections: ContreeTransportError in BugsInPy FastAPI #6-#11)
    retrying()    a dropped connection or timeout is retried with backoff; if it keeps failing, an
                  InfraError says so, so it is never mistaken for a result about the app
    install()     an install that needs `git` (a dependency straight from a repository: Sanic's 2020
                  requirements) gets git first; one that fails to compile (no C compiler, no libpq,
                  libxml2, ...) is retried once with the build tools installed

Used by test mode (doppel.traffic.NebiusSession), the HTTP twin (doppel.backends.nebius) and the probe.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

MAX_SANDBOXES = 12
RETRIES = 3
BACKOFF = (5, 15, 30)   # seconds before the 2nd, 3rd, ... attempt

GIT = ("(command -v git >/dev/null 2>&1 || (apt-get update -qq >/dev/null && "
       "apt-get install -y -qq --no-install-recommends git >/dev/null))")
BUILD_TOOLS = ("apt-get update -qq >/dev/null && apt-get install -y -qq --no-install-recommends build-essential "
               "pkg-config git libpq-dev libxml2-dev libxslt1-dev libffi-dev libssl-dev libjpeg-dev zlib1g-dev "
               "default-libmysqlclient-dev >/dev/null")
NEEDS_GIT = re.compile(r"git\+|git://|\bgit@[\w.-]+:")
NEEDS_BUILD = re.compile("|".join([
    r"command '(gcc|cc|x86_64-linux-gnu-gcc)' failed", r"unable to execute '?(gcc|cc)", r"gcc: not found",
    r"No such file or directory: '(gcc|cc)'", r"Python\.h: No such file", r"pg_config executable not found",
    r"mysql_config not found", r"libxml/\w+\.h: No such file", r"Failed building wheel",
    r"error: subprocess-exited-with-error[\s\S]{0,4000}(gcc|compil|\.h)"]), re.I)
GIT_MISSING = re.compile(r"No such file or directory: 'git'|Cannot find command 'git'|git: not found", re.I)
REQUIREMENT_FILES = ("requirements*.txt", "requirements/*.txt", "*requirements*.txt", "pyproject.toml", "setup.py",
                     "setup.cfg", "Pipfile", "tox.ini")


class InfraError(RuntimeError):
    """The sandbox service kept failing (dropped connections, timeouts): nothing was learned about the app.
    Rerun; never count it as a result."""


def transient(e: BaseException) -> bool:
    name = type(e).__name__.lower()
    text = str(e).lower()[:500]
    return any(k in name for k in ("timeout", "timedout", "connect", "unavailable", "network", "transport", "reset",
                                   "readerror", "protocol", "temporar")) or \
        any(k in text for k in ("readerror", "connection reset", "502", "503", "504", "timed out"))


def throttle() -> asyncio.Semaphore:
    """One per session: how many sandbox runs may be in flight at once."""
    return asyncio.Semaphore(MAX_SANDBOXES)


async def retrying(make, what: str, log=lambda m: None, sem: asyncio.Semaphore | None = None):
    """await make() (a fresh coroutine per attempt), inside the throttle, retrying transient failures."""
    for attempt in range(RETRIES):
        try:
            if sem is None:
                return await make()
            async with sem:
                return await make()
        except Exception as e:  # noqa: BLE001 - classified below
            if not transient(e):
                raise
            if attempt == RETRIES - 1:
                raise InfraError(f"infrastructure error while {what}, {RETRIES} attempts: "
                                 f"{type(e).__name__}: {str(e)[:400]}. Nothing was learned about the app; "
                                 "rerun.") from e
            log(f"[sandbox] {type(e).__name__} while {what}: retrying in {BACKOFF[attempt]} s")
            await asyncio.sleep(BACKOFF[attempt])
    raise AssertionError("unreachable")


def needs_git(repo: str | Path, install: str = "") -> bool:
    """Does installing this repo fetch a dependency straight from a git repository?"""
    if NEEDS_GIT.search(install or ""):
        return True
    root = Path(repo)
    for pattern in REQUIREMENT_FILES:
        for f in root.glob(pattern):
            try:
                if f.is_file() and NEEDS_GIT.search(f.read_text("utf-8", errors="replace")):
                    return True
            except OSError:
                continue
    return False


def needs_build_tools(output: str) -> bool:
    """Did an install fail for want of a compiler, headers or git?"""
    return bool(NEEDS_BUILD.search(output or "") or GIT_MISSING.search(output or ""))


def install_shell(install: str, repo: str | Path) -> str:
    """The install command, with git provided first when the dependencies need it."""
    if not install:
        return ""
    return f"{GIT} && {install}" if needs_git(repo, install) else install


def with_build_tools(install: str) -> str:
    return f"{BUILD_TOOLS} && {install}"
