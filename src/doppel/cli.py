"""doppel: run the same personas against the base and head versions of an app, report what changed.

    doppel run --base examples/shop/base --head examples/shop/head \\
               --personas examples/shop/personas.json --out report.md
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from doppel import __version__
from doppel.diff import compare
from doppel.report import markdown
from doppel.spec import TwinSpec

load_dotenv()


def cmd_run(a) -> int:
    spec = load_spec(a.base, a.head, a.spec)
    personas = json.loads(Path(a.personas).read_text(encoding="utf-8"))

    if a.backend == "local":
        from doppel.backends.local import run_twin
    else:
        from doppel.backends.nebius import run_twin

    t0 = time.perf_counter()
    sus, checks = [], {}
    if a.guided:
        if not a.pr:
            raise ValueError("--guided needs --pr (the reviewer reads the PR description)")
        from doppel.personas import code_diff, generate, read_source
        from doppel.suspects import suspect
        sus = suspect(Path(a.pr).read_text(encoding="utf-8"), code_diff(a.base, a.head),
                      read_source(a.base))["suspicions"]
        print(f"[review] {len(sus)} suspicion(s): " + "; ".join(x["claim"] for x in sus), file=sys.stderr)
        if sus:
            personas = personas + generate(a.base, head_dir=a.head, guide=spec.persona_guide, suspicions=sus,
                                           probes_only=True)["personas"]
    refine, repaired = None, []
    if a.rehearse:
        from doppel.rehearsal import repair

        def refine(ps, recordings):
            fixed = repair(ps, recordings, a.base, guide=spec.persona_guide, login=spec.login)
            repaired.extend(fixed["repaired"])
            print(f"[rehearsal] fixed {len(fixed['repaired'])} persona(s) that got nowhere", file=sys.stderr)
            return fixed["personas"]
    from doppel import reach
    found = reach.changes(a.base, a.head)
    worlds = run_twin(a.base, a.head, spec, personas, refine=refine, watch=reach.targets(found) if found else None)
    personas = worlds.get("personas", personas)
    for f in worlds.get("failed", []):
        print(f"[twin] persona '{f['persona']}' failed: {f['error']}", file=sys.stderr)
    from doppel.rehearsal import coverage
    cov = {**coverage(worlds["base"]), "repaired": repaired}
    touched = setup_files_changed(a.base, a.head, a.personas)
    if touched:
        cov["setup_note"] = (f"⚠️ This PR edits Doppel's own setup ({', '.join(touched)}). The comparison settings "
                             "([diff]) come from the base branch, so the PR can't hide its own changes; "
                             "review these edits by hand.")
    reached = reach.summarize(found, worlds) if found else []
    if reached:
        cov["reach"] = reached
    elif any(p.endswith(".py") for p in _changed(a.base, a.head)):
        cov["reach_note"] = ("The changed code runs when the app loads (module or class level), so which users "
                             "reached it can't be measured line by line.")
    findings = compare(worlds["base"], worlds["head"], spec.ignore, spec.slow_ratio, spec.slow_min_ms)
    # a persona that ran on base but broke on head (the app no longer starts, the runner crashed) is the
    # loudest regression there is, not a persona to drop: report it as an error finding
    from doppel.diff import Finding
    broke = [f for f in worlds.get("failed", []) if f.get("world") == "head"]
    findings = [Finding("error", f["persona"], 0, "(whole persona)", "ran on base, failed on head",
                        "ran", f["error"][:200]) for f in broke] + findings
    if not worlds["base"] and not broke:
        raise RuntimeError(f"no persona replayed in either world ({len(worlds.get('failed', []))} failed): "
                           "the twin tested nothing, so this is not a pass")
    steps = sum(len(r["steps"]) for r in worlds["base"])
    judged = None
    if a.pr and findings:
        from doppel.judge import judge, regression_tests
        from doppel.personas import code_diff
        judged = judge(findings, Path(a.pr).read_text(encoding="utf-8"), code_diff(a.base, a.head))
        if a.tests_out:
            Path(a.tests_out).write_text(regression_tests(findings, judged["verdicts"], personas,
                                                          Path(a.tests_out).name), encoding="utf-8")
    if sus:
        from doppel.suspects import check
        checks = check(sus, findings, judged["verdicts"] if judged else None, personas,
                       worlds["base"], worlds["head"])["checks"]
        if judged:
            from doppel.judge import escalate
            judged["verdicts"] = escalate(judged["verdicts"], checks)
    from doppel.report import settle_suspicions
    settled = settle_suspicions([{**x, **checks.get(x["id"], {})} for x in sus], findings,
                                judged["verdicts"] if judged else None) if sus else []
    md = markdown(findings, len(worlds["base"]), steps, judged, cov, settled)
    print(md)
    print(f"({time.perf_counter() - t0:.1f} s)", file=sys.stderr)
    if a.out:
        Path(a.out).write_text(md, encoding="utf-8")
    if a.json:
        verdicts = [v.__dict__ for v in judged["verdicts"]] if judged else None
        pr_text = Path(a.pr).read_text(encoding="utf-8") if a.pr else ""
        Path(a.json).write_text(json.dumps({"findings": [f.to_dict() for f in findings], "verdicts": verdicts,
                                            "coverage": cov, "personas": personas, "worlds": worlds,
                                            "changes": reached, "suspicions": settled,
                                            "pr": {"title": pr_text.strip().splitlines()[0].lstrip("# ").strip()
                                                   if pr_text.strip() else "", "body": pr_text},
                                            "app": Path(a.head).resolve().name},
                                           indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    if a.strict:
        regressions = [v for v in judged["verdicts"] if v.label == "regression"] if judged else findings
        return 1 if regressions else 0
    return 0


def cmd_tests(a) -> int:
    """Test mode: compare what the app's own tests see on base and head (doppel.traffic)."""
    from doppel import traffic
    from doppel.sandbox import InfraError
    t0 = time.perf_counter()
    try:
        result = traffic.check(a.base, a.head, backend=a.backend, test=a.command, install=a.install,
                               log=lambda m: print(m, file=sys.stderr), python=a.python)
    except (traffic.NotComparable, InfraError) as e:
        md = traffic.not_comparable(str(e))
        print(md)
        if a.out:
            Path(a.out).write_text(md, encoding="utf-8")
        return 2
    findings, judged = result["findings"], None
    if a.pr and findings:
        from doppel.judge import judge
        from doppel.personas import code_diff
        judged = judge(findings, Path(a.pr).read_text(encoding="utf-8"), code_diff(a.base, a.head))
    md = traffic.report(result, judged)
    print(md)
    print(f"({time.perf_counter() - t0:.1f} s)", file=sys.stderr)
    if a.out:
        Path(a.out).write_text(md, encoding="utf-8")
    if a.json:
        verdicts = [v.__dict__ for v in judged["verdicts"]] if judged else None
        Path(a.json).write_text(json.dumps({"mode": "tests", "findings": [f.to_dict() for f in findings],
                                            "verdicts": verdicts, **{k: result[k] for k in (
                                                "stats", "excluded", "flaky", "unstable", "noise_keys", "test",
                                                "worlds")}}, indent=1, default=str), encoding="utf-8")
    if a.strict:
        regressions = [v for v in judged["verdicts"] if v.label == "regression"] if judged else findings
        return 1 if regressions else 0
    return 0


