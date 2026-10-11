"""The setup agent: when drafted setups fail, Nemotron sets the app up the way a developer would, in a live
sandbox, instead of guessing again from source.

Each turn the model answers with ONE JSON action:

    {"tool": "shell", "command": "..."}           run in the repo root; files persist, processes don't
    {"tool": "read", "path": "app/settings.py"}   a repo file, or a folder listing
    {"tool": "serve", "start": "...", "ready": "/", "env": {...}, "steps": [...]}
                                                  start the app on the current sandbox state, send HTTP
                                                  steps, get each status and reply plus the app's log
    {"tool": "try", "files": {"doppel.toml": "...", "doppel_seed.json": "...", "doppel_seed.py": "..."}}
                                                  the real check: a clean twin booted with these files;
                                                  success ends the run
    {"tool": "give_up", "reason": "..."}

Bounded: MAX_STEPS turns and MAX_TRIES `try` calls. The sandbox is the same kind Doppel tests in: a Nebius
sandbox (or, with the local backend, a throwaway copy of the repo in your current Python environment).
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

MAX_STEPS, MAX_TRIES = 20, 4
OUT_CHARS, OLD_OUT_CHARS, KEEP_FULL = 4000, 500, 4
READ_STREAK_NUDGE = 4      # this many reads in a row: remind it to test something
ALIASES = {"run": "shell", "bash": "shell", "sh": "shell", "exec": "shell", "command": "shell", "execute": "shell",
           "cat": "read", "open": "read", "view": "read", "read_file": "read",
           "request": "serve", "http": "serve", "start": "serve",
           "test": "try", "check": "try", "verify": "try", "submit": "try"}
MODEL_ENV = "AGENT_MODEL"


# ---------------------------------------------------------------- workspaces

def _tail(text: str, n: int = OUT_CHARS) -> str:
    text = text or ""
    return text if len(text) <= n else "...(cut)...\n" + text[-n:]


class LocalWorkspace:
    """A throwaway copy of the repo; commands run in your current Python environment (like the local backend)."""

    def __init__(self, repo: Path, env: dict | None = None):
        self.tmp = Path(tempfile.mkdtemp(prefix="doppel-agent-"))
        self.dir = self.tmp / "app"
        from doppel.bootstrap import SKIP_PARTS
        shutil.copytree(repo, self.dir, ignore=shutil.ignore_patterns(*SKIP_PARTS))
        self.env = {**os.environ, **(env or {}), "PORT": "8000"}

    def shell(self, command: str, timeout: int = 300) -> str:
        log = self.tmp / "shell.log"
        with open(log, "w", encoding="utf-8", errors="replace") as out:  # a file, not a pipe: a background
            kw = {"start_new_session": True} if os.name != "nt" else {     # process can't hold it open
                "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
            p = subprocess.Popen(command, shell=True, cwd=self.dir, env=self.env, stdout=out,
                                 stderr=subprocess.STDOUT, **kw)
            try:
                rc = p.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                rc = "timeout"
            finally:
                _kill_tree(p)
        return f"exit {rc}\n" + _tail(log.read_text("utf-8", errors="replace"))

    def serve(self, start: str, ready: str, steps: list, env: dict | None = None) -> dict:
        from doppel.backends.local import _serve_and_replay
        from doppel.spec import TwinSpec
        fork = self.tmp / "serve"
        shutil.rmtree(fork, ignore_errors=True)
        shutil.copytree(self.dir, fork)
        try:
            return _serve_and_replay(fork, TwinSpec(start=start, ready=ready, env=env or {}),
                                     [{"name": "agent", "steps": steps}])[0]
        finally:
            shutil.rmtree(fork, ignore_errors=True)

    def close(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)


def _kill_tree(p: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
        else:
            os.killpg(p.pid, 9)
    except (OSError, ProcessLookupError):
        pass


class NebiusWorkspace:
    """A Nebius sandbox with the repo uploaded and its dependencies installed. Every shell command is a
    non-disposable run, so its file changes become the next command's starting point."""

    def __init__(self, repo: Path, install: str = "", image: str = "", env: dict | None = None):
        from doppel.probe import image_for
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.env = dict(env or {})
        self.img = self._wait(self._open(Path(repo).resolve(), install, image or image_for(repo)))

    def _wait(self, coro, timeout: float = 1800):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def _exports(self, env: dict | None = None) -> str:
        import shlex
        return "".join(f"export {k}={shlex.quote(str(v))}; " for k, v in {**self.env, **(env or {})}.items())

    async def _open(self, repo: Path, install: str, image: str):
        from doppel import sandbox
        from doppel.backends.nebius import code_files, make_client
        self.client = make_client()
        img = await self.client.images.use(image)
        img = await sandbox.upload(img, code_files(repo), "uploading the code")
        if install:
            out = await img.run(shell=f"cd /app && {self._exports()}{install}", disposable=False, timeout=1800)
            self.install_log = f"exit {out.exit_code}\n" + _tail((out.stdout or "") + (out.stderr or ""), 1500)
            return out
        self.install_log = "(no install command)"
        return img

    def shell(self, command: str, timeout: int = 300) -> str:
        from doppel.backends.nebius import OUTPUT_LIMIT

        async def go():
            return await self.img.run(shell=f"cd /app && export PORT=8000; {self._exports()}{command}",
                                      disposable=False, timeout=timeout, truncate_output_at=OUTPUT_LIMIT)
        out = self._wait(go())
        self.img = out
        return f"exit {out.exit_code}\n" + _tail((out.stdout or "") + (out.stderr or ""))

    def serve(self, start: str, ready: str, steps: list, env: dict | None = None) -> dict:
        from doppel.backends.nebius import OUTPUT_LIMIT, RUNNER, persona_command
        from doppel.runner import APP_LOG_MARKER, attach_app_log
        from doppel.spec import TwinSpec
        spec = TwinSpec(start=start, ready=ready, env={**self.env, **(env or {})})
        files = {"doppel/runner.py": RUNNER.read_bytes(),
                 "doppel/persona.json": json.dumps([{"name": "agent", "steps": steps}]).encode()}

        async def go():
            return await self.img.run(shell=persona_command(spec), files=files, disposable=True, timeout=600,
                                      truncate_output_at=OUTPUT_LIMIT)
        out = self._wait(go())
        text = (out.stdout or "").strip()
        try:
            rec = json.loads(text.splitlines()[-1])[0]
        except (ValueError, IndexError, KeyError):
            raise RuntimeError("the app did not start:\n" + _tail((out.stdout or "") + (out.stderr or ""))) from None
        parts = (out.stderr or "").split(APP_LOG_MARKER)
        rec = attach_app_log(rec, parts[1] if len(parts) >= 3 else "")
        if "app_log" not in rec and len(parts) >= 3:
            rec["app_log"] = parts[1].strip()[-3000:]
        return rec

    def close(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)


