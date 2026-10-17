"""Review-guided twin: an AI reviewer suspects, the twin proves or refutes.

An AI code reviewer reading the diff is good at noticing what *might* break, but it only claims:
it can't run the code, so it also flags things that are fine and waves through things that
aren't. Doppel can run the code but only sees what its personas happen to do. Together:

  1. suspect():  Nemotron 3 Ultra reads the PR description, the diff and the source, and lists
                 every behavior change it suspects the PR doesn't announce, with how to trigger it
  2. personas:   for each suspicion, probe personas are written to trigger it and read back the
                 result (personas.generate(..., suspicions=...))
  3. the twin replays everything in base and head, as usual
  4. check():    each suspicion becomes "confirmed" (the twin saw it), "refuted" (a probe reached
                 the code and both worlds behaved the same) or "untested" (no probe got there)

The PR comment then shows evidence for confirmed suspicions and drops refuted ones, so reviewers
see proven regressions, not guesses.
"""
from __future__ import annotations

import json
import os
import re
from urllib.parse import unquote_plus

from doppel.personas import cost_usd, nemotron_chat, parse_json

MODEL = "nvidia/Nemotron-3-Ultra-550b-a55b"
STATUSES = ("confirmed", "refuted", "untested")

SUSPECT_PROMPT = """You review a pull request to the web API below. List every behavior change the PR could
cause that its description does NOT announce and that a user or API client could notice: wrong data,
lost validation, new errors or crashes, changed status codes, removed/renamed/leaked fields,
authorization mistakes, wrong side effects. Prefer recall: if in doubt, list it; a running copy of the
app will check each one. Skip changes the description announces.

For each, say exactly how to trigger it with HTTP requests against the API (which route, which
input, and what to read back afterwards to see the effect). The trigger input must be one that the code
before and after the PR treat DIFFERENTLY: an input both versions handle the same way proves nothing. When
it depends on characters or values, name every one that differs (e.g. escaping with quote=False instead of
quote=True differs on ' and " only; & and < are escaped either way), and list them in "inputs".

Return ONLY JSON:
{{"suspicions": [{{"id": "s1", "claim": "one sentence", "route": "METHOD /path",
                   "trigger": "requests to send and what to read back",
                   "inputs": ["each exact value to send that base and head treat differently"]}}]}}
Use at most {max_n} suspicions, most likely first. Return an empty list if the PR is a pure refactor.

PR description:
{pr}

PR diff:
{diff}

App source (before the PR):
{source}
"""

CHECK_PROMPT = """A reviewer suspected the behavior changes below in a pull request. A running copy of the
app replayed probe users against the code before the PR (base) and after it (head). For each
suspicion decide, from the evidence only:
- "confirmed": one or more observed behavior changes show it; list their ids in "changes".
  Base and head answering the same is never "confirmed".
- "refuted": a probe reached the route with input that would trigger it, and base and head
  answered the same, so it does not happen. The input has to be one that the suspected change and the
  old code would treat differently (a suspicion about quotes needs a probe that sent a quote; an input
  both versions handle the same way refutes nothing: that is "untested"). Put the exact value the probe
  sent, copied from its request, in "sent".
- "untested": no request reached it with triggering input (wrong route, login failed, 404 from a
  bad path, unresolved placeholder like {{post_id}}), so the evidence says nothing.

Return ONLY JSON:
{{"checks": [{{"id": "s1", "status": "confirmed|refuted|untested", "changes": ["f1"],
               "sent": "for refuted: the triggering value the probe sent, copied exactly",
               "evidence": "one sentence naming the request(s) and what base and head answered"}}]}}

Suspicions:
{suspicions}

Behavior changes the twin observed (id, request, what changed, base, head):
{changes}

What the probe users sent and what each world answered:
{probes}
"""


