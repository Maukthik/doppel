"""Did the twin actually run the code the pull request changed?

A twin that reports "no differences" is only convincing if its users reached the changed code. This
module answers that the way a coverage tool would, but only for the changed lines:

  1. changes()   diff base and head; each changed region becomes a change (file, function, lines),
                 keeping only lines that run inside functions. Module- and class-level lines run on
                 import, whatever the users do, so they can't prove anything and are left out.
  2. targets()   the lines to watch in each world: changed or added lines in head, changed or
                 removed lines in base (a removed check is "reached" if base ran it).
  3. TRACER      a sitecustomize.py put on the app's PYTHONPATH in each persona run. It records the
                 first time each watched line runs, in any process or thread, to a small file.
  4. summarize() which changes some user reached, and which no user did.

Standard library only; the tracer must run in whatever Python the app's image has.
"""
from __future__ import annotations

import ast
import difflib
from pathlib import Path

TRACE_DIR = "doppel-trace-hook"          # folder holding sitecustomize.py, put first on PYTHONPATH
TARGETS_ENV, OUT_ENV, ROOT_ENV = "DOPPEL_TRACE_TARGETS", "DOPPEL_TRACE_OUT", "DOPPEL_TRACE_ROOT"
MARKER = "@@doppel-reached@@"


def _function_lines(source: str) -> tuple[dict[int, int], dict[int, str]]:
    """For every line inside a function body: the first line of the innermost statement holding it
    (a line event fires there, even for a statement spread over several lines), and the innermost
    function's name. Lines outside functions are absent: they run on import, not per request."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}, {}
    funcs: list[tuple[ast.AST, str]] = []

    def visit(node, cls: str | None):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                funcs.append((child, f"{cls}.{child.name}" if cls else child.name))
                visit(child, None)
            elif isinstance(child, ast.ClassDef):
                visit(child, child.name)
            else:
                visit(child, cls)

    visit(tree, None)
    names: dict[int, str] = {}
    stmt_of: dict[int, int] = {}
    span = lambda n: (n.end_lineno or n.lineno) - n.lineno  # noqa: E731
    for func, name in sorted(funcs, key=lambda f: -span(f[0])):  # inner functions overwrite outer ones
        for line in range(func.lineno, (func.end_lineno or func.lineno) + 1):
            names[line] = name
    stmts = []
    for func, _ in funcs:
        for stmt in func.body:
            for sub in ast.walk(stmt):
                nested = isinstance(sub, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
                if isinstance(sub, ast.stmt) and not nested:
                    if isinstance(sub, ast.Expr) and isinstance(sub.value, ast.Constant):
                        continue  # docstrings and bare constants
                    stmts.append(sub)
    for stmt in sorted(stmts, key=lambda n: -span(n)):  # innermost statement wins
        for line in range(stmt.lineno, (stmt.end_lineno or stmt.lineno) + 1):
            stmt_of[line] = stmt.lineno
    return stmt_of, names


def _read(path: Path) -> str:
    return path.read_text("utf-8", "replace") if path.is_file() else ""


def changes(base_dir: str | Path, head_dir: str | Path, files: list[str] | None = None) -> list[dict]:
    """Changed regions of Python code that run inside functions, one entry per region."""
    base_dir, head_dir = Path(base_dir), Path(head_dir)
    if files is None:
        from doppel.backends.nebius import changed_files
        files = changed_files(base_dir, head_dir)
    out: list[dict] = []
    for rel in files:
        if not rel.endswith(".py"):
            continue
        old, new = _read(base_dir / rel), _read(head_dir / rel)
        (old_stmt, old_names), (new_stmt, new_names) = _function_lines(old), _function_lines(new)
        matcher = difflib.SequenceMatcher(a=old.splitlines(), b=new.splitlines(), autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue
            base_lines = sorted({old_stmt[n] for n in range(i1 + 1, i2 + 1) if n in old_stmt})
            head_lines = sorted({new_stmt[n] for n in range(j1 + 1, j2 + 1) if n in new_stmt})
            if not base_lines and not head_lines:
                continue  # comments, docstrings, imports, class fields: nothing a request runs
            where = (new_names.get(head_lines[0]) if head_lines else None) or \
                    (old_names.get(base_lines[0]) if base_lines else None) or "?"
            if out and out[-1]["file"] == rel and out[-1]["where"] == where:
                out[-1]["base_lines"] = sorted(set(out[-1]["base_lines"]) | set(base_lines))  # one function,
                out[-1]["head_lines"] = sorted(set(out[-1]["head_lines"]) | set(head_lines))  # one change
                continue
            out.append({"id": f"c{len(out) + 1}", "file": rel, "where": where,
                        "base_lines": base_lines, "head_lines": head_lines})
    return out


def targets(found: list[dict]) -> dict[str, dict[str, list[int]]]:
    """Lines to watch per world: {"base": {file: [lines]}, "head": {...}}."""
    out: dict[str, dict[str, list[int]]] = {"base": {}, "head": {}}
    for c in found:
        for world, key in (("base", "base_lines"), ("head", "head_lines")):
            if c[key]:
                out[world].setdefault(c["file"], [])
                out[world][c["file"]] = sorted(set(out[world][c["file"]]) | set(c[key]))
    return out


def parse_hits(text: str) -> list[str]:
    """`file:line` entries from the tracer's output (or the part of stderr after MARKER)."""
    if MARKER in text:
        text = text.split(MARKER, 1)[1]
    return sorted({ln.strip() for ln in text.splitlines() if ":" in ln and ln.strip().rsplit(":", 1)[-1].isdigit()})


