"""Doppel's traffic recorder: runs INSIDE the app's own test process and writes down every request its tests
send through a framework test client, with the response, plus how each test ended.

Loaded by a generated `sitecustomize.py` (PYTHONPATH) and as a pytest plugin (PYTEST_PLUGINS); copied into
the sandbox next to the app, never imported by Doppel itself. Design rules, each preventing a class of bugs:

- standard library only: it runs in the app's environment, whatever that has installed;
- patches a test client only when the app imports it (post-import hook): Doppel never imports or
  initialises a framework the app does not use, and import order stays the app's;
- every recording step is wrapped: a recorder failure is written to errors-<pid>.log, never raised, so it
  can't fail, skip or change a test;
- one output file per process: parallel runners (pytest-xdist) never interleave writes;
- only the outermost request is recorded (a test client following redirects calls itself again);
- an existing sitecustomize of the project still runs (chained, see doppel/traffic.py).

Hooked clients: werkzeug.test.Client (Flask and every Werkzeug app), django.test.Client (Django, DRF's
APIClient), and whichever starlette.testclient transport has handle_request (FastAPI, Starlette).

Output, under $DOPPEL_TRAFFIC_OUT:
  traffic-<pid>.jsonl   {"test", "phase", "seq", "method", "path", "query", "sent", "status", "type", "body", "ms"}
  outcomes-<pid>.jsonl  {"test", "when", "outcome"}
"""
from __future__ import annotations

import contextvars
import importlib.abc
import importlib.util
import json
import os
import sys
import threading
import time
import traceback

OUT = os.environ.get("DOPPEL_TRAFFIC_OUT", "")
MAX_BODY = 256 * 1024
_lock = threading.Lock()
_seq: dict[str, int] = {}
_local = threading.local()


# ---------------------------------------------------------------- writing

