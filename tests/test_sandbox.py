"""Sandbox reliability: throttle, retries, infrastructure errors, git and build tools on demand."""
import asyncio
from types import SimpleNamespace

import pytest

from doppel import sandbox, traffic
from tests.test_traffic import FLASK_APP, flask_repo


class ContreeTransportError(Exception):      # the name the Nebius SDK uses for a dropped connection
    pass


class FakeImage:
    def __init__(self, world):
        self.world, self.exit_code, self.stdout, self.stderr = world, 0, "", ""

    async def apply_files(self, files):
        return self

    async def run(self, shell, **kw):
        return await self.world.run(self, shell, kw)


class FakeWorld:
    """Records every shell; `script` decides each run's outcome."""
    def __init__(self, script=None):
        self.shells, self.script, self.live, self.peak = [], script or (lambda shell, n: (0, "")), 0, 0

    async def run(self, image, shell, kw):
        self.shells.append(shell)
        self.live += 1
        self.peak = max(self.peak, self.live)
        try:
            await asyncio.sleep(0.01)
            code, out = self.script(shell, len(self.shells))
            if isinstance(code, Exception):
                raise code
            result = FakeImage(self)
            result.exit_code, result.stdout = code, out
            return result
        finally:
            self.live -= 1

    def client(self):
        world = self

        class Images:
            async def use(self, name):
                return FakeImage(world)

        class Client:
            images = Images()
        return Client()


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(sandbox, "BACKOFF", (0, 0, 0))
    monkeypatch.setattr(sandbox, "UPLOAD_BACKOFF", (0,))


def session(monkeypatch, tmp_path, world, install="pip install -r requirements.txt", head_app=FLASK_APP):
    from doppel.backends import nebius
    monkeypatch.setattr(nebius, "make_client", world.client)
    base, head = flask_repo(tmp_path / "b"), flask_repo(tmp_path / "h", head_app)
    return base, head, lambda: traffic.NebiusSession(base, head, install=install)


def test_no_more_than_max_sandboxes_start_at_once(monkeypatch, tmp_path):
    world = FakeWorld()
    _, _, make = session(monkeypatch, tmp_path, world)
    s = make()
    s.run([("base", "python -m pytest")] * 30)
    s.close()
    assert world.peak <= sandbox.MAX_SANDBOXES


def test_a_dropped_connection_is_retried(monkeypatch, tmp_path, fast):
    jobs = []

    def flaky(shell, n):
        if "pytest" in shell:
            jobs.append(n)
            if len(jobs) == 1:                   # the first attempt at the job loses its connection
                return ContreeTransportError("ReadError('')"), ""
        return 0, ""
    world = FakeWorld(flaky)
    _, _, make = session(monkeypatch, tmp_path, world)
    s = make()
    s.run([("base", "python -m pytest")])
    s.close()
    assert sum("pytest" in sh for sh in world.shells) == 2      # failed once, then went through


def test_a_service_that_keeps_failing_is_an_infrastructure_error_not_a_result(monkeypatch, tmp_path, fast):
    world = FakeWorld(lambda shell, n: (ContreeTransportError("ReadError('')"), ""))
    _, _, make = session(monkeypatch, tmp_path, world)
    with pytest.raises(sandbox.InfraError, match="infrastructure error while starting the sandbox|uploading|install"):
        make()


def test_an_app_error_is_not_retried(fast):
    calls = []

    async def boom():
        calls.append(1)
        raise ValueError("a real bug in the code")
    with pytest.raises(ValueError):
        asyncio.run(sandbox.retrying(boom, "x"))
    assert calls == [1]


def test_git_is_installed_first_when_a_dependency_comes_from_a_repository(monkeypatch, tmp_path):
    world = FakeWorld()
    base, _, make = session(monkeypatch, tmp_path, world)
    (base / "requirements.txt").write_text("flask\ngit+https://github.com/x/y.git@abc#egg=y\n", encoding="utf-8")
    make().close()
    install = next(sh for sh in world.shells if "requirements.txt" in sh)
    assert install.index("apt-get install -y -qq --no-install-recommends git") < install.index("pip install -r")


def test_no_git_when_nothing_needs_it(monkeypatch, tmp_path):
    world = FakeWorld()
    _, _, make = session(monkeypatch, tmp_path, world)
    make().close()
    assert not any("apt-get" in sh for sh in world.shells)