def summarize(found: list[dict], recordings: dict[str, list[dict]]) -> list[dict]:
    """Each change with the users who reached it in base (old code) or head (new code)."""
    out = []
    for c in found:
        who: dict[str, list[str]] = {"base": [], "head": []}
        for world, key in (("base", "base_lines"), ("head", "head_lines")):
            want = {f"{c['file']}:{n}" for n in c[key]}
            for r in recordings.get(world, []):
                if want & set(r.get("reached", [])):
                    who[world].append(r["persona"])
        out.append({**c, "reached_by": sorted(set(who["base"]) | set(who["head"])),
                    "reached_in": [w for w in ("base", "head") if who[w]]})
    return out


def reach_line(summary: list[dict] | None) -> str:
    """One line for the PR comment: how much of the changed code the users ran."""
    if not summary:
        return ""
    hit = [c for c in summary if c["reached_by"]]
    line = f"Changed code reached by the users: {len(hit)} of {len(summary)} change(s)"
    missed = [f"`{c['where']}` ({c['file']})" for c in summary if not c["reached_by"]]
    if missed:
        line += f". Not reached by any user: {', '.join(missed[:5])}" + (" …" if len(missed) > 5 else "")
    return line + "."


TRACER = r'''
"""Doppel's line tracer: records the first time each watched line runs. Remove nothing, change nothing."""
import json as _json
import os as _os
import sys as _sys
import threading as _threading


def _start():
    path, out = _os.environ.get("DOPPEL_TRACE_TARGETS"), _os.environ.get("DOPPEL_TRACE_OUT")
    if not path or not out or not _os.path.exists(path):
        return
    root = _os.path.abspath(_os.environ.get("DOPPEL_TRACE_ROOT") or _os.getcwd())
    with open(path, encoding="utf-8") as f:
        wanted = {_os.path.normcase(_os.path.join(root, rel)): set(lines) for rel, lines in _json.load(f).items()}
    rel_of = {full: _os.path.relpath(full, root).replace(_os.sep, "/") for full in wanted}
    seen, cache = set(), {}

    def local(frame, event, arg):
        if event == "line":
            key = (frame.f_code.co_filename, frame.f_lineno)
            if key not in seen:
                full = cache.get(frame.f_code.co_filename)
                if full and frame.f_lineno in wanted[full]:
                    seen.add(key)
                    try:
                        fd = _os.open(out, _os.O_WRONLY | _os.O_CREAT | _os.O_APPEND, 0o644)
                        _os.write(fd, ("%s:%d\n" % (rel_of[full], frame.f_lineno)).encode())
                        _os.close(fd)
                    except OSError:
                        pass
        return local

    def call(frame, event, arg):
        name = frame.f_code.co_filename
        if name not in cache:
            full = _os.path.normcase(_os.path.abspath(name))
            cache[name] = full if full in wanted else None
        return local if cache[name] else None

    _sys.settrace(call)
    _threading.settrace(call)


try:
    _start()
except Exception:  # a tracer must never break the app
    pass

# keep any sitecustomize the image already had
try:
    import importlib.machinery as _m
    _here = _os.path.dirname(_os.path.abspath(__file__))
    _spec = _m.PathFinder.find_spec("sitecustomize", [p for p in _sys.path if _os.path.abspath(p or ".") != _here])
    if _spec and _spec.origin and _spec.origin != __file__:
        exec(compile(open(_spec.origin, encoding="utf-8").read(), _spec.origin, "exec"), {"__name__": "sitecustomize"})
except Exception:
    pass
'''
