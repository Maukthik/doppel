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


class _Outermost:
    """Record only the outermost call on this thread (redirect following re-enters the client)."""

    def __enter__(self):
        depth = getattr(_local, "depth", 0)
        _local.depth = depth + 1
        return depth == 0

    def __exit__(self, *exc):
        _local.depth -= 1
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


HOOKS = {"werkzeug.test": _hook_werkzeug, "django.test.client": _hook_django, "starlette.testclient": _hook_starlette}


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
                        _write("outcomes", {"test": test.id(), "when": "call", "outcome": outcome})
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
            _write("outcomes", {"test": report.nodeid, "when": report.when, "outcome": report.outcome})
    except Exception:  # noqa: BLE001
        _error("pytest outcome")
