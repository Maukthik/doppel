"""Running real installs reliably in Nebius sandboxes: one place for what every sandbox path needs.

    throttle      at most MAX_SANDBOXES sandboxes starting at once (a burst of 24+ made the API drop
                  connections: ContreeTransportError in BugsInPy FastAPI #6-#11)
    retrying()    a dropped connection or timeout is retried with backoff; if it keeps failing, an
                  InfraError says so, so it is never mistaken for a result about the app
    install()     an install that needs `git` (a dependency straight from a repository: Sanic's 2020
                  requirements) gets git first; one that fails to compile (no C compiler, no libpq,
                  libxml2, ...) is retried once with the build tools installed

    upload()      the code goes up as ONE archive, unpacked in the sandbox: the SDK sends every file as its own
                  request, all at once, and thousands of them broke the connection pool (paperless-ngx,
                  label-studio: ReadError and pool timeouts on every attempt, 46 minutes lost on one run)

Used by test mode (doppel.traffic.NebiusSession), the HTTP twin (doppel.backends.nebius), the setup agent and the
probe.
"""
from __future__ import annotations

import asyncio
import io
import os
import re
import tarfile
import time
from pathlib import Path

MAX_SANDBOXES = 12
RETRIES = 3
BACKOFF = (5, 15, 30)   # seconds before the 2nd, 3rd, ... attempt

APT = "DEBIAN_FRONTEND=noninteractive apt-get"   # no terminal in a sandbox: never ask, never warn about it
GIT = (f"(command -v git >/dev/null 2>&1 || ({APT} update -qq >/dev/null && "
       f"{APT} install -y -qq --no-install-recommends git >/dev/null 2>&1))")
BUILD_TOOLS = (f"{APT} update -qq >/dev/null && {APT} install -y -qq --no-install-recommends build-essential "
               "pkg-config git libpq-dev libxml2-dev libxslt1-dev libffi-dev libssl-dev libjpeg-dev zlib1g-dev "
               "default-libmysqlclient-dev libldap2-dev libsasl2-dev >/dev/null 2>&1")
# a header a build could not find -> the Debian package that has it (a third try installs these)
HEADER_PACKAGES = {"lber.h": "libldap2-dev libsasl2-dev", "ldap.h": "libldap2-dev", "sasl/sasl.h": "libsasl2-dev",
                   "krb5": "libkrb5-dev", "gssapi": "libkrb5-dev", "libpq-fe.h": "libpq-dev",
                   "mysql.h": "default-libmysqlclient-dev", "ffi.h": "libffi-dev", "openssl/": "libssl-dev",
                   "jpeglib.h": "libjpeg-dev", "zlib.h": "zlib1g-dev", "libxml/": "libxml2-dev",
                   "libxslt/": "libxslt1-dev", "xmlsec": "libxmlsec1-dev", "lzma.h": "liblzma-dev",
                   "bzlib.h": "libbz2-dev", "sqlite3.h": "libsqlite3-dev", "curl/curl.h": "libcurl4-openssl-dev",
                   "magic.h": "libmagic-dev", "yaml.h": "libyaml-dev", "cairo": "libcairo2-dev",
                   "png.h": "libpng-dev", "freetype": "libfreetype-dev", "unixodbc": "unixodbc-dev",
                   "sql.h": "unixodbc-dev", "snappy": "libsnappy-dev", "geos": "libgeos-dev", "gdal": "libgdal-dev"}
NEEDS_GIT = re.compile(r"git\+|git://|\bgit@[\w.-]+:")
NEEDS_BUILD = re.compile("|".join([
    r"command '(gcc|cc|x86_64-linux-gnu-gcc)' failed", r"unable to execute '?(gcc|cc)", r"gcc: not found",
    r"No such file or directory: '(gcc|cc)'", r"Python\.h: No such file", r"pg_config executable not found",
    r"mysql_config not found", r"libxml/\w+\.h: No such file", r"Failed building wheel",
    r"pkg-config: not found", r"Command 'pkg-config [^']*' returned non-zero", r"pkg-config: command not found",
    r"Package \S+ was not found in the pkg-config search path",
    r"error: subprocess-exited-with-error[\s\S]{0,4000}(gcc|compil|\.h)"]), re.I)
GIT_MISSING = re.compile(r"No such file or directory: 'git'|Cannot find command 'git'|git: not found", re.I)
REQUIREMENT_FILES = ("requirements*.txt", "requirements/*.txt", "*requirements*.txt", "pyproject.toml", "setup.py",
                     "setup.cfg", "Pipfile", "tox.ini")


UPLOAD_PATH = "/tmp/.doppel-upload.tgz"
# One request carrying 5.3 MB went through, 8.7 MB failed every time; 2 MB parts three at a time still failed
# for some parts again and again. What fails is a request that takes too long while it shares the uplink, not
# its size: so small parts, one at a time, each with more tries. A fast uplink (a CI runner) can raise both.
UPLOAD_PART = int(float(os.getenv("DOPPEL_UPLOAD_PART_MB", "1")) * 1024 * 1024)
UPLOAD_PARALLEL = int(os.getenv("DOPPEL_UPLOAD_PARALLEL", "1"))
UPLOAD_ATTEMPTS, UPLOAD_BACKOFF = 5, (5, 15, 30, 60)
UNPACK_TIMEOUT = 900


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


