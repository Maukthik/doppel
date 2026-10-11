"""Compare what the same personas saw in the base world and in the head world."""
from __future__ import annotations

from dataclasses import asdict, dataclass

# Most severe first. "error" = head crashed or failed where base didn't.
KINDS = ("error", "status", "body", "slow")


@dataclass
class Finding:
    kind: str           # error | status | body | slow
    persona: str
    step: int
    request: str
    detail: str
    base: object = None
    head: object = None
    context: str = ""    # what led to it (what was sent, the steps before, the responses' fields): for the judge

    def to_dict(self) -> dict:
        return asdict(self)


def _fields_of(body) -> list[str]:
    """Top-level field names a client sees (a list's first item, or a page's first data item)."""
    if isinstance(body, dict) and isinstance(body.get("data"), list):   # a page: its items' fields, or none
        return list(body["data"][0]) if body["data"] and isinstance(body["data"][0], dict) else []
    if isinstance(body, list) and body and isinstance(body[0], dict):
        return list(body[0])
    return list(body) if isinstance(body, dict) else []


def context(steps_b: list[dict], i: int, steps_h: list[dict], j: int, req: str = "request") -> str:
    """What led to a difference, in a line the judge can read without the code: what this request sent, the
    (up to three) steps before it with their statuses on base and head, and the fields each response had.
    A changed status after the user sent a newly announced field, or a changed list right after the same user
    did something the PR announces, is a consequence; without this the judge sees only the difference."""
    import json as _json

    def sent(step) -> str:
        value = step.get("sent")
        return "" if value in (None, "", {}, []) else _json.dumps(value, ensure_ascii=False, default=str)[:300]
    sb, sh = steps_b[i], (steps_h[j] if 0 <= j < len(steps_h) else {})
    parts = []
    if sent(sb):
        parts.append(f"sent {sent(sb)}")
    before = []
    for k in range(max(0, i - 3), i):
        b = steps_b[k]
        h = steps_h[j - (i - k)] if 0 <= j - (i - k) < len(steps_h) else {}
        status = str(b.get("status")) if b.get("status") == h.get("status") else \
            f"base {b.get('status')}, head {h.get('status')}"
        what = sent(b)
        before.append(f"{b.get(req)}" + (f" sent {what[:120]}" if what else "") + f" -> {status}")
    if before:
        parts.append("before that: " + "; ".join(before))
    fb, fh = _fields_of(sb.get("body")), _fields_of(sh.get("body"))
    if fb or fh:
        parts.append("base response fields: " + ", ".join(fb[:25]) if fb == fh else
                     f"base response fields: {', '.join(fb[:25]) or '(none)'}; head response fields: "
                     f"{', '.join(fh[:25]) or '(none)'}")
    return " | ".join(parts)[:900]


def _strip(value, ignore: set[str]):
    if isinstance(value, dict):
        return {k: _strip(v, ignore) for k, v in value.items() if k not in ignore}
    if isinstance(value, list):
        return [_strip(v, ignore) for v in value]
    return value


def changed_paths(a, b, prefix: str = "", limit: int = 10) -> list[str]:
    """Dotted paths where two JSON values differ, e.g. ["total", "items.0.price"]."""
    out: list[str] = []

    def walk(x, y, path):
        if len(out) >= limit:
            return
        if isinstance(x, dict) and isinstance(y, dict):
            for k in sorted(set(x) | set(y), key=str):
                p = f"{path}.{k}" if path else str(k)
                if k not in x:
                    out.append(f"{p} (added)")
                elif k not in y:
                    out.append(f"{p} (removed)")
                else:
                    walk(x[k], y[k], p)
        elif _lines(x) and _lines(y) and len(x) != len(y):
            gone, new = line_changes(x, y)
            out.append(f"{path or '(whole body)'} (-{len(gone)} +{len(new)} lines)")
        elif isinstance(x, list) and isinstance(y, list) and len(x) == len(y):
            for i, (xi, yi) in enumerate(zip(x, y, strict=True)):
                walk(xi, yi, f"{path}.{i}" if path else str(i))
        elif x != y:
            out.append(path or "(whole body)")

    walk(a, b, prefix)
    return out