def suspect(pr_text: str, diff: str, source: str, model: str | None = None, max_n: int = 6,
            chat=nemotron_chat) -> dict:
    """Returns {"suspicions": [{"id", "claim", "route", "trigger"}], "usd": ...}."""
    model = model or os.getenv("SUSPECT_MODEL", MODEL)
    prompt = SUSPECT_PROMPT.format(max_n=max_n, pr=pr_text.strip() or "(no description)",
                                   diff=diff[:60_000] or "(no diff)", source=source)
    out, usd = [], 0.0
    for _ in range(2):  # an unreadable reply would silently turn guided mode off: ask once more
        text, tin, tout = chat(model, prompt, temperature=0.2)
        usd += cost_usd(model, tin, tout)
        raw = parse_json(text, key="suspicions")
        for i, s in enumerate(raw.get("suspicions", []) if isinstance(raw, dict) else []):
            if isinstance(s, dict) and s.get("claim"):
                inputs = [str(v)[:120] for v in s.get("inputs", []) if str(v).strip()][:6] \
                    if isinstance(s.get("inputs"), list) else []
                trigger = str(s.get("trigger", ""))[:400]
                if inputs:  # the probe writer reads the trigger: the exact values go with it
                    trigger += " Send each of these values: " + ", ".join(json.dumps(v) for v in inputs)
                out.append({"id": f"s{len(out) + 1}", "claim": str(s["claim"])[:300],
                            "route": str(s.get("route", ""))[:80], "trigger": trigger, "inputs": inputs})
            if len(out) >= max_n or i > 3 * max_n:
                break
        if out or (isinstance(raw, dict) and isinstance(raw.get("suspicions"), list)):
            break  # got suspicions, or a readable "none": a pure refactor
    return {"suspicions": out, "usd": usd, "model": model}


def is_probe(persona: dict) -> bool:
    """A persona written for a suspicion: tagged with "probe", or named like one when the model
    dropped the tag. The checker and the bench's probe counter both use this, so they agree."""
    return bool(persona.get("probe")) or str(persona.get("name", "")).lower().startswith("probe")


def _probe_lines(personas: list[dict], base: list[dict], head: list[dict], limit: int = 30_000) -> str:
    """Compact transcripts of the probe personas (the ones written for a suspicion)."""
    head_by = {r["persona"]: r for r in head}
    lines = []
    for p, rb in ((p, rb) for rb in base for p in personas if p["name"] == rb["persona"]):
        if not is_probe(p):
            continue
        rh = head_by.get(rb["persona"], {"steps": []})
        lines.append(f"probe '{p['name']}' for {p.get('probe', 'a suspicion')}:")
        for sb, sh in zip(rb["steps"], rh["steps"], strict=False):
            b = json.dumps(sb["body"], ensure_ascii=False, default=str)[:140]
            h = json.dumps(sh["body"], ensure_ascii=False, default=str)[:140]
            sent = f" sent {json.dumps(sb['sent'], ensure_ascii=False, default=str)[:200]}" if sb.get("sent") else ""
            lines.append(f"  {sb['request']}{sent} -> base {sb['status']} {b} | head {sh['status']} {h}")
    return "\n".join(lines)[:limit] or "(no probe personas ran)"


def _norm_sent(text: str) -> str:
    """A value as it can appear in a request: JSON-escaped or not, URL-quoted or not."""
    return unquote_plus(str(text)).replace('\\"', '"').replace("\\'", "'").replace("\\\\", "\\")


def probe_sent(personas: list[dict], base: list[dict]) -> str:
    """Everything the probe personas sent in the base world (paths and bodies), for checking a refutation."""
    probes = {p["name"] for p in personas if is_probe(p)}
    out = []
    for r in base:
        if r.get("persona") in probes:
            for st in r.get("steps", []):
                out.append(str(st.get("request", "")))
                if st.get("sent") is not None:
                    sent = st["sent"]
                    out.append(sent if isinstance(sent, str) else json.dumps(sent, ensure_ascii=False, default=str))
    return _norm_sent("\n".join(out))


def _get(obj, name):
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def probe_of(persona: dict) -> str:
    """The suspicion id a probe persona was written for ("s1"), from its tag or its name."""
    tag = str(persona.get("probe") or "")
    if re.fullmatch(r"s\d+", tag):
        return tag
    m = re.match(r"probe[\s_:-]*(s\d+)", str(persona.get("name", "")), re.I)
    return m.group(1).lower() if m else ""


