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
    spec_path = Path(a.spec) if a.spec else Path(a.head) / "doppel.toml"
    spec = TwinSpec.load(spec_path)
    personas = json.loads(Path(a.personas).read_text(encoding="utf-8"))

    if a.backend == "local":
        from doppel.backends.local import run_twin
    else:
        from doppel.backends.nebius import run_twin

    t0 = time.perf_counter()
    worlds = run_twin(a.base, a.head, spec, personas)
    findings = compare(worlds["base"], worlds["head"], spec.ignore, spec.slow_ratio, spec.slow_min_ms)
    steps = sum(len(r["steps"]) for r in worlds["base"])
    md = markdown(findings, len(personas), steps)
    print(md)
    print(f"({time.perf_counter() - t0:.1f} s)", file=sys.stderr)
    if a.out:
        Path(a.out).write_text(md, encoding="utf-8")
    if a.json:
        Path(a.json).write_text(json.dumps({"findings": [f.to_dict() for f in findings], "worlds": worlds},
                                           indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    return 1 if (a.strict and findings) else 0


def cmd_personas(a) -> int:
    from doppel.personas import generate
    out = generate(a.app, n=a.n, head_dir=a.head, model=a.model)
    Path(a.out).write_text(json.dumps(out["personas"], indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(out['personas'])} personas -> {a.out} | {out['model']} | "
          f"{out['tokens'][0]}+{out['tokens'][1]} tokens | ${out['usd']:.4f}"
          + (f" | dropped {out['dropped']} malformed" if out["dropped"] else ""))
    for p in out["personas"]:
        print(f"  - {p['name']}: {p.get('goal', '')} ({len(p['steps'])} steps)")
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
    r.add_argument("--strict", action="store_true", help="exit 1 if anything changed (for CI)")
    g = sub.add_parser("personas", help="have Nemotron invent personas for an app")
    g.add_argument("--app", required=True, help="folder with the app's (base) code")
    g.add_argument("--head", help="pull request code: about half the personas will target what changed")
    g.add_argument("-n", type=int, default=12, help="how many personas")
    g.add_argument("--model", help="default: $PERSONA_MODEL or nvidia/Nemotron-3_5-Lightning")
    g.add_argument("--out", default="personas.json")
    a = ap.parse_args(argv)
    if a.cmd == "personas":
        return cmd_personas(a)
    return cmd_run(a)


if __name__ == "__main__":
    sys.exit(main())