def open_workspace(repo: Path, backend: str, install: str = "", env: dict | None = None):
    return LocalWorkspace(repo, env) if backend == "local" else NebiusWorkspace(repo, install, env=env)


# ---------------------------------------------------------------- the agent

PROMPT = """You are setting up a Python web app so that Doppel, an automated tester, can run it. Earlier
drafts of the setup files failed (see below). Instead of guessing again, you now work like a developer in a
LIVE SANDBOX: a Linux machine with the repo at the working directory and its dependencies installed. Find
out what is true, reproduce the failure, fix its cause, then prove the setup with `try`.

Answer every turn with exactly ONE JSON object, nothing else:
{{"tool": "shell", "command": "..."}}
    Runs with a POSIX shell in the repo root; $PORT is 8000. Files you create persist to later commands;
    processes do not (each command starts fresh). Use it to look around (ls, cat, python -c "import ..."),
    create the schema, run a seed script, check a database (python -c with sqlite3).
{{"tool": "read", "path": "relative/path"}}
    A repo file (or a folder listing). Paths are relative to the repo root.
{{"tool": "serve", "start": "...", "ready": "/...", "env": {{}}, "steps": [ ... ]}}
    Starts the app with `start` on a copy of the current sandbox (so create the schema and data with shell
    first), waits for `ready`, sends the steps (the doppel_seed.json step format: method, path, form or
    json, headers, auth, save), and returns every status and reply plus the app's own log. Use it to see
    what a login or sign-up route really answers.
{{"tool": "try", "files": {{"doppel.toml": "...", "doppel_seed.json": "...", "doppel_seed.py": "..."}}}}
    The real check: boots a clean twin from the repo with exactly these files (doppel_seed.json and
    doppel_seed.py only if you use them) and replays users. Success ends the job. At most {tries} tries in
    total, so check with shell and serve first.
{{"tool": "give_up", "reason": "..."}}
    Only if the app cannot run here at all (it needs a service that cannot be replaced).

How to work: start from the error below. Reproduce it (create the data with shell, then serve the failing
step) before changing anything, read only the files the evidence points at, and never read the same file
twice: everything you read stays in front of you. Prefer one experiment over more reading. You have
{steps} steps in total; make a `try` well before they run out.

The two files follow these rules (written for the first drafts; they hold here too):
----------------------------------------------------------------
{rules}
----------------------------------------------------------------
{facts}
How the dependencies were installed in this sandbox: {install}

What the earlier drafts did, oldest first:
{history}

The setup that got furthest:
{files}

Its error:
{error}

Repository setup hints:
{hints}

Repository API index (read files with the read tool for anything else):
{index}
"""


