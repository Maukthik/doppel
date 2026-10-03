"""Nemotron invents synthetic users (personas) for an app, in the runner's JSON format.

It reads the app's source (Nemotron 3.5 Lightning has a 1M-token context) and, if given, the
pull request's diff, so about half the personas walk through the code the PR changed.
Personas are generated once and saved, so base and head replay exactly the same users.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import sys
from pathlib import Path

from doppel.backends.nebius import SKIP_PARTS

SOURCE_SUFFIXES = {".py", ".js", ".ts", ".go", ".rb", ".java", ".kt", ".php", ".rs", ".toml", ".yaml",
                   ".yml", ".json", ".sql", ".md"}
MAX_SOURCE_CHARS = 400_000
METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
PRICES = {"lightning": (0.06, 0.24), "nano": (0.06, 0.24), "super": (0.30, 0.90), "ultra": (1.00, 3.00)}

PROMPT = """You write synthetic users ("personas") that exercise a web API, so that two versions of
the app can be compared by replaying the same personas against both.

Return ONLY JSON: {{"personas": [persona, ...]}} with exactly {n} personas. Each persona:
  {{"name": "short distinct name", "goal": "one sentence",
    "vars": {{"name": value, ...}},
    "headers": {{"Header-Name": "value"}} (optional, sent with every step),
    "steps": [{{"method": "GET|POST|PUT|PATCH|DELETE", "path": "/route?query",
               "json": {{...}} (only for requests with a body),
               "headers": {{...}} (optional, this step only),
               "auth": ["username", "password"] (optional, HTTP Basic auth for this step),
               "save": {{"var_name": "dotted.path.in.response"}} (optional)}}, ...]}}
Use "{{var}}" in path, json or headers to reuse saved or preset values (an id or a token returned
by an earlier step), e.g. "headers": {{"Authorization": "Bearer {{token}}"}} after a login step that
saves {{"token": "access_token"}}.
Rules:
- Only use routes that exist in the code below, with their full path (including any URL prefix the
  app registers them under). 2-8 steps per persona.
- If routes need a login, read how the code authenticates (e.g. HTTP Basic auth on a token route,
  then a Bearer token header) and do exactly that, using accounts that the seed or setup code
  creates (or that the persona registers itself first). A persona stuck at the login tests nothing.
- Log in only with accounts the seed or setup code creates (exact usernames and passwords), or
  register the account first in the same persona.
- Mix: typical users, power users, and careless users who send bad input (zero, negative, empty,
  missing fields, unknown ids, duplicates, boundary values). Careless users matter: they find lost validation.