SETUP_FILES = ("doppel.toml", "doppel_seed.py", "doppel-personas.json")


def load_spec(base, head, explicit: str | None = None) -> TwinSpec:
    """The twin spec for a run. An explicit --spec is used as given. Otherwise [app] (install, start,
    migrate) comes from the PR, which may legitimately add a dependency or a migration, but [diff]
    (ignored keys, slowness thresholds) comes from the base branch: a PR that adds "total" to
    [diff] ignore must not be able to hide its own change to totals."""
    if explicit:
        return TwinSpec.load(explicit)
    head_toml, base_toml = Path(head) / "doppel.toml", Path(base) / "doppel.toml"
    spec = TwinSpec.load(head_toml if head_toml.exists() else base_toml)
    if base_toml.exists() and head_toml.exists():
        trusted = TwinSpec.load(base_toml)
        spec.ignore, spec.slow_ratio, spec.slow_min_ms = trusted.ignore, trusted.slow_ratio, trusted.slow_min_ms
    return spec


def setup_files_changed(base, head, personas: str | None = None) -> list[str]:
    """Doppel's own setup files (spec, seed, personas) that the PR adds, removes or edits."""
    names = set(SETUP_FILES) | ({Path(personas).name} if personas else set())
    return [rel for rel in _changed(base, head) if Path(rel).name in names]


