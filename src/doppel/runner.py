"""Replay personas against a running app and record what it answered.

Standard library only: this file is copied into the sandbox and runs there, next to
the app, so it must work in any Python image without installing anything.

    python runner.py --base-url http://127.0.0.1:8000 --personas personas.json
prints a JSON list of recordings to stdout.

A persona is {"name": ..., "vars": {...}, "steps": [step, ...]}. A step is
    {"method": "POST", "path": "/orders", "json": {...}, "save": {"order_id": "id"}}
"{name}" placeholders in path and json are filled from vars; "save" copies values from
the JSON response (dotted paths like "items.0.id") into vars for later steps.
"""
from __future__ import annotations

import argparse
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


def call(base_url: str, method: str, path: str, body, timeout: float):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
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


def replay(base_url: str, personas: list[dict], timeout: float = 10.0) -> list[dict]:
    recordings = []
    for persona in personas:
        variables = dict(persona.get("vars") or {})
        steps = []
        for i, step in enumerate(persona.get("steps") or []):
            method = str(step.get("method", "GET")).upper()
            path = str(render(step.get("path", "/"), variables))
            body = render(step["json"], variables) if "json" in step else None
            status, payload, ms = call(base_url, method, path, body, timeout)
            for var, dotted in (step.get("save") or {}).items():
                value = extract(payload, dotted)
                if value is not None:
                    variables[var] = value
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