def _rules() -> str:
    from doppel.bootstrap import PROMPT as SETUP
    text = SETUP.split("Return ONLY JSON", 1)[0]
    return text.replace("{{", "{").replace("}}", "}")


def _transcript(turns: list[dict], max_steps: int = MAX_STEPS, tries_left: int = MAX_TRIES) -> str:
    out = []
    if turns:
        out.append("\nWhat you did so far in the sandbox (newest last):")
        for k, t in enumerate(turns):
            # files read stay in full (forgetting them made the agent read the same file again and again);
            # command and server output keeps only its end once it is old
            full = t.get("tool") == "read" or k >= len(turns) - KEEP_FULL
            result = t["result"] if full else _tail(t["result"], OLD_OUT_CHARS)
            out.append(f"--- step {k + 1}: {json.dumps(t['action'])[:1500]}\n{result}")
    left = max_steps - len(turns)
    status = f"\nStep {len(turns) + 1} of {max_steps}; try calls left: {tries_left}."
    streak = 0
    for t in reversed(turns):
        if t.get("tool") != "read":
            break
        streak += 1
    if streak >= READ_STREAK_NUDGE:
        status += (f" You have read {streak} files in a row: stop reading and test a hypothesis with shell or "
                   "serve, or make a try.")
    if left <= 3 and tries_left > 0:
        status += " You are almost out of steps: make a try NOW with your best files."
    return "\n".join(out) + status + "\nYour next action (one JSON object):"


def _show_recording(rec: dict) -> str:
    lines = []
    for s in rec.get("steps", []):
        lines.append(f"{s['request']} -> {s['status']}: {json.dumps(s.get('body'), default=str)[:700]}")
    if rec.get("app_log"):
        lines.append("app log (last lines):\n" + rec["app_log"][-2000:])
    return "\n".join(lines) or "(no steps were sent)"


def _read(repo: Path, rel: str) -> str:
    target = (repo / rel).resolve()
    if not target.is_relative_to(repo.resolve()) or not target.exists():
        return f"no such file or folder: {rel}"
    if target.is_dir():
        names = sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir())[:200]
        return "\n".join(names) or "(empty folder)"
    return _tail(target.read_text("utf-8", errors="replace"), 12000)