def reconcile(suspicions: list[dict], findings, labels: list, personas: list[dict]) -> list[dict]:
    """A suspicion is never refuted or untested while a regression came from its own probe.

    The checker is a model reading transcripts; on r17 it refuted "password_hash is exposed" while
    probe_s1, written for that suspicion, recorded GET /api/users/1 gaining password_hash. The
    twin's measurement wins: such a suspicion is confirmed, citing the probe's changes. labels holds
    the judge's label per finding; only findings the judge called a regression count, so an unjudged
    run (or a probe whose change was intended) leaves the checker's verdict as it was."""
    owner = {p.get("name"): probe_of(p) for p in personas if is_probe(p)}
    out = []
    for x in suspicions:
        x = dict(x)
        if x.get("status") != "confirmed":
            hits = [i for i, f in enumerate(findings)
                    if owner.get(_get(f, "persona")) == x.get("id") and
                    i < len(labels) and labels[i] == "regression"]
            if hits:
                x["status"], x["changes"] = "confirmed", [f"f{i + 1}" for i in hits]
                x["evidence"] = "; ".join(f"{_get(findings[i], 'persona')}: {_get(findings[i], 'request')} "
                                          f"{_get(findings[i], 'detail')}" for i in hits[:3]) + \
                    (f" (+{len(hits) - 3} more)" if len(hits) > 3 else "")
        out.append(x)
    return out


def check(suspicions: list[dict], findings, verdicts, personas: list[dict], base: list[dict],
          head: list[dict], model: str | None = None, chat=nemotron_chat) -> dict:
    """Returns {"checks": {sid: {"status", "evidence"}}, "usd": ...}. No suspicions: no call."""
    if not suspicions:
        return {"checks": {}, "usd": 0.0}
    model = model or os.getenv("JUDGE_MODEL", MODEL)
    changes = "\n".join(
        json.dumps({"id": f"f{i + 1}", "request": f.request, "what": f.detail, "base": f.base, "head": f.head,
                    "judged": v.label if v else None}, ensure_ascii=False, default=str)[:600]
        for i, (f, v) in enumerate(zip(findings, verdicts or [None] * len(findings), strict=True))) or "(none)"
    prompt = CHECK_PROMPT.format(suspicions=json.dumps(suspicions, ensure_ascii=False, indent=1),
                                 changes=changes[:30_000], probes=_probe_lines(personas, base, head))
    text, tin, tout = chat(model, prompt, temperature=0.1)
    raw = parse_json(text, key="checks") or {}
    got = {str(c.get("id")): c for c in (raw.get("checks", []) if isinstance(raw, dict) else [])
           if isinstance(c, dict) and c.get("status") in STATUSES}
    ids = {f"f{i + 1}" for i in range(len(findings))}
    sent_by_probes = probe_sent(personas, base)
    checks = {}
    for s in suspicions:
        c = got.get(s["id"], {})
        status, evidence = c.get("status", "untested"), str(c.get("evidence", "No verdict from the checker."))[:400]
        cited = [x for x in (c.get("changes") or []) if str(x) in ids] if isinstance(c.get("changes"), list) else []
        if status == "confirmed" and not cited:  # no observed change behind it: it is not proof
            status, evidence = "untested", f"(no observed change cited) {evidence}"
        sent = str(c.get("sent") or "").strip()
        if status == "refuted" and (not sent or _norm_sent(sent) not in sent_by_probes):
            # isso, 16 Oct: s1 (quote escaping) was refuted on a probe that only sent '&'. A refutation has to
            # name the triggering value, and a probe has to have sent it
            status = "untested"
            evidence = (f"(no probe sent the triggering value {sent!r}) " if sent else
                        "(the checker named no triggering value a probe sent) ") + evidence
        checks[s["id"]] = {"status": status, "evidence": evidence, "changes": cited}
    labels = [v.label if v else None for v in (verdicts or [None] * len(findings))]
    fixed = reconcile([{**s, **checks[s["id"]]} for s in suspicions], findings, labels, personas)
    checks = {x["id"]: {k: x[k] for k in ("status", "evidence", "changes")} for x in fixed}
    return {"checks": checks, "usd": cost_usd(model, tin, tout)}
