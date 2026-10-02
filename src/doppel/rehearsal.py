"""Rehearsal: check the personas against the running base world before the real comparison.

A persona that never gets past the login, or calls routes that don't exist, sees the same 401 or
404 in both worlds, so it can never find a regression. Diffing it would report "no changes" and
look like a clean PR. Rehearsal catches that:

  1. replay every persona once in the base world (in the sandbox, in parallel, about a second)
  2. coverage(): how many of its requests the app actually served
  3. repair(): personas that mostly hit 401/403/404/405 go back to Nemotron together with what
     the app answered, and come back fixed (deliberately bad input from careless users is kept)

The report always states coverage, so "no regressions" can be told apart from "couldn't test".
"""
from __future__ import annotations

import json
import os

from doppel.personas import cost_usd, nemotron_chat, parse_json, read_source, validate
from doppel.runner import placeholders

BLOCKED = {401, 403, 404, 405}

REPAIR_PROMPT = """These synthetic users ("personas") were replayed against a running copy of the web API
whose source is below. Their requests mostly failed with 401/403/404/405, which means the persona
is wrong (wrong login method, a token that is never saved or sent, a route that doesn't exist,
a wrong URL prefix, a path parameter the route doesn't take), not the app. Read the app's answers
and the route definitions in the code; do not repeat a path that answered 404. Fix each one so
it does what its goal says, using only routes and the
login flow that exist in the code. Keep each persona's name and goal. Keep deliberate bad input
(careless users sending invalid data on purpose), but it must reach the route it is testing.

Persona format reminder: steps may carry "auth": ["username", "password"] for HTTP Basic auth,
"headers": {{...}} (e.g. {{"Authorization": "Bearer {{token}}"}}), "json": {{...}} for a body, and
"save": {{"var": "dotted.path"}} to keep a value from the response. Persona-level "headers" apply
to every step.

Return ONLY JSON: {{"personas": [persona, ...]}} with the {n} fixed personas.
{guide}
Personas and what the app answered (status, start of the body) for each step:
{transcripts}

App source:
{source}
"""


def _ok(status: int) -> bool:
    return 200 <= status < 400


def coverage(recordings: list[dict]) -> dict:
    """How much of the app the personas actually reached in one world."""
    steps = [s for r in recordings for s in r["steps"]]
    stuck = [r["persona"] for r in recordings if is_stuck(r)]
    return {"requests": len(steps), "served": sum(_ok(s["status"]) for s in steps),
            "blocked": sum(s["status"] in BLOCKED for s in steps), "stuck_personas": stuck,
            "personas": len(recordings)}


def is_stuck(recording: dict) -> bool:
    """Most of its requests were refused or not found: the persona can't be testing anything."""
    steps = recording["steps"]
    blocked = sum(s["status"] in BLOCKED for s in steps)
    return bool(steps) and blocked * 2 > len(steps) and not any(_ok(s["status"]) for s in steps[1:])


def unfilled(persona: dict) -> list[str]:
    """Placeholders the persona uses but never presets or saves (auto-capture may still fill some)."""
    used = placeholders({k: persona.get(k) for k in ("headers", "steps")})
    known = set(persona.get("vars") or {}) | {v for s in persona.get("steps") or [] for v in (s.get("save") or {})}
    return sorted(used - known)


def _transcript(persona: dict, recording: dict) -> str:
    lines = [json.dumps({k: persona[k] for k in ("name", "goal", "headers", "vars", "steps") if k in persona},
                        ensure_ascii=False)]
    if unfilled(persona):
        lines.append(f"  note: uses {', '.join('{' + v + '}' for v in unfilled(persona))} but no step saves it")
    lines.append("  what the app answered:")
    for s in recording["steps"]:
        body = json.dumps(s["body"], ensure_ascii=False, default=str)[:160]
        lines.append(f"  step {s['i']}: {s['request']} -> {s['status']} {body}")
    return "\n".join(lines)


def with_login(persona: dict, login: dict) -> dict:
    """The persona with the app owner's login step in front and its token header on every step."""
    step = {k: login[k] for k in ("method", "path", "auth", "json", "save") if k in login}
    step.setdefault("method", "POST")
    return {**persona, "steps": [step, *persona["steps"]],
            "headers": {**(login.get("header") or {}), **(persona.get("headers") or {})}}


def never_logged_in(persona: dict, recording: dict, login: dict) -> bool:
    """Refused with 401 and never even tried the login route."""
    tried = any(s.get("auth") or s.get("path", "").split("?")[0] == login.get("path") for s in persona["steps"])
    refused = sum(s["status"] == 401 for s in recording["steps"])
    return not tried and refused * 2 > len(recording["steps"])


def repair(personas: list[dict], recordings: list[dict], app_dir, guide: str = "", model: str | None = None,
           chat=nemotron_chat, login: dict | None = None) -> dict:
    """Returns {"personas": the full list with stuck ones replaced, "repaired": names, "usd": ...}.
    Personas that simply never logged in get the owner's login step (no model call); the rest of
    the stuck ones go to Nemotron with the app's answers."""
    by_name = {r["persona"]: r for r in recordings}
    stuck = [p for p in personas if p["name"] in by_name and is_stuck(by_name[p["name"]])]
    if login and login.get("path"):
        quick = {p["name"] for p in stuck if never_logged_in(p, by_name[p["name"]], login)}
        personas = [with_login(p, login) if p["name"] in quick else p for p in personas]
        stuck = [p for p in stuck if p["name"] not in quick]
    else:
        quick = set()
    if not stuck:
        return {"personas": personas, "repaired": sorted(quick), "usd": 0.0}
    # repair is a small, hard call (read the app's answers, find the mistake): use the strong model
    model = model or os.getenv("REPAIR_MODEL", "nvidia/Nemotron-3-Ultra-550b-a55b")
    prompt = REPAIR_PROMPT.format(n=len(stuck), guide=f"\nNotes from the app's owner: {guide}\n" if guide else "",
                                  transcripts="\n\n".join(_transcript(p, by_name[p["name"]]) for p in stuck),
                                  source=read_source(app_dir))
    text, tin, tout = chat(model, prompt, temperature=0.2)
    fixed = {p["name"]: p for p in validate(parse_json(text))}
    out = [{**fixed[p["name"]], **({"probe": p["probe"]} if p.get("probe") else {})}
           if p in stuck and p["name"] in fixed else p for p in personas]
    return {"personas": out, "repaired": sorted(quick | (set(fixed) & {p["name"] for p in stuck})),
            "usd": cost_usd(model, tin, tout)}
