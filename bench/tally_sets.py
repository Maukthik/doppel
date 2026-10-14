import json
from pathlib import Path

prs = json.loads(Path("bench/microblog/prs/index.json").read_text())
for name in ("main", "heldout", "consequence"):
    rows = [p for p in prs if p.get("set", "main") == name]
    reg = [p for p in rows if p["kind"] == "regression"]
    safe = [p for p in rows if p["kind"] == "safe"]
    def hit(p):
        f = Path("bench/results") / f"{p['id']}.json"
        c = json.loads(f.read_text()).get("check") if f.exists() else None
        return None if c is None else c["caught"]
    caught = [p["id"] for p in reg if hit(p)]
    alarms = [p["id"] for p in safe if hit(p)]
    print(f"{name:12} caught {len(caught)}/{len(reg)}   false alarms {len(alarms)}/{len(safe)}   "
          f"missed {[p['id'] for p in reg if hit(p) is False]}   alarms {alarms}")
