"""Turning a failed command's output into an error a model can act on."""
from __future__ import annotations

import re

SEED_FRAME = re.compile(r'^\s*File "[^"]*doppel_seed\.py", line \d+')
NOISE = re.compile(r"^\s*(Applying \S+\.\.\. OK|Operations to perform:|Running migrations:|Apply all migrations:)")


def failure_text(stdout: str, stderr: str, limit: int = 2000) -> str:
    """The end of each stream, without migration chatter, plus the seed's own traceback frames.

    Django tracebacks are long: keeping only the last 2000 characters of stderr cut off the frame
    in doppel_seed.py, so the model saw "NOT NULL constraint failed" but not which of its lines
    failed (linkding, 8 Oct). Those frames are pulled out of the whole output first."""
    stdout, stderr = stdout or "", stderr or ""
    lines = stderr.splitlines()
    own = []
    for i, line in enumerate(lines):
        if SEED_FRAME.match(line):
            own += [line] + [ln for ln in lines[i + 1:i + 3] if not ln.lstrip().startswith(("File ", "~", "^"))][:1]
    kept_out = "\n".join(ln for ln in stdout.splitlines() if not NOISE.match(ln))
    tail = stderr[-limit:]
    frames = ""
    if own and not all(f in tail for f in own):
        frames = "Your doppel_seed.py, where it failed:\n" + "\n".join(own[-6:]) + "\n...\n"
    return f"{kept_out[-limit:]}\n{frames}{tail}".lstrip("\n")


def head_and_tail(text: str, limit: int = 2500) -> str:
    """A stop reason short enough to show: its first line (what happened) and the END of the rest (where the
    real error is). Keeping the start cut off the final exception line: wger's report ended mid-traceback at
    'File "<froze', the ModuleNotFoundError or ImproperlyConfigured below it lost."""
    text = text or ""
    if len(text) <= limit:
        return text
    first, _, rest = text.partition("\n")
    room = max(limit - len(first) - 6, 200)
    return f"{first[:limit]}\n...\n{rest[-room:]}"
