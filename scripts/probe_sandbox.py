"""Probe Nebius Token Factory Sandboxes (ConTree) before designing Fork around them.

No Token Factory model calls. Measures:
  1. your account's sandbox permissions and limits
  2. how long a checkpoint takes (non-disposable run, apply_files)
  3. how long a disposable pytest run from a checkpoint takes
  4. whether a deep chain of checkpoints slows runs down, and whether you can run
     from the middle of the chain (branch from any point = rollback)
  5. whether state written in a child checkpoint persists there and not in its parent
  6. whether the sandbox has outbound internet (pip install from PyPI)
  7. how many runs really execute at once (parallel fan-out)
  8. what runs cost (the API reports a cost per run)

Usage, from the repo root with NEBIUS_API_KEY and NEBIUS_PROJECT_ID in .env or the environment:
    python scripts/probe_sandbox.py
    python scripts/probe_sandbox.py --fanout 24 --depth 20
Prints a summary and writes logs/sandbox_probe.json. Takes about 2-4 minutes.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

CALC = b"def add(a, b):\n    return a + b\n"
TEST = b"from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"


class Probe:
    def __init__(self):
        self.report: dict = {"steps": {}, "errors": {}, "costs": []}

    async def timed(self, awaitable):
        """Await a ConTree operation; return (image, wall seconds, server seconds)."""
        t = time.perf_counter()
        img = await awaitable
        wall = round(time.perf_counter() - t, 2)
        server = None
        try:
            server = round(img.elapsed.total_seconds(), 2)
            self.report["costs"].append(img.result.cost)
        except Exception:
            pass  # images.use() results have no run result
        return img, wall, server

    def record(self, name: str, **data):
        self.report["steps"][name] = data
        print(f"  {name:30s} " + "  ".join(f"{k}={v}" for k, v in data.items()), flush=True)

    async def step(self, name: str, fn):
        try:
            return await fn()
        except Exception as e:  # one failing probe must not hide the others
            self.report["errors"][name] = f"{type(e).__name__}: {e}"
            print(f"  {name:30s} ERROR {type(e).__name__}: {e}", flush=True)
            return None


def text(img) -> str:
    return ((img.stdout or "") + (img.stderr or "")).strip()


async def run_probe(client, image: str, depth: int, fanout: int) -> dict:
    p = Probe()
    st: dict = {}

    async def account():
        info = await client.get_token_info()
        p.report["permissions"], p.report["limits"] = info.permissions, info.limits
        p.record("account", limits=info.limits, permissions=info.permissions)

    async def base_and_checkpoints():
        t = time.perf_counter()
        st["base"] = await client.images.use(image)
        p.record("use_base_image", image=image, wall_s=round(time.perf_counter() - t, 2))
        env, wall, server = await p.timed(st["base"].run(shell="pip install -q pytest", disposable=False))
        p.record("checkpoint_pip_install", wall_s=wall, server_s=server, exit=env.exit_code)
        st["repo"], wall, server = await p.timed(env.apply_files({"app/calc.py": CALC, "app/test_calc.py": TEST}))
        p.record("checkpoint_apply_files", wall_s=wall, server_s=server, uuid=str(st["repo"].uuid))

    async def disposable_tests():
        walls, servers, last = [], [], None
        for _ in range(3):
            last, wall, server = await p.timed(st["repo"].run(shell="cd /app && python -m pytest -q", timeout=120))
            walls.append(wall)
            servers.append(server or 0)
        p.record("disposable_pytest_x3", median_wall_s=statistics.median(walls),
                 median_server_s=statistics.median(servers), exit=last.exit_code,
                 last_line=(text(last).splitlines() or [""])[-1])

    async def chain_and_rollback():
        chain, walls = [st["repo"]], []
        for i in range(depth):  # chain[k] has step.txt == k-1 for k >= 1
            nxt, wall, _ = await p.timed(chain[-1].apply_files({"app/step.txt": f"{i}\n".encode()}))
            chain.append(nxt)
            walls.append(wall)
        mid = max(1, depth // 2)
        deep, wall_deep, _ = await p.timed(chain[-1].run(shell="cat /app/step.txt"))
        middle, wall_mid, _ = await p.timed(chain[mid].run(shell="cat /app/step.txt"))
        p.record("checkpoint_chain", depth=depth, median_wall_s=statistics.median(walls))
        p.record("run_from_any_checkpoint",
                 deepest=text(deep), expected_deepest=depth - 1, wall_deepest_s=wall_deep,
                 middle=text(middle), expected_middle=mid - 1, wall_middle_s=wall_mid)

    async def state_isolation():
        child, _, _ = await p.timed(st["repo"].run(shell="echo hello > /opt/marker", disposable=False))
        in_child, _, _ = await p.timed(child.run(shell="cat /opt/marker"))
        in_parent, _, _ = await p.timed(st["repo"].run(shell="cat /opt/marker"))
        p.record("state_persists_in_child_only",
                 child_has_it=in_child.exit_code == 0, parent_has_it=in_parent.exit_code == 0)

    async def network():
        pip, wall, _ = await p.timed(st["repo"].run(
            shell="pip install -q six && python -c 'import six; print(six.__version__)'",
            disposable=False, timeout=120))
        p.record("outbound_internet_pip", online=pip.exit_code == 0, wall_s=wall, detail=text(pip)[-120:])

    async def fan_out():
        async def one(i):
            _, wall, _ = await p.timed(st["repo"].run(shell=f"sleep 3; echo {i}", timeout=60))
            return wall

        single = await one(-1)
        t = time.perf_counter()
        await asyncio.gather(*(one(i) for i in range(fanout)))
        total = round(time.perf_counter() - t, 2)
        p.record("parallel_disposable_runs", n=fanout, single_wall_s=single, total_wall_s=total,
                 effective_parallelism=round(fanout * single / total, 1) if total else None)
        t = time.perf_counter()
        forks = await asyncio.gather(*(st["repo"].apply_files({"app/branch.txt": f"b{i}\n".encode()})
                                       for i in range(fanout)))
        p.record("parallel_branch_checkpoints", n=fanout, distinct_uuids=len({str(f.uuid) for f in forks}),
                 total_wall_s=round(time.perf_counter() - t, 2))

    async def dedup():
        a = await st["repo"].run(shell="echo same > /app/same.txt", disposable=False)
        b = await st["repo"].run(shell="echo same > /app/same.txt", disposable=False)
        p.record("same_command_same_uuid", same=str(a.uuid) == str(b.uuid))

    print("Probing Nebius Sandboxes ...", flush=True)
    await p.step("account", account)
    if await p.step("base_and_checkpoints", base_and_checkpoints) is None and "repo" not in st:
        print("Can't build a checkpoint, stopping. Check NEBIUS_API_KEY / NEBIUS_PROJECT_ID.")
        return p.report
    for name, fn in (("disposable_tests", disposable_tests), ("chain_and_rollback", chain_and_rollback),
                     ("state_isolation", state_isolation), ("network", network),
                     ("fan_out", fan_out), ("dedup", dedup)):
        await p.step(name, fn)
    costs = [c for c in p.report["costs"] if isinstance(c, (int, float))]
    p.report["cost_runs"], p.report["cost_sum"] = len(costs), sum(costs)
    print(f"\nRuns with a reported cost: {len(costs)}, sum of reported cost: {sum(costs)}")
    return p.report


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", default="python:3.12-slim")
    ap.add_argument("--depth", type=int, default=10, help="length of the checkpoint chain")
    ap.add_argument("--fanout", type=int, default=12, help="parallel runs to start at once")
    a = ap.parse_args()

    from contree_sdk import Contree
    from contree_sdk.config import ContreeConfig

    # Poll often so timings measure the sandbox, not the SDK's polling backoff (max 10 s by default).
    client = Contree(config=ContreeConfig(operation_poll_secs_min=0.05, operation_poll_secs_max=1.0))
    report = asyncio.run(run_probe(client, a.image, a.depth, a.fanout))
    out = Path("logs") / "sandbox_probe.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
