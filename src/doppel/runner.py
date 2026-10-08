"""Replay personas against a running app and record what it answered.

Standard library only: this file is copied into the sandbox and runs there, next to
the app, so it must work in any Python image without installing anything.

    python runner.py --base-url http://127.0.0.1:8000 --personas personas.json
prints a JSON list of recordings to stdout.

A persona is {"name": ..., "vars": {...}, "headers": {...}, "steps": [step, ...]}. A step is
    {"method": "POST", "path": "/orders", "json": {...}, "save": {"order_id": "id"},
     "headers": {"Authorization": "Bearer {token}"}, "auth": ["alice", "secret"]}
or, for an HTML form, "form": {"username": "alice", "password": "secret"} instead of "json".
Each persona keeps its own cookies across its steps, like one browser: a session cookie set by a
login stays for the next request. When the app has set a CSRF cookie (Django's csrftoken, Flask-WTF,
...), unsafe requests send it back as an X-CSRFToken header, which those frameworks accept.
"{name}" placeholders in path, json and headers are filled from vars; "save" copies values from
the JSON response (dotted paths like "items.0.id") into vars for later steps. Persona headers
apply to every step (step headers win); "auth" sends HTTP Basic credentials.
"""
from __future__ import annotations

import argparse
import base64
import html.parser
import http.cookiejar
import json
import re
import sys
import time
import urllib.error
import urllib.parse
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


UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
TOKEN_FIELD = re.compile(r"csrf|xsrf|authenticity_token|nonce|__requestverificationtoken", re.I)
MAX_LINES, MAX_LINKS, MAX_TEXT = 400, 200, 10_000


class _Page(html.parser.HTMLParser):
    """What a person sees and can do on an HTML page: title, visible text lines, forms, links.
    Attributes, scripts and styles are dropped; CSRF tokens and nonces are masked, so two loads of
    the same page compare equal and a change in the content shows up as the line that changed."""
    SKIP = {"script", "style", "noscript", "template", "svg"}
    BLOCK = {"p", "div", "li", "tr", "br", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article",
             "header", "footer", "nav", "main", "table", "ul", "ol", "dt", "dd", "form", "label", "option",
             "button", "td", "th", "pre", "blockquote", "title"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.lines, self.forms, self.links = "", [], [], []
        self.hidden: list[tuple[str, dict]] = []  # (action, raw hidden fields): what a browser submits back
        self._buf, self._skip, self._in_title = [], 0, False

    def _flush(self):
        line = " ".join("".join(self._buf).split())
        if line and len(self.lines) < MAX_LINES:
            self.lines.append(line[:300])
        self._buf = []

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag in self.SKIP:
            self._skip += 1
        if tag in self.BLOCK:
            self._flush()
        if tag == "title":
            self._in_title = True
        elif tag == "form":
            self.forms.append({"action": a.get("action", ""), "method": a.get("method", "get").lower(),
                               "fields": {}})
            self.hidden.append((a.get("action", ""), {}))
        elif tag in ("input", "select", "textarea") and self.forms and a.get("name"):
            name = a["name"]
            if tag == "input" and a.get("type", "").lower() == "hidden":
                self.hidden[-1][1][name] = a.get("value", "")
            value = "(token)" if TOKEN_FIELD.search(name) else a.get("value", "")
            if a.get("type") in ("checkbox", "radio"):
                value = f"{value} checked" if "checked" in a else value
            self.forms[-1]["fields"][name] = value
        elif tag == "meta" and TOKEN_FIELD.search(a.get("name", "")):
            pass  # <meta name="csrf-token" content="..."> is noise, never content
        elif tag == "a" and a.get("href") and len(self.links) < MAX_LINKS:
            self.links.append(a["href"])

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in self.BLOCK:
            self._flush()

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self.title += data.strip()
            return
        self._buf.append(data)


def _parse(text: str) -> _Page:
    page = _Page()
    try:
        page.feed(text)
        page.close()
    except Exception:  # broken markup: keep what was parsed
        pass
    page._flush()
    return page


def own_origin_relative(value, base_url: str):
    """The app's own absolute URLs as paths ("http://127.0.0.1:51234/snippets/1/" -> "/snippets/1/"):
    hyperlinked APIs (DRF, HATEOAS, pagination links, Location) build them from the request's host and
    port, which differ between worlds and runs, so they are noise, never behavior."""
    parts = urllib.parse.urlsplit(base_url)
    origins = {f"{parts.scheme}://{parts.netloc}"}
    if parts.hostname in ("127.0.0.1", "localhost"):
        origins |= {f"{parts.scheme}://{h}:{parts.port}" for h in ("127.0.0.1", "localhost") if parts.port}
    return _replace_origins(value, sorted(origins, key=len, reverse=True))


def _replace_origins(value, origins):
    if isinstance(value, dict):
        return {k: _replace_origins(v, origins) for k, v in value.items()}
    if isinstance(value, list):
        return [_replace_origins(v, origins) for v in value]
    if isinstance(value, str) and "://" in value:
        for o in origins:
            value = value.replace(o + "/", "/").replace(o, "/")
    return value


def page_view(text: str) -> dict:
    """An HTML page as {"html": {"title", "text": [lines], "forms": [...], "links": [...]}}."""
    page = _parse(text)
    return {"html": {"title": page.title, "text": page.lines, "forms": page.forms, "links": page.links}}


def remember_hidden(forms_seen: dict, url: str, text: str) -> None:
    """Hidden fields of every form on a page, by the path each form posts to (a form with no action
    posts back to the page itself). Flask-WTF, Django and Rails keep their CSRF token there."""
    for action, fields in _parse(text).hidden:
        if fields:
            target = urllib.parse.urlsplit(urllib.parse.urljoin(url, action or url)).path
            forms_seen[target] = fields


def with_hidden(forms_seen: dict | None, path: str, form: dict) -> dict:
    """The form as a browser submits it: the page's hidden fields, then the persona's own values. A
    value the persona copied from a recording ("(token)") is replaced by the real one."""
    hidden = (forms_seen or {}).get(urllib.parse.urlsplit(path).path) or {}
    merged = {**hidden, **form}
    return {k: hidden[k] if v == "(token)" and k in hidden else v for k, v in merged.items()}


def is_html(content_type: str, text: str) -> bool:
    head = text.lstrip()[:100].lower()
    return "html" in (content_type or "").lower() or head.startswith(("<!doctype html", "<html"))


def browser() -> tuple[urllib.request.OpenerDirector, http.cookiejar.CookieJar]:
    """An opener that keeps cookies, one per persona."""
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar)), jar


