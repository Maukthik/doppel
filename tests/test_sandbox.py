"""Sandbox reliability: throttle, retries, infrastructure errors, git and build tools on demand."""
import asyncio

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