def test_an_install_that_cannot_compile_is_retried_with_build_tools(monkeypatch, tmp_path):
    def needs_gcc(shell, n):
        if "pip install" in shell and "build-essential" not in shell:
            return 1, "error: command 'gcc' failed: No such file or directory"
        return 0, ""
    world = FakeWorld(needs_gcc)
    _, _, make = session(monkeypatch, tmp_path, world)
    make().close()
    installs = [sh for sh in world.shells if "pip install" in sh]
    assert len(installs) == 2 and "build-essential" in installs[1] and "libpq-dev" in installs[1]


def test_an_install_that_fails_for_another_reason_stops_with_its_output(monkeypatch, tmp_path):
    world = FakeWorld(lambda shell, n: (1, "ERROR: No matching distribution found for flask==99")
                      if "pip install" in shell else (0, ""))
    _, _, make = session(monkeypatch, tmp_path, world)
    with pytest.raises(traffic.NotComparable, match="flask==99"):
        make()
    assert sum("pip install" in sh for sh in world.shells) == 1     # no pointless build-tools retry


def test_a_pr_whose_dependencies_do_not_install_is_not_compared(monkeypatch, tmp_path):
    installs = []

    def head_breaks(shell, n):
        if "pip install" in shell:
            installs.append(n)
            if len(installs) == 2:               # the base installs; the PR's dependencies do not
                return 1, "ERROR: No matching distribution found for nothere"
        return 0, ""
    world = FakeWorld(head_breaks)
    base, head, make = session(monkeypatch, tmp_path, world)
    (head / "requirements.txt").write_text("flask\nnothere==1\n", encoding="utf-8")
    with pytest.raises(traffic.NotComparable, match="installing the PR's dependencies failed"):
        make()


def test_the_http_twin_installs_the_same_way():
    from doppel.backends.nebius import NebiusTwin
    from doppel.spec import TwinSpec
    twin = NebiusTwin.__new__(NebiusTwin)
    twin.spec, twin.log, seen = TwinSpec(start="x", install="pip install -r requirements.txt"), (lambda m: None), []

    async def step(image, shell, what, shown=None, kind="checkpoint"):
        seen.append(shell)
        if "build-essential" not in shell:
            raise RuntimeError("install (base) failed:\nPython.h: No such file or directory")
        return image
    twin._step = step
    asyncio.run(twin._install("img", ".", "install (base)"))
    assert len(seen) == 2 and "build-essential" in seen[1]


# --- the code goes up as one archive ------------------------------------------------------------

def test_the_archive_holds_every_file_with_its_content_and_mode(tmp_path):
    import io
    import os
    import tarfile
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "app.py").write_bytes(b"print('hi')\n")   # bytes: write_text gives \r\n on Windows
    (tmp_path / "run.sh").write_bytes(b"#!/bin/sh\n")
    if os.name != "nt":
        os.chmod(tmp_path / "run.sh", 0o755)
    files = {"app/pkg/app.py": tmp_path / "pkg" / "app.py", "app/run.sh": tmp_path / "run.sh"}
    with tarfile.open(fileobj=io.BytesIO(sandbox.archive(files)), mode="r:gz") as tar:
        members = {m.name: m for m in tar.getmembers()}
        assert set(members) == {"app/pkg/app.py", "app/run.sh"}
        assert tar.extractfile("app/pkg/app.py").read() == b"print('hi')\n"
        assert all(m.isfile() and m.uid == 0 for m in members.values())
        if os.name != "nt":
            assert members["app/run.sh"].mode & 0o111


def test_upload_is_one_request_and_one_unpack_whatever_the_file_count(tmp_path, fast):
    for i in range(300):
        (tmp_path / f"m{i}.py").write_text(f"x = {i}\n")
    calls = []

    class Image:
        exit_code, stdout, stderr = 0, "", ""

        async def run(self, shell, files=None, **kw):
            calls.append((shell, sorted(files or {})))
            return Image()
    files = {f"app/m{i}.py": tmp_path / f"m{i}.py" for i in range(300)}
    asyncio.run(sandbox.upload(Image(), files, "uploading the code"))
    assert len(calls) == 1 and calls[0][1] == [f"{sandbox.UPLOAD_PATH}.0000"]     # small: one part, one run
    assert calls[0][0].startswith(f"cat {sandbox.UPLOAD_PATH}.* > {sandbox.UPLOAD_PATH} && tar xzf")


