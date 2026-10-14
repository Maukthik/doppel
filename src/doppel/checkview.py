"""The `doppel check` page: what a PR changed, proven, on one static page (no JavaScript; opens anywhere).

    doppel view check.json            # the JSON `doppel check --json` writes
    render_check(data, public=True)   # the replay site's version: secrets and password hashes masked

Sections: the verdict; what each mode did (your tests, invented users); the three worlds and what each
comparison means; one card per distinct change (where, who saw it, which mode, Nemotron's verdict and reason,
the example's base and head side by side, every request behind it); what needs a human; the intended changes;
the AI reviewer's suspicions as the twin checked them.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

from doppel.viewer import CSS


def is_check(path: Path) -> bool:
    path = Path(path)
    if path.is_dir():
        path = path / "check.json"
    try:
        return path.is_file() and json.loads(path.read_text("utf-8")).get("mode") == "check"
    except (ValueError, OSError):
        return False


def _e(value) -> str:
    return html.escape(str(value), quote=True)


def _pretty(value) -> str:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return value
    return json.dumps(value, indent=1, ensure_ascii=False, default=str)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _modes(data: dict) -> str:
    tests, twin = data.get("tests") or {}, data.get("twin") or {}
    cards = []
    if tests.get("ran"):
        st = tests.get("stats") or {}
        left = []
        if st.get("failing_on_base"):
            left.append(f"{_plural(st['failing_on_base'], 'test')} failing on unchanged code")
        if st.get("flaky"):
            left.append(f"{st['flaky']} flaky")
        if st.get("unstable"):
            left.append(f"{st['unstable']} compared on outcome only")
        conf = st.get("confirmation") or {}
        facts = [f"<b>{st.get('tests', 0)}</b> tests sent <b>{st.get('requests', 0)}</b> requests",
                 f"command <code>{_e(tests.get('test', ''))}</code> (from {_e(tests.get('source', ''))})"]
        if tests.get("noise_keys"):
            facts.append("noise ignored: " + ", ".join(f"<code>{_e(k)}</code>" for k in tests["noise_keys"][:8]))
        if left:
            facts.append("left out: " + "; ".join(left))
        if conf.get("ran"):
            facts.append(f"every difference re-checked by rerunning {_plural(conf.get('tests', 0), 'test')}"
                         + (f"; {conf['dropped']} dropped as noise" if conf.get("dropped") else ""))
        body = "".join(f"<li>{f}</li>" for f in facts)
        cards.append(f'<div class="mode on"><h3>Your tests <span>test mode · no setup</span></h3><ul>{body}</ul></div>')
    else:
        full = tests.get("reason") or "did not run"
        reason = full.splitlines()[0].split(" Use the HTTP twin")[0]
        rest = full.partition("\n")[2].strip()
        output = (f'<details><summary>The end of the output</summary><pre>{_e(rest[-2500:])}</pre></details>'
                  if rest else "")   # where the real error is: the last lines, not the first
        cards.append(f'<div class="mode off"><h3>Your tests <span>test mode</span></h3><p>Couldn\'t compare: '
                     f'{_e(reason[:400])}</p>{output}</div>')
    if twin.get("ran"):
        cov = twin.get("coverage") or {}
        served = f"; the app served <b>{cov['served']}</b> of {cov['requests']} requests" if cov.get("requests") else ""
        cards.append(f'<div class="mode on"><h3>Invented users <span>the guided twin</span></h3><ul>'
                     f'<li><b>{twin.get("personas", 0)}</b> users ({_e(twin.get("source", ""))}), replayed in both '
                     f'worlds{served}</li><li>an AI reviewer\'s suspicions turned into probe users</li></ul></div>')
    else:
        cards.append(f'<div class="mode off"><h3>Invented users <span>the guided twin</span></h3>'
                     f'<p>{_e((twin.get("reason") or "did not run").splitlines()[0][:400])}</p></div>')
    return f'<div class="modes">{"".join(cards)}</div>'


WORLDS = """<section class="worlds3" aria-label="How Doppel decides">
<div class="ws"><div class="w"><b>Base</b><span>the code before the PR</span></div>
<div class="w"><b>Base again</b><span>the same code, run again</span></div>
<div class="w after"><b>Head</b><span>the code with the PR</span></div></div>
<div class="wcap"><p><b>Base vs base again:</b> whatever differs between two runs of the same code is noise, and is
ignored.</p><p><b>Base vs head:</b> every other difference is a behavior change. It is re-checked by running it
again, then judged by Nemotron against the PR description.</p></div></section>"""


def _group_card(k: int, g: dict, clean) -> str:
    ex = g["example"]
    label = g["label"]
    tag = {"regression": "Regression", "unjudged": "Behavior change", "needs_human": "Needs a human",
           "intended": "Intended"}.get(label, label)
    seen = ", ".join(_seen(g["rows"], s) for s in g["sources"])
    routes = ", ".join(f"<code>{_e(r)}</code>" for r in g["routes"][:4]) + (
        f" and {len(g['routes']) - 4} more" if len(g["routes"]) > 4 else "")
    why = f'<p class="why {label}">{_e(clean(g["why"]))}</p>' if g.get("why") else ""
    rows = "".join(f"<tr><td>{_e(r['source'])}</td><td>{_e(r['who'])}</td><td><code>{_e(r['request'])}</code></td>"
                   f"<td>{_e(r['detail'])}</td></tr>" for r in g["rows"])
    changes = headline(g)
    return f"""<article class="change {label}">
