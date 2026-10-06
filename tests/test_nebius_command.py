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
    monkeypatch.setattr(nebius, "APP", str(tmp_path))
    line = nebius.persona_command(TwinSpec(start=start, ready="/"))
    app_part = line.split("; python3 /doppel/runner.py")[0]   # start the app, skip the replay
    subprocess.run(["sh", "-c", app_part.replace("/tmp/app.log", str(tmp_path / "app.log"))
                    .replace("/tmp/app.pid", str(tmp_path / "app.pid"))], check=True, timeout=10)
    log = tmp_path / "app.log"
    deadline = time.monotonic() + 5                      # the app runs in the background: wait for its line
    while time.monotonic() < deadline and not log.read_text().strip():
        time.sleep(0.05)
    assert log.read_text().strip() == f"serving on 0.0.0.0:{nebius.PORT}"