def test_a_big_archive_goes_up_in_parts_one_at_a_time_and_joins_back(tmp_path, fast, monkeypatch):
    # 8.7 MB and up failed as one request every time; parts of 2 MB, three at once, each retried alone
    import io
    import os
    import tarfile
    monkeypatch.setattr(sandbox, "UPLOAD_PART", 64 * 1024)
    for i in range(6):
        (tmp_path / f"blob{i}.bin").write_bytes(os.urandom(50_000))   # random: gzip can't shrink it
    files = {f"app/blob{i}.bin": tmp_path / f"blob{i}.bin" for i in range(6)}
    sent, running, peak, failed_once = [], [0], [0], set()

    class Files:
        async def _upload_bytes_file(self, data):
            running[0] += 1
            peak[0] = max(peak[0], running[0])
            await asyncio.sleep(0.01)
            running[0] -= 1
            if len(data) and data[:8] not in failed_once:     # every part drops once, then goes through
                failed_once.add(data[:8])
                raise ContreeTransportError("ReadError")
            sent.append(data)

    runs = []

    class Image:
        _client = SimpleNamespace(files=Files())
        exit_code, stdout, stderr = 0, "", ""

        async def run(self, shell, files=None, **kw):
            runs.append(files)
            return Image()
    asyncio.run(sandbox.upload(Image(), files, "uploading the code"))
    parts = runs[0]
    assert len(parts) > 3 and len(sent) == len(parts) and peak[0] == 1     # one at a time by default
    whole = b"".join(parts[k] for k in sorted(parts))
    with tarfile.open(fileobj=io.BytesIO(whole), mode="r:gz") as tar:
        assert sorted(tar.getnames()) == sorted(files)


def test_a_dropped_upload_is_retried_and_a_failed_unpack_is_an_infra_error(tmp_path, fast):
    (tmp_path / "a.py").write_text("x = 1\n")
    tries = []

    class Image:
        def __init__(self, code=0):
            self.exit_code, self.stdout, self.stderr = code, "", "tar: short read"

        async def run(self, shell, files=None, **kw):
            tries.append(shell)
            if len(tries) == 1:
                raise ContreeTransportError("ReadError")
            return Image(code=2)
    with pytest.raises(sandbox.InfraError, match="unpacking the code"):
        asyncio.run(sandbox.upload(Image(), {"app/a.py": tmp_path / "a.py"}, "uploading the code"))
    assert len(tries) == 2


def test_nothing_to_upload_leaves_the_image_as_it_is():
    marker = object()
    assert asyncio.run(sandbox.upload(marker, {}, "uploading the PR's code")) is marker


def test_a_missing_pkg_config_calls_for_the_build_tools():
    # healthchecks, 11 Oct: mysqlclient's build looked for pkg-config; the build tools have it, the trigger missed it
    out = ("error: subprocess-exited-with-error\n  Getting requirements to build wheel did not run successfully.\n"
           "      /bin/sh: 1: pkg-config: not found\n      Trying pkg-config --exists mysqlclient\n"
           "      Command 'pkg-config --exists mysqlclient' returned non-zero exit status 127.\n")
    assert sandbox.needs_build_tools(out)
    assert "pkg-config" in sandbox.BUILD_TOOLS and "default-libmysqlclient-dev" in sandbox.BUILD_TOOLS



def test_a_part_gets_more_tries_than_other_calls_and_the_log_says_how_fast(tmp_path, fast, monkeypatch):
    import os
    monkeypatch.setattr(sandbox, "UPLOAD_PART", 32 * 1024)
    (tmp_path / "b.bin").write_bytes(os.urandom(70_000))
    drops = {"left": sandbox.RETRIES}          # more drops than an ordinary call survives

    class Files:
        async def _upload_bytes_file(self, data):
            if drops["left"]:
                drops["left"] -= 1
                raise ContreeTransportError("ReadError")

    class Image:
        _client = SimpleNamespace(files=Files())
        exit_code, stdout, stderr = 0, "", ""

        async def run(self, shell, files=None, **kw):
            return Image()
    lines = []
    asyncio.run(sandbox.upload(Image(), {"app/b.bin": tmp_path / "b.bin"}, "uploading the code", lines.append))
    assert any("MB sent in" in m and "MB/s" in m for m in lines)