<header><span class="n">{k}</span><div><h3>{_e(changes)}</h3><p>{routes} · {_e(seen)}</p></div>
<span class="pill {label}">{tag}</span></header>{why}
<div class="sbs"><div><p>Base <span>before the PR</span></p><pre>{_e(clean(_pretty(ex["base"])))}</pre></div>
<div><p>Head <span>with the PR</span></p><pre>{_e(clean(_pretty(ex["head"])))}</pre></div></div>
<details><summary>All {_plural(len(g["rows"]), "request")} behind this change</summary>
<table class="reqs"><colgroup><col style="width:14%"><col style="width:30%"><col style="width:26%"><col style="width:30%">
</colgroup><thead><tr><th>Found by</th><th>Who</th><th>Request</th><th>What changed</th></tr></thead>
<tbody>{rows}</tbody></table></details></article>"""


def headline(g: dict) -> str:
    """The change in plain words: "Responses now include password_hash", "status 400 → 201", "Requests now
    crash (no response)"."""
    import re

    from doppel.check import _fields
    ex = g["example"]
    kind, detail = ex.get("kind", ""), ex.get("detail", "")
    detail = re.sub(r"\s*\(the base branch's test fails on this\)", "", detail)
    if kind == "body" and detail.startswith("changed: "):
        added, removed, changed = [], [], []
        for f in _fields(detail).split(", "):
            (added if f.endswith("(added)") else removed if f.endswith("(removed)") else changed).append(
                f.replace(" (added)", "").replace(" (removed)", ""))
        parts = []
        if added:
            parts.append("Responses now include " + ", ".join(added))
        if removed:
            parts.append(("responses" if parts else "Responses") + " no longer include " + ", ".join(removed))
        if changed:
            parts.append(("values" if parts else "Values") + " changed: " + ", ".join(changed))
        return "; ".join(parts) or detail
    if kind == "error" or "failed with no response" in detail or re.search(r"-> 0\b", detail):
        return "Requests now crash (no response): " + detail
    if kind == "test":
        return "A test that passed now fails: " + detail.split(": ", 1)[-1]
    if kind == "slow":
        return "Slower: " + detail
    if kind == "request":
        return "The tests now send different requests: " + detail
    return detail[:1].upper() + detail[1:]


def _seen(rows: list[dict], source: str) -> str:
    mine = [r for r in rows if r["source"] == source]
    noun = {"your tests": "test", "invented users": "user"}.get(source, source)
    return f"{_plural(len(mine), 'request')} from {_plural(len({r['who'] for r in mine}), noun)}"


def _suspicions(data: dict, clean) -> str:
    items = (data.get("twin") or {}).get("suspicions") or []
    if not items:
        return ""
    mark = {"confirmed": "🔴 confirmed", "intended": "🟢 confirmed, intended", "refuted": "⚪ refuted",
            "untested": "⚪ untested"}
    order = {"confirmed": 0, "untested": 1, "intended": 2, "refuted": 3}
    rows = []
    for s in sorted(items, key=lambda x: order.get(x.get("status", "untested"), 1)):
        rows.append(f"<tr><td class='st'>{_e(mark.get(s.get('status', 'untested'), s.get('status', '')))}</td>"
                    f"<td>{_e(clean(s.get('claim', '')))}</td><td>{_e(clean(str(s.get('evidence', ''))))[:700]}</td></tr>")
    return ("<h2>The AI reviewer's suspicions, checked by the twin</h2><p class='sub'>Nemotron read the diff and "
            "listed what might break; the twin wrote a probe user for each and ran it in both worlds.</p>"
            "<table class='sus'><colgroup><col style='width:18%'><col style='width:36%'><col style='width:46%'></colgroup>"
            "<thead><tr><th></th><th>Suspicion</th><th>Evidence</th></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table>")