def _write(kind: str, entry: dict) -> None:
    if not OUT:
        return
    with _lock:
        with open(os.path.join(OUT, f"{kind}-{os.getpid()}.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")


def _error(where: str) -> None:
    try:
        if OUT:
            with open(os.path.join(OUT, f"errors-{os.getpid()}.log"), "a", encoding="utf-8") as f:
                f.write(f"--- {where}\n{traceback.format_exc()}\n")
    except Exception:  # noqa: BLE001 - the recorder must never raise
        pass


def current_test() -> tuple[str, str]:
    """(test id, phase): pytest's PYTEST_CURRENT_TEST, else the unittest.TestCase on the call stack."""
    current = os.environ.get("PYTEST_CURRENT_TEST", "")
    if current:
        node, _, phase = current.rpartition(" ")
        return (node or current), phase.strip("()")
    try:
        import unittest
        frame = sys._getframe(2)
        while frame is not None:
            me = frame.f_locals.get("self")
            if isinstance(me, unittest.TestCase):
                return me.id(), "call"
            frame = frame.f_back
    except Exception:  # noqa: BLE001
        pass
    return "(outside any test)", ""


def _text(data) -> str:
    if data is None:
        return ""
    if isinstance(data, (bytes, bytearray)):
        return bytes(data[:MAX_BODY]).decode("utf-8", "replace")
    if isinstance(data, str):
        return data[:MAX_BODY]
    try:
        return json.dumps(data, default=str)[:MAX_BODY]
    except Exception:  # noqa: BLE001
        return str(data)[:MAX_BODY]


def record(method: str, path: str, query: str, sent, status: int, ctype: str, body, ms: float) -> None:
    try:
        test, phase = current_test()
        with _lock:
            seq = _seq.get(test, 0)
            _seq[test] = seq + 1
        _write("traffic", {"test": test, "phase": phase, "seq": seq, "method": str(method).upper(),
                           "path": str(path), "query": str(query or ""), "sent": _text(sent)[:4000],
                           "status": int(status), "type": str(ctype or ""), "body": _text(body),
                           "ms": round(ms, 2)})
    except Exception:  # noqa: BLE001
        _error("record")


_depth = contextvars.ContextVar("doppel_depth", default=0)


class _Outermost:
    """Record only the outermost call (a client following redirects, or a test client built on httpx, calls
    itself again). A context variable, not a thread-local: concurrent asyncio requests share one thread, and
    each task has its own context."""

    def __enter__(self):
        depth = _depth.get()
        self._token = _depth.set(depth + 1)
        return depth == 0

    def __exit__(self, *exc):
        _depth.reset(self._token)
        return False


# ---------------------------------------------------------------- hooks, one per test client

def _hook_werkzeug(module) -> None:
    Client = getattr(module, "Client", None)  # noqa: N806
    if Client is None or getattr(Client.open, "_doppel", False):
        return
    original = Client.open

    def open(self, *args, **kwargs):  # noqa: A001 - same name as the method it wraps
        with _Outermost() as outermost:
            t0 = time.perf_counter()
            response = original(self, *args, **kwargs)
            if outermost:
                try:
                    req = getattr(response, "request", None)
                    method = getattr(req, "method", kwargs.get("method", "GET"))
                    path = getattr(req, "path", args[0] if args and isinstance(args[0], str) else "")
                    query = getattr(req, "query_string", b"")
                    query = query.decode("latin-1") if isinstance(query, bytes) else query
                    sent = kwargs.get("json", kwargs.get("data"))
                    record(method, path, query, sent, response.status_code, response.headers.get("Content-Type", ""),
                           response.get_data(), (time.perf_counter() - t0) * 1000)
                except Exception:  # noqa: BLE001
                    _error("werkzeug")
            return response
    open._doppel = True
    Client.open = open


def _hook_django(module) -> None:
    Client = getattr(module, "Client", None)  # noqa: N806
    if Client is None or getattr(Client.request, "_doppel", False):
        return
    original = Client.request

    def request(self, **environ):
        sent = None
        try:
            payload = environ.get("wsgi.input")
            content = getattr(payload, "_FakePayload__content", None)
            sent = content.getvalue() if content is not None else None
        except Exception:  # noqa: BLE001
            pass
        with _Outermost() as outermost:
            t0 = time.perf_counter()
            try:
                response = original(self, **environ)
            except Exception as e:
                if outermost:  # the app raised through the test client: a crash, record it as one
                    record(environ.get("REQUEST_METHOD", "GET"), environ.get("PATH_INFO", ""),
                           environ.get("QUERY_STRING", ""), sent, 0, "text/plain",
                           f"{type(e).__name__}: {e}", (time.perf_counter() - t0) * 1000)
                raise
            if outermost:
                try:
                    streaming = getattr(response, "streaming", False)
                    body = "(streaming response)" if streaming else getattr(response, "content", b"")
                    record(environ.get("REQUEST_METHOD", "GET"), environ.get("PATH_INFO", ""),
                           environ.get("QUERY_STRING", ""), sent, response.status_code,
                           response.get("Content-Type", "") if hasattr(response, "get") else "", body,
                           (time.perf_counter() - t0) * 1000)
                except Exception:  # noqa: BLE001
                    _error("django")
            return response
    request._doppel = True
    Client.request = request


def _hook_starlette(module) -> None:
    for name in dir(module):  # Starlette renames its private transport between versions: find it by shape
        cls = getattr(module, name, None)
        handle = getattr(cls, "handle_request", None) if isinstance(cls, type) else None
        if handle is None or getattr(handle, "_doppel", False) or "Transport" not in name:
            continue

        def make(original):
            def handle_request(self, request):
                with _Outermost() as outermost:
                    t0 = time.perf_counter()
                    response = original(self, request)
                    if outermost:
                        try:
                            try:
                                sent = request.content
                            except Exception:  # noqa: BLE001 - a streamed body
                                sent = None
                            response.read()
                            record(request.method, request.url.path, request.url.query, sent, response.status_code,
                                   response.headers.get("content-type", ""), response.content,
                                   (time.perf_counter() - t0) * 1000)
                        except Exception:  # noqa: BLE001
                            _error("starlette")
                    return response
            handle_request._doppel = True
            return handle_request
        cls.handle_request = make(handle)


LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]", "testserver"}