def _changed(base, head) -> list[str]:
    from doppel.backends.nebius import changed_files
    try:
        return changed_files(base, head)
    except OSError:
        return []


def cmd_personas(a) -> int:
    from doppel.personas import generate
    spec_path = Path(a.head or a.app) / "doppel.toml"
    guide = TwinSpec.load(spec_path).persona_guide if spec_path.exists() else ""
    out = generate(a.app, n=a.n, head_dir=a.head, model=a.model, guide=guide)
    Path(a.out).write_text(json.dumps(out["personas"], indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(out['personas'])} personas -> {a.out} | {out['model']} | "
          f"{out['tokens'][0]}+{out['tokens'][1]} tokens | ${out['usd']:.4f}"
          + (f" | dropped {out['dropped']} malformed" if out["dropped"] else ""))
    for p in out["personas"]:
        print(f"  - {p['name']}: {p.get('goal', '')} ({len(p['steps'])} steps)")
    return 0


def cmd_check(a) -> int:
    from doppel.bootstrap import boot_report, calibration_report, check, checks_report
    result = check(a.app, backend=a.backend, calibrate=a.calibrate)
    if result.get("checks"):
        print("Checks before booting (free, no sandbox):")
        print(checks_report(result["checks"]))
    if "smoke_steps" in result or result.get("error", "").startswith(("RuntimeError", "the login", "the twin")):
        print("Booting the twin:")
        print(boot_report(result))
    if result.get("seed_log"):
        print("What the seed printed, and the databases after it:\n" + result["seed_log"])
    if result.get("ok") and result.get("coverage") is not None:
        print(calibration_report(result))
    if not result.get("ok"):
        print(f"doppel init --check: {result.get('error', '').splitlines()[0]}", file=sys.stderr)
        return 1
    print("The setup works." + ("" if a.calibrate else " (Add --calibrate to replay invented users too.)"))
    return 0


def cmd_init(a) -> int:
    from doppel.bootstrap import TRACE_DIR, init, write
    if a.check:
        return cmd_check(a)
    trace = Path(a.app) / TRACE_DIR if a.trace else None
    result = init(a.app, backend=a.backend, tries=a.tries, personas=a.n, min_coverage=a.min_coverage,
                  agent=not a.no_agent,
                  resume=not a.fresh, trace=trace)
    if trace:
        print(f"trace of every attempt: {trace}", file=sys.stderr)
    if not result["ok"]:
        if result.get("infra"):
            print("doppel init: the sandbox API timed out or dropped the connection, so the setup was never "
                  "tried. A slow upload is the usual cause: raise DOPPEL_HTTP_TIMEOUT (seconds, default 180) "
                  f"and run it again.\n{result['error']}", file=sys.stderr)
        else:
            print(f"doppel init: no working setup after {result.get('attempts', a.tries)} attempt(s). "
                  f"Last error:\n{result['error']}", file=sys.stderr)
        if a.keep_draft:
            for path in write(Path(a.app), result.get("files", {}), force=a.force):
                print(f"  draft kept: {path}")
        return 2
    for path in write(Path(a.app), result["files"], force=a.force):
        print(f"wrote {path}")
    print(f"attempts: {result['attempts']} | users replayed: {result['personas']} | "
          f"requests served: {result['coverage']:.0%}")
    print(f"ignoring noisy keys: {', '.join(result['noise']) or '(none found)'}")
    if result["flaky"]:
        print("warning: these requests changed status between two identical runs:")
        for line in result["flaky"]:
            print(f"  - {line}")
    if result["notes"]:
        print(f"model notes: {result['notes']}")
    print("Review both files before committing: Doppel trusts this setup on every pull request.")
    return 0