def render_check(data: dict, public: bool = False, nav: str = "") -> str:
    """public: mask secrets, tokens and password hashes (the replay site); local pages show raw evidence."""
    from doppel.check import group
    if public:
        from doppel.site import _scrub_text, sanitize
        data = sanitize(data)
        clean = _scrub_text
    else:
        def clean(text):
            return text
    rows = data.get("rows") or []
    regressions = group([r for r in rows if r["label"] in ("regression", "unjudged")])
    human = group([r for r in rows if r["label"] == "needs_human"])
    intended = group([r for r in rows if r["label"] == "intended"])
    unjudged = bool(regressions) and all(g["label"] == "unjudged" for g in regressions)
    tests, twin = data.get("tests") or {}, data.get("twin") or {}
    n_req = sum(len(g["rows"]) for g in regressions)
    if not tests.get("ran") and not twin.get("ran"):
        verdict, tone = "Couldn't check this PR", "grey"
    elif regressions:
        noun = "behavior change" if unjudged else "regression"
        verdict = (f"{_plural(len(regressions), noun)}" + ("" if unjudged else " the PR doesn't mention")
                   + f", seen in {_plural(n_req, 'request')}")
        tone = "red"
    else:
        from doppel.check import untested
        open_, total = untested(twin)
        verdict, tone = (f"No regressions found, but {open_} of the reviewer's {_plural(total, 'suspicion')} "
                         "couldn't be tested", "grey") if open_ else ("No regressions", "green")
    pr = data.get("pr") or {}
    sections = []
    if regressions:
        title = "Behavior changes (not judged: no PR description given)" if unjudged else \
            "Regressions the PR doesn't mention"
        sections.append(f"<h2>{title}</h2>" + "".join(_group_card(k, g, clean) for k, g in enumerate(regressions, 1)))
    if human:
        sections.append("<h2>Needs a human</h2>" + "".join(_group_card(k, g, clean) for k, g in enumerate(human, 1)))
    if intended:
        sections.append(f"<details class='intended-all'><summary>Intended: matches the PR description "
                        f"({len(intended)})</summary>"
                        + "".join(_group_card(k, g, clean) for k, g in enumerate(intended, 1)) + "</details>")
    if not sections and (tests.get("ran") or twin.get("ran")):
        sections.append("<p class='allsame'>Every response matched between the code before and after the PR "
                        "(after ignoring what differs between two runs of the same code).</p>")
    body = f"""{nav}<p class="brand">doppel check · {_e(data.get('app', ''))}</p>
<h1>{_e(pr.get('title') or 'Behavior check')}</h1>
<p class="what">Doppel ran this app's code from before and after the pull request side by side, compared every
response, and had NVIDIA Nemotron judge each difference against what the pull request says it does.</p>
<div class="banner {tone}">{_e(verdict)}</div>
{_modes(data)}
{WORLDS}
{''.join(sections)}
{_suspicions(data, clean)}
<footer>Each change above comes from real runs of the code before and after the PR, in Nebius sandboxes; the
verdicts are NVIDIA Nemotron's, judged against the PR description. Seconds: {_e(data.get('seconds', '?'))}.
<br><a href="https://github.com/Maukthik/doppel">What is Doppel? Add it to your repo in one file →</a></footer>"""
    title = f"doppel check: {pr.get('title') or data.get('app', '')}"
    return PAGE.replace("__TITLE__", _e(title)).replace("__CSS__", CSS + CHECK_CSS).replace("__BODY__", body)


def write_check_view(path: Path, out: Path | None = None, public: bool = False) -> Path:
    path = Path(path)
    src = path / "check.json" if path.is_dir() else path
    out = Path(out) if out else src.parent / "doppel-check.html"
    out.write_text(render_check(json.loads(src.read_text("utf-8")), public=public), encoding="utf-8")
    return out