- Every persona must work on its own against a freshly seeded database (no persona depends on another).
{guide}{focus}
App source:
{source}
"""

FOCUS = """- About half of the personas must walk through the behavior touched by this pull request diff
  (they should call the routes whose code changed, with inputs that reach the changed lines).
  After any request that changes data, read the affected data back (the list, the object, the
  feed), because a wrong side effect only shows up in what the app returns afterwards:
{diff}
"""

PROBES = """- Probes: {lead} exactly one probe persona per suspicion below (so {total} personas in
  all). A probe has "probe": "<suspicion id>" and a name starting with "probe". It logs in if the
  route needs it, sends exactly the input that would trigger the suspicion (use ids that exist in
  the seed data or that an earlier step created and saved, never an unfilled placeholder), and then
  reads back whatever would show the effect. Suspicions:
{suspicions}
"""


def read_source(app_dir: str | Path, limit: int = MAX_SOURCE_CHARS) -> str:
    root = Path(app_dir).resolve()
    parts, used = [], 0
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if not p.is_file() or SKIP_PARTS & set(rel.parts) or p.suffix not in SOURCE_SUFFIXES:
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        block = f"### {rel.as_posix()}\n{text}\n"
        if used + len(block) > limit:
            break
        parts.append(block)
        used += len(block)
    return "".join(parts)


def code_diff(base_dir: str | Path, head_dir: str | Path) -> str:
    a, b = Path(base_dir).resolve(), Path(head_dir).resolve()
    out = []
    names = {p.relative_to(a).as_posix() for p in a.rglob("*") if p.is_file()} | \
            {p.relative_to(b).as_posix() for p in b.rglob("*") if p.is_file()}
    for rel in sorted(names):
        if SKIP_PARTS & set(Path(rel).parts) or Path(rel).suffix not in SOURCE_SUFFIXES:
            continue
        old = (a / rel).read_text("utf-8", "replace").splitlines(True) if (a / rel).exists() else []
        new = (b / rel).read_text("utf-8", "replace").splitlines(True) if (b / rel).exists() else []
        out += difflib.unified_diff(old, new, f"a/{rel}", f"b/{rel}")
    return "".join(out)


def parse_json(text: str, key: str = "personas"):
    """Find the {key: [...]} object in a reply, even if the model wrote its reasoning first.
    Tries every '{"<key>": ...' in the text and keeps the valid one with the longest list."""
    text = text or ""
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    decoder, best = json.JSONDecoder(), None
    for m in re.finditer(r'\{\s*"' + re.escape(key) + r'"\s*:', text):
        try:
            obj, _ = decoder.raw_decode(text[m.start():])
        except ValueError:
            continue
        if isinstance(obj.get(key), list) and (best is None or len(obj[key]) >= len(best[key])):
            best = obj
    if best is not None:
        return best
    start, end = text.find("{"), text.rfind("}")
    try:
        return json.loads(text[start:end + 1]) if start != -1 < end else None
    except ValueError:
        return None


def validate(raw) -> list[dict]:
    """Keep well-formed personas and steps; drop anything the runner couldn't replay."""
    personas, seen = [], set()
    for p in (raw or {}).get("personas", []) if isinstance(raw, dict) else []:
        if not isinstance(p, dict):
            continue
        steps = []
        for s in p.get("steps") or []:
            if not isinstance(s, dict):
                continue
            method, path = str(s.get("method", "")).upper(), s.get("path")
            if method not in METHODS or not isinstance(path, str) or not path.startswith("/"):
                continue
            step = {"method": method, "path": path}
            if isinstance(s.get("json"), (dict, list)):
                step["json"] = s["json"]
            if isinstance(s.get("save"), dict):
                step["save"] = {str(k): str(v) for k, v in s["save"].items()}
            if isinstance(s.get("headers"), dict):
                step["headers"] = {str(k): str(v) for k, v in s["headers"].items()}
            if isinstance(s.get("auth"), list) and len(s["auth"]) == 2:
                step["auth"] = [str(x) for x in s["auth"]]
            steps.append(step)
        name = str(p.get("name") or f"persona {len(personas) + 1}")[:60]
        while name in seen:
            name += "'"
        if steps:
            seen.add(name)
            persona = {"name": name, "goal": str(p.get("goal", ""))[:200],
                       "vars": p.get("vars") if isinstance(p.get("vars"), dict) else {}, "steps": steps}
            if isinstance(p.get("headers"), dict):
                persona["headers"] = {str(k): str(v) for k, v in p["headers"].items()}
            if p.get("probe"):
                persona["probe"] = str(p["probe"])[:20]
            personas.append(persona)
    return personas


def cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    m = model.lower()
    for key, (p_in, p_out) in PRICES.items():
        if key in m:
            return (tokens_in * p_in + tokens_out * p_out) / 1_000_000
    return 0.0


# Ask for JSON mode with reasoning off first; drop whatever the endpoint rejects.
ATTEMPTS = (
    {"response_format": {"type": "json_object"},
     "extra_body": {"chat_template_kwargs": {"enable_thinking": False}}},
    {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}},
    {"response_format": {"type": "json_object"}},
    {},
)