def cmd_view(a) -> int:
    from doppel.viewer import is_run, is_trace, write_run_view, write_view
    path = Path(a.path)
    if is_run(path):
        out = write_run_view(path, Path(a.out) if a.out else None)
    elif is_trace(path):
        out = write_view(path, Path(a.out) if a.out else None)
    else:
        print(f"doppel view: {path} is neither a run (run.json from `doppel run --json`, or a folder holding "
              "one) nor an init trace (a folder with attempt-N folders from `doppel init --trace`).",
              file=sys.stderr)
        return 2
    print(f"wrote {out}")
    if not a.no_open:
        import webbrowser
        webbrowser.open(out.resolve().as_uri())
    return 0


def cmd_site(a) -> int:
    from doppel.site import build
    files = build(Path(a.gallery), Path(a.out))
    print(f"wrote {len(files)} pages to {a.out} (open {Path(a.out) / 'index.html'})")
    return 0


def cmd_comment(a) -> int:
    import os

    from doppel.github import pr_number_from_event, upsert_comment
    body = Path(a.report).read_text(encoding="utf-8")
    if a.footer:
        body += f"\n{a.footer}\n"
    repo, token = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"]
    pr = a.pr_number or pr_number_from_event()
    print(f"PR #{pr}: comment {upsert_comment(body, repo, pr, token)}")
    return 0