def fix(repo: Path, backend: str, brief: str, history: list[str], files: dict | None, error: str,
        try_setup, chat, log=lambda m: None, tracer=None, workspace=None, model: str | None = None,
        max_steps: int = MAX_STEPS, max_tries: int = MAX_TRIES) -> dict:
    """Run the agent. `try_setup(files)` is init's full check (pre-boot checks, then try_files). Returns
    try_setup's result for the last `try`, with "agent_steps" and "agent_tries" added."""
    from doppel.bootstrap import api_index, derived_install, gather_hints
    from doppel.personas import parse_json
    repo = Path(repo).resolve()
    model = model or os.getenv(MODEL_ENV) or os.getenv("INIT_MODEL", "nvidia/Nemotron-3-Ultra-550b-a55b")
    install = derived_install(repo)
    log("[agent] opening a live sandbox for the setup agent" + (f" (install: {install[:80]})" if install else ""))
    ws = workspace or open_workspace(repo, backend, install)
    base = PROMPT.format(
        tries=max_tries, steps=max_steps, rules=_rules(),
        facts=("Facts from the running app (Doppel imported it the way its framework does; these are true):\n"
               + brief + "\n\n") if brief else "",
        install=getattr(ws, "install_log", install or "(your current environment)"),
        history="\n".join(f"- {h}" for h in history) or "- (none)",
        files="\n".join(f"--- {k}\n{v}" for k, v in (files or {}).items()) or "(none)",
        error=_tail(error or "(none)", 6000), hints=gather_hints(repo), index=api_index(repo))
    turns: list[dict] = []
    tries = 0
    result = {"ok": False, "error": error or "the agent made no try"}
    try:
        read_before: dict[str, int] = {}
        for step in range(1, max_steps + 1):
            text, _, _ = chat(model, base + _transcript(turns, max_steps, max_tries - tries), temperature=0.3)
            action = parse_json(text) or {}
            tool = str(action.get("tool", "")).strip().lower()
            tool = ALIASES.get(tool, tool)
            if tool == "shell" and not action.get("command"):
                action["command"] = action.get("cmd") or action.get("args") or ""
            try:
                if tool == "read" and str(action.get("path", ".")).strip("./") in read_before:
                    first = read_before[str(action.get("path", ".")).strip("./")]
                    out = (f"You already read {action.get('path')} at step {first}; it is unchanged and still shown "
                           "above. Use what it says: test with shell or serve, or make a try.")
                elif tool == "shell":
                    out = ws.shell(str(action.get("command", "")))
                elif tool == "read":
                    out = _read(repo, str(action.get("path", ".")))
                    read_before[str(action.get("path", ".")).strip("./")] = step
                elif tool == "serve":
                    steps = action.get("steps") if isinstance(action.get("steps"), list) else []
                    out = _show_recording(ws.serve(str(action.get("start", "")), str(action.get("ready", "/")),
                                                   steps, action.get("env") if isinstance(action.get("env"), dict)
                                                   else None))
                elif tool == "try":
                    proposed = {k: v for k, v in (action.get("files") or {}).items() if isinstance(v, str)}
                    if "doppel.toml" not in proposed:
                        out = "try needs at least doppel.toml in files"
                    else:
                        tries += 1
                        log(f"[agent] step {step}: try {tries}/{max_tries}")
                        result = try_setup(proposed)
                        if result.get("ok"):
                            result.update(agent_steps=step, agent_tries=tries, files=result.get("files") or proposed)
                            _trace(tracer, turns + [{"action": action, "result": "WORKS"}])
                            log(f"[agent] the setup works after {step} step(s) and {tries} try(s)")
                            return result
                        out = "FAILED:\n" + _tail(result.get("error", ""), 6000)
                        if tries >= max_tries:
                            turns.append({"action": action, "result": out, "tool": tool})
                            log("[agent] out of tries")
                            break
                elif tool == "give_up":
                    log(f"[agent] gave up: {str(action.get('reason', ''))[:200]}")
                    result = {**result, "ok": False, "agent_gave_up": str(action.get("reason", ""))}
                    turns.append({"action": action, "result": "(stopped)", "tool": tool})
                    break
                else:
                    out = ('Reply with exactly one JSON object with a "tool" of shell, read, serve, try or give_up. '
                           f"Your reply started with: {text[:200]!r}")
            except Exception as e:  # noqa: BLE001 - a tool failure is information for the next turn
                out = f"the tool failed: {type(e).__name__}: {_tail(str(e), 3000)}"
            if tool != "try":
                log(f"[agent] step {step}: {tool or '?'} {_describe(action)}")
            turns.append({"action": action, "result": out, "tool": tool})
        result.update(agent_steps=len(turns), agent_tries=tries)
        return result
    finally:
        _trace(tracer, turns)
        if workspace is None:
            ws.close()


def _describe(action: dict) -> str:
    for key in ("command", "path", "start", "reason"):
        if action.get(key):
            return str(action[key]).replace("\n", " ")[:100]
    return ""


def _trace(tracer, turns: list[dict]) -> None:
    if tracer is None or not getattr(tracer, "root", None):
        return
    text = "\n\n".join(f"=== step {k + 1}\n{json.dumps(t['action'], indent=1)[:4000]}\n--- result\n{t['result']}"
                       for k, t in enumerate(turns))
    tracer.write_root("agent.txt", text or "(the agent took no steps)\n")


if __name__ == "__main__":  # pragma: no cover
    print(__doc__, file=sys.stderr)