CHECK_CSS = r"""
.brand{color:var(--quiet);font-size:.85rem;letter-spacing:.04em;text-transform:uppercase;margin:0 0 8px}
.what{color:var(--quiet);margin:4px 0 0;max-width:72ch}
footer a{color:var(--free);font-weight:500}
.banner{font-size:1.25rem;font-weight:600;padding:14px 20px;border-radius:12px;margin:18px 0 26px;color:#fff}
.banner.red{background:var(--stop)}.banner.green{background:var(--pass)}.banner.grey{background:var(--quiet)}
.modes{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:14px;margin:0 0 22px;align-items:start}
.mode{background:var(--paper);border:1px solid var(--line);border-radius:14px;padding:14px 18px}
.mode h3{margin:0 0 8px;font-size:1rem;font-weight:600}.mode h3 span{color:var(--quiet);font-weight:400;font-size:.85rem;margin-left:6px}
.mode ul{margin:0;padding-left:18px;font-size:.92rem}.mode li{margin:2px 0}.mode p{margin:0;font-size:.92rem;color:var(--quiet)}
.mode.off{opacity:.8;border-style:dashed}
.worlds3{margin:0 0 30px}
.ws{display:flex;gap:10px;margin:0 0 10px}
.ws .w{flex:1 1 0;background:var(--paper);border:1px solid var(--line);border-radius:12px;padding:10px 14px;text-align:center}
.ws .w b{display:block;font-weight:600}.ws .w span{display:block;color:var(--quiet);font-size:.85rem}
.ws .w.after{border:2px solid var(--free)}   /* not "head": the viewer's base CSS styles .head as a grid row */
.wcap{display:flex;gap:14px;color:var(--quiet);font-size:.88rem}.wcap p{flex:1 1 0;margin:0}
h2{font-size:1.15rem;font-weight:600;margin:34px 0 12px}
.sub{color:var(--quiet);margin:-6px 0 12px;font-size:.92rem}
.change{background:var(--paper);border:1px solid var(--line);border-left:5px solid var(--rail);border-radius:14px;
padding:16px 18px;margin:0 0 14px}
.change.regression,.change.unjudged{border-left-color:var(--stop)}.change.needs_human{border-left-color:#C08A1E}
.change.intended{border-left-color:var(--pass)}
.change header{display:flex;gap:12px;align-items:flex-start}.change header>div{flex:1 1 auto;min-width:0}
.change .n{flex:0 0 28px;height:28px;line-height:28px;border-radius:14px;background:var(--fog);text-align:center;font-weight:600}
.change h3{margin:0;font-size:1rem;font-weight:600}.change header p{margin:2px 0 0;color:var(--quiet);font-size:.88rem}
.pill{flex:0 0 auto;font-size:.78rem;font-weight:600;padding:3px 10px;border-radius:999px;background:var(--fog);white-space:nowrap}
.pill.regression,.pill.unjudged{background:#F7E3E6;color:var(--stop)}.pill.intended{background:#E2F1ED;color:var(--pass)}
.pill.needs_human{background:#F8EED9;color:#8A5A00}
.why{margin:12px 0 0;padding:10px 14px;border-left:4px solid var(--stop);background:#FBFAFC;font-size:.92rem}
.why.intended{border-color:var(--pass)}.why.needs_human{border-color:#C08A1E}
.sbs{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:12px;margin:12px 0 0}
.sbs p{margin:0 0 4px;font-weight:600;font-size:.88rem}.sbs p span{color:var(--quiet);font-weight:400;margin-left:4px}
.sbs pre{max-height:260px;overflow-wrap:anywhere;word-break:break-all}
table.reqs,table.sus{width:100%;table-layout:fixed;font-size:.88rem;border-collapse:collapse;background:var(--paper)}
table.reqs td,table.sus td,table.reqs code{overflow-wrap:anywhere;word-break:break-word}
table.reqs th,table.sus th{text-align:left;color:var(--quiet);font-weight:500;padding:6px 10px;border-bottom:1px solid var(--line)}
table.reqs td,table.sus td{padding:6px 10px;border-bottom:1px solid var(--line);vertical-align:top}
table.sus{border:1px solid var(--line);border-radius:14px}
td.st{white-space:nowrap}
.allsame{background:var(--paper);border:1px solid var(--line);border-radius:14px;padding:16px 18px}
.intended-all>summary{font-size:1rem;color:var(--ink);font-weight:600;margin:24px 0 10px}
footer{margin:40px 0 0;color:var(--quiet);font-size:.85rem;border-top:1px solid var(--line);padding-top:14px}
@media (max-width:720px){.modes,.sbs{grid-template-columns:1fr}.ws,.wcap{flex-direction:column}}
"""

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title><style>__CSS__</style></head>
<body><main>__BODY__</main></body></html>
"""