def main(argv=None) -> int:
    try:  # the report has emoji; a redirected Windows console would otherwise crash on them
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(prog="doppel", description="A twin of your app that tries every pull request first.")
    ap.add_argument("--version", action="version", version=f"doppel {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="replay personas against base and head, report behavior changes")
    r.add_argument("--base", required=True, help="folder with the base (main branch) code")
    r.add_argument("--head", required=True, help="folder with the head (pull request) code")
    r.add_argument("--personas", required=True, help="JSON file with personas")
    r.add_argument("--spec", help="twin spec (default: <head>/doppel.toml)")
    r.add_argument("--backend", choices=["local", "nebius"], default="local")
    r.add_argument("--out", help="write the Markdown report here")
    r.add_argument("--json", help="write findings and raw recordings here")
    r.add_argument("--pr", help="PR description file: Nemotron Ultra judges each change against it")
    r.add_argument("--tests-out", help="write a pytest file with one test per regression")
    r.add_argument("--guided", action="store_true",
                   help="an AI reviewer lists suspected regressions; probe personas prove or refute each one")
    r.add_argument("--rehearse", action="store_true",
                   help="replay personas on base first and have Nemotron fix the ones stuck at login or 404")
    r.add_argument("--strict", action="store_true",
                   help="exit 1 on regressions (or on any change when there is no --pr), for CI")
    g = sub.add_parser("personas", help="have Nemotron invent personas for an app")
    g.add_argument("--app", required=True, help="folder with the app's (base) code")
    g.add_argument("--head", help="pull request code: about half the personas will target what changed")
    g.add_argument("-n", type=int, default=12, help="how many personas")
    g.add_argument("--model", help="default: $PERSONA_MODEL or nvidia/Nemotron-3_5-Lightning")
    g.add_argument("--out", default="personas.json")
    i = sub.add_parser("init", help="write doppel.toml and doppel_seed.py for an app, checked in the twin")
    i.add_argument("app", nargs="?", default=".", help="the app's folder (default: current folder)")
    i.add_argument("--backend", choices=["local", "nebius"], default="nebius",
                   help="where to try the setup (local uses your current Python environment)")
    i.add_argument("--tries", type=int, default=2,
                   help="drafts to try before the setup agent takes over (default 2)")
    i.add_argument("--no-agent", action="store_true",
                   help="stop after the drafts instead of handing over to the setup agent")
    i.add_argument("-n", type=int, default=6, help="invented users for the noise calibration")
    i.add_argument("--min-coverage", type=float, default=0.4,
                   help="share of the invented users' requests that must be served (default 0.4)")
    i.add_argument("--fresh", action="store_true", help="ignore progress saved by an earlier run")
    i.add_argument("--trace", action="store_true",
                   help="save every attempt (prompt, reply, checks, boot, evidence) to .doppel-trace/")
    i.add_argument("--check", action="store_true",
                   help="test the doppel.toml and doppel_seed.py already in the app, without drafting")
    i.add_argument("--calibrate", action="store_true",
                   help="with --check: also replay invented users (costs model calls and a second boot)")
    i.add_argument("--force", action="store_true", help="overwrite existing doppel.toml / doppel_seed.py")
    i.add_argument("--keep-draft", action="store_true", help="write the last draft even if it failed")
    v = sub.add_parser("view", help="turn a run (run.json) or an init trace into a web page and open it")
    v.add_argument("path", nargs="?", default=".doppel-trace",
                   help="run.json, a folder holding it, or an init trace folder (default: .doppel-trace)")
    v.add_argument("--out", help="where to write the page (default: doppel-view.html next to the trace)")
    v.add_argument("--no-open", action="store_true", help="write the page without opening a browser")
    st = sub.add_parser("site", help="build the static replay site from recorded runs (for GitHub Pages)")
    st.add_argument("--gallery", default="site/gallery.json", help="which runs to publish (default: site/gallery.json)")
    st.add_argument("--out", default="_site", help="output folder (default: _site)")
    t = sub.add_parser("tests", help="test mode: run the app's own tests on base and head and compare every "
                                     "response they saw (no doppel.toml needed)")
    t.add_argument("--base", required=True, help="the code before the change")
    t.add_argument("--head", required=True, help="the code after the change")
    t.add_argument("--backend", choices=["local", "nebius"], default="nebius")
    t.add_argument("--command", default=None, help="test command (default: read from tox.ini / CI / the layout)")
    t.add_argument("--install", default=None, help="install command (default: read from the repo)")
    t.add_argument("--pr", help="PR title and description (Markdown) for the judge")
    t.add_argument("--out", help="write the Markdown report here")
    t.add_argument("--json", help="write the findings and verdicts here")
    t.add_argument("--strict", action="store_true", help="exit 1 when there are regressions")
    t.add_argument("--python", default="", help="Python for the tests: a version (3.8) picks the sandbox image; "
                                                  "with --backend local, a path to an interpreter (its venv)")
    pb = sub.add_parser("probe", help="install and import an app, print what is true about it (routes, database, "
                                      "login, models, import errors)")
    pb.add_argument("app", nargs="?", default=".")
    pb.add_argument("--backend", choices=["local", "nebius"], default="nebius",
                    help="where to install and import it (local uses your current Python environment)")
    pb.add_argument("--install", default=None, help="install command (default: derived from the app's requirements)")
    pb.add_argument("--json", action="store_true", help="print the raw facts")
    c = sub.add_parser("comment", help="post or update the report as a PR comment (in GitHub Actions)")
    c.add_argument("--report", required=True)
    c.add_argument("--pr-number", type=int, help="default: from the GitHub event")
    c.add_argument("--footer", default="", help="extra Markdown under the report (e.g. a link to the run)")
    a = ap.parse_args(argv)
    if a.cmd == "comment":
        return cmd_comment(a)
    try:
        if a.cmd == "personas":
            return cmd_personas(a)
        if a.cmd == "init":
            return cmd_init(a)
        if a.cmd == "view":
            return cmd_view(a)
        if a.cmd == "site":
            return cmd_site(a)
        if a.cmd == "tests":
            return cmd_tests(a)
        if a.cmd == "probe":
            from doppel.probe import main as probe_main
            args = [a.app, "--backend", a.backend] + (["--install", a.install] if a.install is not None else []) \
                + (["--json"] if a.json else [])
            return probe_main(args)
        return cmd_run(a)
    except Exception as e:  # noqa: BLE001 - exit 2 = Doppel itself failed (1 means regressions found)
        print(f"doppel: error: {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