def _lines(v) -> bool:
    return isinstance(v, list) and all(isinstance(i, str) for i in v)


def line_changes(a: list[str], b: list[str]) -> tuple[list[str], list[str]]:
    """The lines only in a and the lines only in b, aligned the way a text diff aligns them."""
    import difflib
    gone, new = [], []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op != "equal":
            gone += a[i1:i2]
            new += b[j1:j2]
    return gone, new


def _at(body, path: str):
    for part in path.split("."):
        if isinstance(body, list) and part.isdigit() and int(part) < len(body):
            body = body[int(part)]
        elif isinstance(body, dict) and part in body:
            body = body[part]
        else:
            return "(absent)"
    return body


def summarize_paths(paths: list[str], limit: int = 4) -> str:
    """["0.in_stock (added)", "1.in_stock (added)"] -> "*.in_stock (added) x2" so lists stay readable."""
    counts: dict[str, int] = {}
    for p in paths:
        key = ".".join("*" if part.isdigit() else part for part in p.split("."))
        counts[key] = counts.get(key, 0) + 1
    parts = [f"{k} x{n}" if n > 1 else k for k, n in counts.items()]
    return ", ".join(parts[:limit]) + (f", +{len(parts) - limit} more" if len(parts) > limit else "")


def compare(base: list[dict], head: list[dict], ignore=(), slow_ratio: float = 2.0,
            slow_min_ms: float = 50.0) -> list[Finding]:
    ignore = set(ignore)
    head_by_name = {r["persona"]: r for r in head}
    findings: list[Finding] = []
    for rb in base:
        rh = head_by_name.get(rb["persona"])
        if rh is None:
            continue
        for k, (sb, sh) in enumerate(zip(rb["steps"], rh["steps"], strict=False)):
            who, i, req = rb["persona"], sb["i"], sb["request"]
            mark = len(findings)
            b_bad = sb["status"] == 0 or sb["status"] >= 500
            h_bad = sh["status"] == 0 or sh["status"] >= 500
            if h_bad and not b_bad:
                findings.append(Finding("error", who, i, req, f"head failed with {sh['status'] or 'no response'}",
                                        sb["status"], sh["status"], context(rb["steps"], k, rh["steps"], k)))
                continue
            if sb["status"] != sh["status"]:
                findings.append(Finding("status", who, i, req, f"status {sb['status']} -> {sh['status']}",
                                        sb["status"], sh["status"], context(rb["steps"], k, rh["steps"], k)))
                continue
            bb, hb = _strip(sb["body"], ignore), _strip(sh["body"], ignore)
            if bb != hb:
                paths = changed_paths(bb, hb, limit=50)
                shown = [p.split(" (")[0] for p in paths[:2]]  # show values at the first changed paths
                if shown and shown[0] != "(whole body)":
                    vb, vh = {p: _at(bb, p) for p in shown}, {p: _at(hb, p) for p in shown}
                    for p in shown:  # for text lines, show only the lines that changed
                        if _lines(vb[p]) and _lines(vh[p]) and len(vb[p]) != len(vh[p]):
                            vb[p], vh[p] = line_changes(vb[p], vh[p])
                else:
                    vb, vh = bb, hb
                findings.append(Finding("body", who, i, req, "changed: " + summarize_paths(paths), vb, vh))
            if sh["ms"] >= slow_ratio * max(sb["ms"], 1) and sh["ms"] - sb["ms"] >= slow_min_ms:
                findings.append(Finding("slow", who, i, req, f"{sb['ms']:.0f} ms -> {sh['ms']:.0f} ms",
                                        sb["ms"], sh["ms"]))
            for f in findings[mark:]:
                f.context = f.context or context(rb["steps"], k, rh["steps"], k)
    findings.sort(key=lambda f: (KINDS.index(f.kind), f.persona, f.step))
    return findings