def nemotron_chat(model: str, prompt: str, temperature: float = 0.7) -> tuple[str, int, int]:
    """One Token Factory call. Returns (text, input tokens, output tokens). Tests replace this."""
    from openai import (
        APIConnectionError,
        APITimeoutError,
        BadRequestError,
        InternalServerError,
        OpenAI,
        UnprocessableEntityError,
    )
    client = OpenAI(base_url=os.getenv("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1"),
                    api_key=os.environ["NEBIUS_API_KEY"], max_retries=3, timeout=600)
    messages = [{"role": "system", "content": "Reply with the JSON object only. No reasoning, no prose."},
                {"role": "user", "content": prompt}]
    last_error = None
    for extra in ATTEMPTS:
        try:
            resp = client.chat.completions.create(model=model, temperature=temperature, max_tokens=16000,
                                                  messages=messages, **extra)
        except (BadRequestError, UnprocessableEntityError) as e:  # option not supported here: try the next
            last_error = e
            continue
        except (InternalServerError, APIConnectionError, APITimeoutError) as e:
            # the client already retried; a 500 that repeats can be tied to an option (JSON mode,
            # reasoning off) for this prompt, so carry on with the next, plainer request
            print(f"[nemotron] {type(e).__name__} with {sorted(extra) or 'plain request'}, trying the next option",
                  file=sys.stderr)
            last_error = e
            continue
        choice, u = resp.choices[0], resp.usage
        if choice.finish_reason == "length":
            print("[nemotron] warning: reply hit max_tokens and may be cut off",
                  file=sys.stderr)
        return choice.message.content or "", u.prompt_tokens or 0, u.completion_tokens or 0
    raise last_error


def generate(app_dir, n: int = 12, head_dir=None, model: str | None = None, chat=nemotron_chat,
             guide: str = "", suspicions: list[dict] | None = None, probes_only: bool = False) -> dict:
    """Returns {"personas": [...], "model": ..., "usd": ..., "dropped": ...}. Asks again once if the
    model returns fewer than half the personas asked for."""
    model = model or os.getenv("PERSONA_MODEL", "nvidia/Nemotron-3_5-Lightning")
    diff = code_diff(app_dir, head_dir) if head_dir else ""
    focus = FOCUS.format(diff=diff[:60_000]) if diff else ""
    if suspicions:
        listed = "\n".join(f"  {x['id']}: {x['claim']} (route: {x.get('route')}; trigger: {x.get('trigger')})"
                           for x in suspicions)
        if probes_only:  # only the probes: n is the number of suspicions
            n, focus = len(suspicions), ""
        focus += PROBES.format(lead="write only" if probes_only else "in addition, write",
                               total=n if probes_only else n + len(suspicions), suspicions=listed)
    notes = f"- Notes from the app's owner: {guide}\n" if guide else ""
    prompt = PROMPT.format(n=n, guide=notes, focus=focus, source=read_source(app_dir))
    best, usd, tokens, used = ([], "", 0), 0.0, [0, 0], model
    # Lightning first (cheap); if it twice returns too few usable personas, the strong model tries once
    fallback = os.getenv("PERSONA_FALLBACK_MODEL", "nvidia/Nemotron-3-Ultra-550b-a55b")
    for m in [model, model] + ([fallback] if fallback and fallback != model else []):
        text, tin, tout = chat(m, prompt)
        usd += cost_usd(m, tin, tout)
        tokens = [tokens[0] + tin, tokens[1] + tout]
        raw = parse_json(text)
        personas = validate(raw)
        asked = len(raw.get("personas", [])) if isinstance(raw, dict) else 0
        if len(personas) > len(best[0]):
            best, used = (personas, text, asked), m
        if len(best[0]) * 2 >= n:
            break
    personas, text, asked = best
    if not personas:
        raise RuntimeError(f"{model} returned no usable personas. Reply started with: {text[:300]!r}")
    if probes_only and suspicions:  # every persona from this call is a probe, even if the tag was dropped
        ids = [x["id"] for x in suspicions]
        for i, p in enumerate(personas):
            p.setdefault("probe", ids[i] if i < len(ids) else "probe")
            if not p["name"].lower().startswith("probe"):
                p["name"] = f"probe {p['probe']}: {p['name']}"
    return {"personas": personas, "model": used, "usd": usd, "tokens": tokens,
            "dropped": max(asked - len(personas), 0) if isinstance(asked, int) else 0}