async def retrying(make, what: str, log=lambda m: None, sem: asyncio.Semaphore | None = None,
                   attempts: int | None = None, backoff: tuple = ()):
    """await make() (a fresh coroutine per attempt), inside the throttle, retrying transient failures."""
    tries = attempts or RETRIES
    waits = backoff or BACKOFF
    for attempt in range(tries):
        try:
            if sem is None:
                return await make()
            async with sem:
                return await make()
        except Exception as e:  # noqa: BLE001 - classified below
            if not transient(e):
                raise
            if attempt == tries - 1:
                raise InfraError(f"infrastructure error while {what}, {tries} attempts: "
                                 f"{type(e).__name__}: {str(e)[:400]}. Nothing was learned about the app; "
                                 "rerun.") from e
            wait = waits[min(attempt, len(waits) - 1)]
            log(f"[sandbox] {type(e).__name__} while {what}: retrying in {wait} s")
            await asyncio.sleep(wait)
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


# a shared library or program the tests could not find at run time -> the Debian package (GitHub's runners have
# many of these preinstalled; a slim image does not). paperless-ngx: "failed to find libmagic".
RUNTIME_PACKAGES = [
    (r"failed to find libmagic|libmagic\.so", "libmagic1"),
    (r"libzbar\.so|Unable to find zbar shared library", "libzbar0"),
    (r"libGL\.so\.1", "libgl1"),
    (r"lib(glib|gthread|gobject)-2\.0\.so", "libglib2.0-0"),
    (r"libcairo[\w.-]*\.so|no library called \"cairo", "libcairo2"),
    (r"libpango[\w.-]*\.so", "libpango-1.0-0 libpangoft2-1.0-0"),
    (r"libgomp\.so", "libgomp1"),
    (r"libsndfile[\w.-]*\.so|sndfile library not found", "libsndfile1"),
    (r"libpq\.so", "libpq5"),
    (r"libxmlsec1[\w.-]*\.so", "libxmlsec1-openssl"),
    (r"TesseractNotFoundError|tesseract is not installed", "tesseract-ocr"),
    (r"pdftotext: not found|Unable to get page count. Is poppler installed", "poppler-utils"),
    (r"ghostscript.*not found|\bgs: not found", "ghostscript"),
]


def runtime_packages(output: str) -> list[str]:
    """The Debian packages for libraries or programs a test run could not find."""
    out: list[str] = []
    for pattern, packages in RUNTIME_PACKAGES:
        if re.search(pattern, output or ""):
            out += [p for p in packages.split() if p not in out]
    return out


def header_packages(output: str) -> list[str]:
    """The Debian packages for the headers a failed build could not find (mealie: python-ldap wanted lber.h)."""
    out: list[str] = []
    for header in re.findall(r"fatal error: ([\w./+-]+\.h): No such file", output or ""):
        for key, packages in HEADER_PACKAGES.items():
            if key in header:
                out += [p for p in packages.split() if p not in out]
    return out


def with_build_tools(install: str) -> str:
    return f"{BUILD_TOOLS} && {install}"


def archive(files: dict[str, Path]) -> bytes:
    """{"path/in/sandbox": local file} as one gzip'd tar. Every entry is a regular file with the local file's
    content (a symlink is stored as what it points to), owned by root, keeping the local permission bits."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=6) as tar:
        for dest, src in sorted(files.items()):
            src = Path(src)
            st = src.stat()
            info = tarfile.TarInfo(str(dest).lstrip("/"))
            info.size, info.mtime, info.mode = st.st_size, int(st.st_mtime), (st.st_mode & 0o777) or 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            with src.open("rb") as f:
                tar.addfile(info, f)
    return buf.getvalue()


async def upload(img, files: dict[str, Path], what: str, log=lambda m: None):
    """Lay files into an image: one archive, sent in parts of at most UPLOAD_PART bytes, joined and unpacked in
    the sandbox; returns the new image. One large request failed every time, one request per file broke the
    connection pool; the parts go up one at a time by default (UPLOAD_PARALLEL), each retried on its own, and
    the log says how fast it went. A failed unpack is an InfraError, never a result about the app."""
    if not files:
        return img
    data = archive(files)
    parts = [data[i:i + UPLOAD_PART] for i in range(0, len(data), UPLOAD_PART)] or [b""]
    names = [f"{UPLOAD_PATH}.{n:04d}" for n in range(len(parts))]
    log(f"[sandbox] {what}: {len(files)} files as one archive ({len(data) / 1e6:.1f} MB"
        + (f", {len(parts)} parts)" if len(parts) > 1 else ")"))
    send = getattr(getattr(getattr(img, "_client", None), "files", None), "_upload_bytes_file", None)
    if send is not None and len(parts) > 1:   # content-addressed: the run below finds the parts already there
        gate = asyncio.Semaphore(max(1, UPLOAD_PARALLEL))
        started = time.monotonic()

        async def one(n: int, part: bytes):
            async with gate:
                await retrying(lambda: send(part), f"{what} (part {n + 1} of {len(parts)})", log,
                               attempts=UPLOAD_ATTEMPTS, backoff=UPLOAD_BACKOFF)
        await asyncio.gather(*(one(n, part) for n, part in enumerate(parts)))
        took = max(time.monotonic() - started, 0.001)
        log(f"[sandbox] {what}: {len(data) / 1e6:.1f} MB sent in {took:.0f} s ({len(data) / 1e6 / took:.2f} MB/s)")
    shell = (f"cat {UPLOAD_PATH}.* > {UPLOAD_PATH} && tar xzf {UPLOAD_PATH} -C / && "
             f"rm -f {UPLOAD_PATH} {UPLOAD_PATH}.*")
    out = await retrying(lambda: img.run(shell=shell, files=dict(zip(names, parts, strict=True)), disposable=False,
                                         timeout=UNPACK_TIMEOUT), what, log)
    if getattr(out, "exit_code", 0) not in (0, None):
        raise InfraError(f"{what}: unpacking the code in the sandbox failed (exit {out.exit_code}): "
                         f"{((out.stderr or '') + (out.stdout or ''))[-400:]}. Nothing was learned about the app.")
    return out