def _is_local(host) -> bool:
    """Live-server tests talk to the app on this machine; anything else is the app calling out (never recorded)."""
    return str(host or "").lower().strip("[]") in {h.strip("[]") for h in LOCAL_HOSTS}


def _httpx_record(request, response, t0) -> None:
    try:
        try:
            sent = request.content
        except Exception:  # noqa: BLE001 - a streamed body
            sent = None
        record(request.method, request.url.path, request.url.query.decode("latin-1")
               if isinstance(request.url.query, bytes) else request.url.query, sent, response.status_code,
               response.headers.get("content-type", ""), response.content, (time.perf_counter() - t0) * 1000)
    except Exception:  # noqa: BLE001
        _error("httpx")


def _hook_httpx(module) -> None:
    """httpx transports: ASGI (FastAPI/Starlette async tests: AsyncClient(transport=ASGITransport(app))),
    WSGI (Flask/Django through httpx), and the network transports, for requests to a live local server."""
    def wrap_sync(cls, local_only):
        original = getattr(cls, "handle_request", None)
        if original is None or getattr(original, "_doppel", False):
            return

        def handle_request(self, request):
            with _Outermost() as outermost:
                t0 = time.perf_counter()
                response = original(self, request)
                if outermost and (not local_only or _is_local(request.url.host)):
                    try:
                        response.read()
                    except Exception:  # noqa: BLE001
                        _error("httpx read")
                        return response
                    _httpx_record(request, response, t0)
                return response
        handle_request._doppel = True
        cls.handle_request = handle_request

    def wrap_async(cls, local_only):
        original = getattr(cls, "handle_async_request", None)
        if original is None or getattr(original, "_doppel", False):
            return

        async def handle_async_request(self, request):
            with _Outermost() as outermost:
                t0 = time.perf_counter()
                response = await original(self, request)
                if outermost and (not local_only or _is_local(request.url.host)):
                    try:
                        await response.aread()
                    except Exception:  # noqa: BLE001
                        _error("httpx aread")
                        return response
                    _httpx_record(request, response, t0)
                return response
        handle_async_request._doppel = True
        cls.handle_async_request = handle_async_request

    for name, kind, local_only in (("ASGITransport", "async", False), ("WSGITransport", "sync", False),
                                   ("HTTPTransport", "sync", True), ("AsyncHTTPTransport", "async", True)):
        cls = getattr(module, name, None)
        if isinstance(cls, type):
            (wrap_async if kind == "async" else wrap_sync)(cls, local_only)


def _hook_requests(module) -> None:
    """requests against a live local server (Django LiveServerTestCase, pytest-flask's live_server, ...)."""
    HTTPAdapter = getattr(module, "HTTPAdapter", None)  # noqa: N806
    if HTTPAdapter is None or getattr(HTTPAdapter.send, "_doppel", False):
        return
    original = HTTPAdapter.send

    def send(self, request, *args, **kwargs):
        with _Outermost() as outermost:
            t0 = time.perf_counter()
            response = original(self, request, *args, **kwargs)
            if outermost:
                try:
                    from urllib.parse import urlsplit
                    url = urlsplit(request.url)
                    if _is_local(url.hostname):
                        record(request.method, url.path or "/", url.query, request.body, response.status_code,
                               response.headers.get("Content-Type", ""), response.content,
                               (time.perf_counter() - t0) * 1000)
                except Exception:  # noqa: BLE001
                    _error("requests")
            return response
    send._doppel = True
    HTTPAdapter.send = send


