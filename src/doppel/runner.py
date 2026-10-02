"""Replay personas against a running app and record what it answered.

Standard library only: this file is copied into the sandbox and runs there, next to
the app, so it must work in any Python image without installing anything.

    python runner.py --base-url http://127.0.0.1:8000 --personas personas.json
prints a JSON list of recordings to stdout.

A persona is {"name": ..., "vars": {...}, "headers": {...}, "steps": [step, ...]}. A step is
    {"method": "POST", "path": "/orders", "json": {...}, "save": {"order_id": "id"},
     "headers": {"Authorization": "Bearer {token}"}, "auth": ["alice", "secret"]}
"{name}" placeholders in path, json and headers are filled from vars; "save" copies values from
the JSON response (dotted paths like "items.0.id") into vars for later steps. Persona headers
apply to every step (step headers win); "auth" sends HTTP Basic credentials.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import time
import urllib.error
import urllib.request

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def render(value, variables: dict):
    """Fill {name} placeholders. A string that is exactly one placeholder keeps the
    variable's type (so {"product_id": "{pid}"} sends a number, not "7")."""
    if isinstance(value, str):
        whole = _PLACEHOLDER.fullmatch(value)
        if whole and whole.group(1) in variables:
            return variables[whole.group(1)]
        return _PLACEHOLDER.sub(lambda m: str(variables.get(m.group(1), m.group(0))), value)
    if isinstance(value, list):
        return [render(v, variables) for v in value]
    if isinstance(value, dict):
        return {k: render(v, variables) for k, v in value.items()}
    return value


def extract(obj, path: str):
    """Follow a dotted path ("items.0.id") into parsed JSON; None if it isn't there."""
    for part in path.split("."):
        if isinstance(obj, list) and part.isdigit() and int(part) < len(obj):
            obj = obj[int(part)]
        elif isinstance(obj, dict) and part in obj:
            obj = obj[part]
        else:
            return None
    return obj


def build_headers(persona: dict, step: dict, variables: dict) -> dict:
    """Persona headers, then step headers, then Basic auth; header values are always strings.
    A header whose placeholder isn't filled yet (no token saved so far) is sent as written,
    the same way in both worlds."""
    headers = {}
    for source in (persona.get("headers"), step.get("headers")):
        if isinstance(source, dict):
            headers.update({str(k): str(render(v, variables)) for k, v in source.items()})
    auth = step.get("auth")
    if isinstance(auth, (list, tuple)) and len(auth) == 2:
        user, password = (str(render(x, variables)) for x in auth)
        headers["Authorization"] = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
    return headers


def call(base_url: str, method: str, path: str, body, timeout: float, headers: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {**({"Content-Type": "application/json"} if data is not None else {}), **(headers or {})}
    req = urllib.request.Request(base_url.rstrip("/") + path, data=data, method=method, headers=headers)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    except Exception as e:  # connection refused, timeout, reset: the app is broken
        status, raw = 0, f"{type(e).__name__}: {e}".encode()
    ms = (time.perf_counter() - t0) * 1000
    try:
        payload = json.loads(raw) if raw else None
    except ValueError:
        payload = raw.decode("utf-8", "replace")[:2000]
    return status, payload, round(ms, 1)


def placeholders(value) -> set[str]:
    """Every {name} used anywhere in a persona (paths, bodies, headers)."""
    return set(_PLACEHOLDER.findall(json.dumps(value)))


def auto_capture(payload, wanted: set[str], variables: dict, path: str = "") -> None:
    """Fill placeholders nobody saved from the response that obviously provides them: a key with the
    same name, or, for a token-like name ("token", "access_token", "jwt"), the response's access token.
    Personas often log in and use {token} but forget the "save"; without this they stay at 401."""
    if not isinstance(payload, dict):
        return
    for name in sorted(wanted - set(variables)):
        if isinstance(payload.get(name), (str, int, float)):
            variables[name] = payload[name]
        elif name.lower().endswith("_id") and isinstance(payload.get("id"), (int, str)) \
                and name.lower()[:-3] in path.lower():  # {post_id} from the "id" of POST /posts
            variables[name] = payload["id"]
        elif name.lower().endswith(("token", "jwt")) or name.lower() in ("auth", "bearer", "access"):
            keys = [k for k, v in payload.items() if isinstance(v, str) and v and
                    any(t in k.lower() for t in ("token", "jwt"))]
            keys.sort(key=lambda k: (k.lower() != "access_token", "refresh" in k.lower()))
            if keys:
                variables[name] = payload[keys[0]]


def replay(base_url: str, personas: list[dict], timeout: float = 10.0) -> list[dict]:
    recordings = []
    for persona in personas:
        variables = dict(persona.get("vars") or {})
        wanted = placeholders({k: persona.get(k) for k in ("headers", "steps")})
        saved = {v for step in persona.get("steps") or [] for v in (step.get("save") or {})}
        steps = []
        for i, step in enumerate(persona.get("steps") or []):
            method = str(step.get("method", "GET")).upper()
            path = str(render(step.get("path", "/"), variables))
            body = render(step["json"], variables) if "json" in step else None
            status, payload, ms = call(base_url, method, path, body, timeout,
                                       build_headers(persona, step, variables))
            for var, dotted in (step.get("save") or {}).items():
                value = extract(payload, dotted)
                if value is not None:
                    variables[var] = value
            if 200 <= status < 300:
                auto_capture(payload, wanted - saved, variables, path)
            steps.append({"i": i, "request": f"{method} {path}", "sent": body,
                          "status": status, "body": payload, "ms": ms})
        recordings.append({"persona": persona.get("name", "?"), "steps": steps})
    return recordings


def wait_ready(base_url: str, path: str = "/", timeout: float = 30.0) -> bool:
    """Poll until the app answers anything below 500, or give up after timeout seconds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, _, _ = call(base_url, "GET", path, None, timeout=2)
        if 0 < status < 500:
            return True
        time.sleep(0.2)
    return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Replay personas against a running app (stdlib only).")
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--personas", required=True, help="JSON file with a list of personas")
    ap.add_argument("--ready-path", default="/")
    ap.add_argument("--ready-timeout", type=float, default=30.0)
    ap.add_argument("--timeout", type=float, default=10.0, help="per-request timeout in seconds")
    a = ap.parse_args(argv)
    with open(a.personas, encoding="utf-8") as f:
        personas = json.load(f)
    if not wait_ready(a.base_url, a.ready_path, a.ready_timeout):
        print(json.dumps({"error": f"app not ready at {a.base_url}{a.ready_path}"}))
        return 2
    json.dump(replay(a.base_url, personas, a.timeout), sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
