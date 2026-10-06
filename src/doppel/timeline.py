"""What happened inside the twin, in order and with timings, for `doppel view` and the replay site.

Each event is one thing the twin did: building the seeded checkpoint, forking a world, or one
persona's throwaway run. Times are seconds from the start of the run, so a page can draw them on
one axis without knowing the clock of the machine that recorded them.

    {"lane": "build" | "base" | "head" | "rehearsal", "label": "install (base)",
     "start": 0.0, "end": 12.4, "ok": true, "kind": "checkpoint" | "fork" | "persona" | "step"}
"""
from __future__ import annotations

import time
from contextlib import contextmanager


class Timeline:
    def __init__(self, backend: str):
        self.backend = backend
        self.t0 = time.perf_counter()
        self.events: list[dict] = []

    def now(self) -> float:
        return round(time.perf_counter() - self.t0, 3)

    def add(self, lane: str, label: str, start: float, end: float | None = None, ok: bool = True,
            kind: str = "step", **extra) -> dict:
        event = {"lane": lane, "label": label, "start": start, "end": self.now() if end is None else end,
                 "ok": ok, "kind": kind, **extra}
        self.events.append(event)
        return event

    @contextmanager
    def span(self, lane: str, label: str, kind: str = "step", **extra):
        """Times the block; an exception marks the event failed and still propagates."""
        start = self.now()
        try:
            yield
        except BaseException:
            self.add(lane, label, start, ok=False, kind=kind, **extra)
            raise
        self.add(lane, label, start, kind=kind, **extra)

    def to_dict(self) -> dict:
        events = sorted(self.events, key=lambda e: (e["start"], e["end"]))
        return {"backend": self.backend, "total": max((e["end"] for e in events), default=0.0),
                "events": events}
