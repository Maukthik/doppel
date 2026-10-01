"""The twin spec: how to seed, migrate and start the app under test.

Read from doppel.toml in the app's folder:

    [app]
    start = "python app.py"   # must listen on the port given in $PORT
    ready = "/health"         # polled until it answers (< 500)
    seed = "python seed.py"   # run ONCE on the base code; both worlds start from that data
    migrate = ""              # run in EACH world after its code is in place

    [diff]
    ignore = ["created_at"]   # JSON keys allowed to differ between worlds
    slow_ratio = 2.0          # flag a step this many times slower in head than in base
    slow_min_ms = 50          # ... and at least this many milliseconds slower
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TwinSpec:
    start: str
    ready: str = "/"
    seed: str = ""
    migrate: str = ""
    ignore: list[str] = field(default_factory=list)
    slow_ratio: float = 2.0
    slow_min_ms: float = 50.0

    @classmethod
    def load(cls, path: str | Path) -> TwinSpec:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        app, diff = data.get("app", {}), data.get("diff", {})
        if not app.get("start"):
            raise ValueError(f"{path}: [app] start is required (the command that runs the app)")
        return cls(start=app["start"], ready=app.get("ready", "/"), seed=app.get("seed", ""),
                   migrate=app.get("migrate", ""), ignore=list(diff.get("ignore", [])),
                   slow_ratio=float(diff.get("slow_ratio", 2.0)),
                   slow_min_ms=float(diff.get("slow_min_ms", 50.0)))