def csrf_headers(jar: http.cookiejar.CookieJar | None, method: str, headers: dict) -> dict:
    """Send a CSRF cookie back as the header Django and Flask-WTF read, on unsafe requests only."""
    if jar is None or method not in UNSAFE or any(k.lower() in ("x-csrftoken", "x-csrf-token") for k in headers):
        return {}
    token = next((c.value for c in jar if "csrf" in c.name.lower() and c.value), None)
    return {"X-CSRFToken": token} if token else {}


def call(base_url: str, method: str, path: str, body, timeout: float, headers: dict | None = None,
         form=None, opener=None, jar=None, forms_seen: dict | None = None):
    if form is not None:
        form = with_hidden(forms_seen, path, form)
        data = urllib.parse.urlencode(form, doseq=True).encode()
        kind = {"Content-Type": "application/x-www-form-urlencoded"}
    elif body is not None:
        data, kind = json.dumps(body).encode(), {"Content-Type": "application/json"}
    else:
        data, kind = None, {}
    headers = {**kind, **(headers or {})}
    headers.update(csrf_headers(jar, method, headers))
    url = base_url.rstrip("/") + path
    if jar is not None and method in UNSAFE:
        headers.setdefault("Referer", url)  # Django's CSRF check wants a same-origin Referer over HTTPS
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    t0 = time.perf_counter()
    try:
        with (opener.open if opener else urllib.request.urlopen)(req, timeout=timeout) as resp:
            status, raw, ctype = resp.status, resp.read(), resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        status, raw, ctype = e.code, e.read(), (e.headers.get("Content-Type", "") if e.headers else "")
    except Exception as e:  # connection refused, timeout, reset: the app is broken
        status, raw, ctype = 0, f"{type(e).__name__}: {e}".encode(), ""
    ms = (time.perf_counter() - t0) * 1000
    try:
        payload = json.loads(raw) if raw else None
    except ValueError:
        text = raw.decode("utf-8", "replace")
        payload = page_view(text) if is_html(ctype, text) else text[:MAX_TEXT]
        if forms_seen is not None and isinstance(payload, dict):
            remember_hidden(forms_seen, url, text)
    payload = own_origin_relative(payload, base_url)
    return status, payload, round(ms, 1)


APP_LOG_MARKER = "@@doppel-app-log@@"
APP_LOG_LINES = 60


def crashed(recording: dict) -> bool:
    """Did any request in this recording crash the app (no answer, or a 5xx)?"""
    return any(s.get("status", 200) == 0 or s.get("status", 200) >= 500 for s in recording.get("steps", []))


def attach_app_log(recording: dict, log_text: str) -> dict:
    """Keep the end of the app's own log on a recording whose requests crashed: the traceback is the
    only place that says WHY (a generic 500 page never does)."""
    if crashed(recording) and log_text.strip():
        recording["app_log"] = "\n".join(log_text.strip().splitlines()[-APP_LOG_LINES:])
    return recording


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
        opener, jar = browser()
        forms_seen: dict = {}  # this persona's browser: the hidden fields of the forms it was shown
        wanted = placeholders({k: persona.get(k) for k in ("headers", "steps")})
        saved = {v for step in persona.get("steps") or [] for v in (step.get("save") or {})}
        steps = []
        for i, step in enumerate(persona.get("steps") or []):
            method = str(step.get("method", "GET")).upper()
            path = str(render(step.get("path", "/"), variables))
            body = render(step["json"], variables) if "json" in step else None
            form = render(step["form"], variables) if isinstance(step.get("form"), dict) else None
            status, payload, ms = call(base_url, method, path, body, timeout,
                                       build_headers(persona, step, variables), form=form, opener=opener, jar=jar,
                                       forms_seen=forms_seen)
            for var, dotted in (step.get("save") or {}).items():
                value = extract(payload, dotted)
                if value is not None:
                    variables[var] = value
            if 200 <= status < 300:
                auto_capture(payload, wanted - saved, variables, path)
            steps.append({"i": i, "request": f"{method} {path}", "sent": body if form is None else form,
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
