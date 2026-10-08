"""Seeding through the front door: starting data created by the app itself, over HTTP.

A seed script writes into the app's internals and has to get everything right that the app normally does:
the database location, password hashing, required fields, foreign keys. `seed_requests` instead replays
a list of ordinary HTTP steps (sign up alice, log in, create a post) once against the started app,
before the checkpoint. The app's own code does the rest.
"""
from __future__ import annotations

SEED_NAME = "doppel front-door seed"


def seed_persona(spec) -> dict:
    return {"name": SEED_NAME, "goal": "create the starting data through the app's own routes",
            "steps": list(spec.seed_steps)}


def failed_steps(recording: dict) -> list[dict]:
    return [s for s in recording.get("steps", []) if s.get("status", 0) == 0 or s.get("status", 0) >= 400]


def seed_problem(recording: dict) -> str:
    """Why the front-door seed failed, step by step, or "" when every step was accepted."""
    bad = failed_steps(recording)
    if not bad:
        return ""
    lines = ["seed_requests failed: the app refused or crashed on these steps (every step must succeed, "
             "because both worlds start from what they created):"]
    for s in bad:
        lines.append(f"  step {s['i'] + 1}: {s['request']} -> {s['status'] or 'no answer'}: {str(s.get('body'))[:400]}")
    lines.append("All steps: " + "; ".join(f"{s['i'] + 1}. {s['request']} {s['status']}"
                                          for s in recording.get("steps", [])))
    if recording.get("app_log"):
        lines.append("The app's own log (last lines):\n" + recording["app_log"][-2500:])
    return "\n".join(lines)


def summary(recording: dict) -> str:
    return "[seed_requests] " + "; ".join(f"{s['request']} -> {s['status']}" for s in recording.get("steps", []))
