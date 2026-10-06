"""The shell line the Nebius backend runs in each sandbox, executed here with a real shell."""
import shutil
import subprocess
import time

import pytest

from doppel.backends import nebius
from doppel.spec import TwinSpec


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell")
@pytest.mark.parametrize("start", ["echo serving on 0.0.0.0:$PORT",              # port spelled out in the command
                                   "env DEBUG=1 sh -c 'echo serving on 0.0.0.0:$PORT'",
                                   "sh -c 'echo serving on 0.0.0.0:$PORT'"])       # port read from the environment
def test_the_start_command_sees_the_port_however_it_reads_it(tmp_path, monkeypatch, start):
    # forward slashes: on Windows the test's `sh` (Git Bash) reads C:/Users/... but eats the backslashes
    # in C:\Users\..., which made all three cases fail there with "cd: No such file or directory"
    folder = tmp_path.as_posix()
    monkeypatch.setattr(nebius, "APP", folder)
    line = nebius.persona_command(TwinSpec(start=start, ready="/"))
    app_part = line.split("; python3 /doppel/runner.py")[0]   # start the app, skip the replay
    subprocess.run(["sh", "-c", app_part.replace("/tmp/app.log", f"{folder}/app.log")
                    .replace("/tmp/app.pid", f"{folder}/app.pid")], check=True, timeout=10)
    log = tmp_path / "app.log"
    # the app runs in the background, so its log may not even exist yet: on Windows, Git Bash takes a
    # moment to start the nested `sh`, and reading too early failed with FileNotFoundError
    def written() -> str:
        return log.read_text().strip() if log.exists() else ""

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not written():
        time.sleep(0.05)
    assert written() == f"serving on 0.0.0.0:{nebius.PORT}"
