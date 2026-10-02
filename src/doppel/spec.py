"""The twin spec: how to seed, migrate and start the app under test.

Read from doppel.toml in the app's folder:

    [app]
    image = "python:3.12-slim"  # sandbox base image (nebius backend)
    install = ""              # install dependencies, e.g. "pip install -r requirements.txt" (nebius backend;
                              # the local backend uses your current environment)
    start = "python app.py"   # must listen on the port given in $PORT
    ready = "/health"         # polled until it answers (< 500)
    seed = "python seed.py"   # run ONCE on the base code; both worlds start from that data
    migrate = ""              # run in EACH world after its code is in place

    [personas]
    guide = ""                # optional notes for Nemotron: how to log in, which seeded accounts exist

    [personas.login]          # optional: a login step Doppel adds to personas that never log in
    method = "POST"
    path = "/api/tokens"
    auth = ["alice", "secret"]            # HTTP Basic (or json = {...} for a JSON body)
    save = { token = "access_token" }
    header = { Authorization = "Bearer {token}" }

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
    image: str = "python:3.12-slim"
    install: str = ""
    seed: str = ""
    migrate: str = ""
    ignore: list[str] = field(default_factory=list)
    slow_ratio: float = 2.0
    slow_min_ms: float = 50.0
    persona_guide: str = ""
    login: dict | None = None

    @classmethod
    def load(cls, path: str | Path) -> TwinSpec:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        app, diff, personas = data.get("app", {}), data.get("diff", {}), data.get("personas", {})
        if not app.get("start"):
            raise ValueError(f"{path}: [app] start is required (the command that runs the app)")
        return cls(start=app["start"], ready=app.get("ready", "/"), image=app.get("image", "python:3.12-slim"),
                   install=app.get("install", ""), seed=app.get("seed", ""),
                   migrate=app.get("migrate", ""), ignore=list(diff.get("ignore", [])),
                   slow_ratio=float(diff.get("slow_ratio", 2.0)),
                   slow_min_ms=float(diff.get("slow_min_ms", 50.0)),
                   persona_guide=str(personas.get("guide", "")).strip(),
                   login=personas.get("login") if isinstance(personas.get("login"), dict) else None)