def _hook_django_async(module) -> None:
    AsyncClient = getattr(module, "AsyncClient", None)  # noqa: N806
    if AsyncClient is None or getattr(AsyncClient.request, "_doppel", False):
        return
    original = AsyncClient.request

    async def request(self, **scope):
        with _Outermost() as outermost:
            t0 = time.perf_counter()
            try:
                response = await original(self, **scope)
            except Exception as e:
                if outermost:
                    record(scope.get("method", "GET"), scope.get("path", ""), _qs(scope.get("query_string", "")),
                           None, 0, "text/plain", f"{type(e).__name__}: {e}", (time.perf_counter() - t0) * 1000)
                raise
            if outermost:
                try:
                    body = "(streaming response)" if getattr(response, "streaming", False) else response.content
                    record(scope.get("method", "GET"), scope.get("path", ""), _qs(scope.get("query_string", "")),
                           None, response.status_code, response.get("Content-Type", ""), body,
                           (time.perf_counter() - t0) * 1000)
                except Exception:  # noqa: BLE001
                    _error("django async")
            return response
    request._doppel = True
    AsyncClient.request = request


def _qs(value) -> str:
    return value.decode("latin-1") if isinstance(value, bytes) else str(value or "")


def _hook_django_all(module) -> None:
    _hook_django(module)
    _hook_django_async(module)


HOOKS = {"werkzeug.test": _hook_werkzeug, "django.test.client": _hook_django_all,
         "starlette.testclient": _hook_starlette, "httpx": _hook_httpx, "requests.adapters": _hook_requests}


class _PostImport(importlib.abc.MetaPathFinder):
    """Runs a hook right after one of HOOKS' modules is imported, by the app, whenever that happens."""

    def find_spec(self, fullname, path, target=None):
        if fullname not in HOOKS or getattr(_local, "finding", False):
            return None
        _local.finding = True
        try:
            spec = importlib.util.find_spec(fullname)
        finally:
            _local.finding = False
        if spec is None or spec.loader is None:
            return None
        loader, hook = spec.loader, HOOKS[fullname]

        class Loader(importlib.abc.Loader):
            def create_module(self, s):
                return loader.create_module(s)

            def exec_module(self, module):
                loader.exec_module(module)
                try:
                    hook(module)
                except Exception:  # noqa: BLE001
                    _error(f"hook {fullname}")
        spec.loader = Loader()
        return spec


# ---------------------------------------------------------------- test outcomes outside pytest (unittest runners)

def _hook_unittest() -> None:
    import unittest.result as ur
    for method, outcome in (("addSuccess", "passed"), ("addFailure", "failed"), ("addError", "failed"),
                            ("addSkip", "skipped"), ("addExpectedFailure", "passed"),
                            ("addUnexpectedSuccess", "failed")):
        original = getattr(ur.TestResult, method)

        def make(original, outcome):
            def wrapped(self, test, *args):
                try:
                    if "PYTEST_CURRENT_TEST" not in os.environ:  # pytest reports through the plugin instead
                        entry = {"test": test.id(), "when": "call", "outcome": outcome}
                        if outcome == "failed" and args and isinstance(args[0], tuple) and len(args[0]) == 3:
                            entry["message"] = "".join(traceback.format_exception(*args[0]))[-1500:]
                        _write("outcomes", entry)
                except Exception:  # noqa: BLE001
                    _error("unittest outcome")
                return original(self, test, *args)
            return wrapped
        setattr(ur.TestResult, method, make(original, outcome))


def install() -> None:
    """Called once from the generated sitecustomize."""
    if not OUT or getattr(sys, "_doppel_traffic", False):
        return
    sys._doppel_traffic = True
    try:
        os.makedirs(OUT, exist_ok=True)
        for name, hook in HOOKS.items():   # already imported (rare at startup): hook it now
            if name in sys.modules:
                hook(sys.modules[name])
        sys.meta_path.insert(0, _PostImport())
        _hook_unittest()
    except Exception:  # noqa: BLE001
        _error("install")


# ---------------------------------------------------------------- pytest plugin (loaded via PYTEST_PLUGINS)

def pytest_runtest_logreport(report) -> None:
    try:
        if report.when == "call" or report.outcome != "passed":
            entry = {"test": report.nodeid, "when": report.when, "outcome": report.outcome}
            if report.outcome == "failed":
                entry["message"] = str(getattr(report, "longreprtext", "") or "")[-1500:]
            _write("outcomes", entry)
    except Exception:  # noqa: BLE001
        _error("pytest outcome")
