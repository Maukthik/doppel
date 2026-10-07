"""doppel site: a static replay site of real recorded runs, for GitHub Pages or any static host.

    doppel site --gallery site/gallery.json --out _site

The gallery file lists the runs to publish. Each entry points at either a run (`doppel run --json`)
or a benchmark result (bench/results/*.json, with the arm to show). Every run is sanitized before
it is written: values of keys that look like credentials are masked, bearer tokens and JWTs are
masked, and the build fails if any credential from the environment (NEBIUS_API_KEY, ...) would end
up on a page. Nothing on the site runs anything: it replays what was recorded, and says when.
"""
from __future__ import annotations

import html
import json
import os
import re
import shutil
from pathlib import Path

from doppel.viewer import CSS, render_run, run_data

SENSITIVE_KEY = re.compile(r"(passw(or)?d|secret|token|api[_-]?key|authorization|cookie|session|credential)", re.I)
JWT = re.compile(r"eyJ[\w-]{6,}\.[\w-]{6,}\.[\w-]{6,}")
BEARER = re.compile(r"(Bearer\s+)(?!\{)[\w.~+/=-]{8,}")
SECRET_ENV = re.compile(r"(API_KEY|TOKEN|SECRET|PASSWORD|PROJECT_ID)$")
MASK = "[hidden on the site]"
PLACEHOLDER = re.compile(r"(Bearer\s+|Basic\s+)?\{\w+\}")  # "Bearer {token}": a persona's template, not a secret
# password hashes by format, wherever they appear (also inside truncated JSON that can't be parsed)
HASH = re.compile(r"((?:scrypt|pbkdf2)[:\w-]*\$|\$argon2\w*\$|\$2[aby]\$\d\d\$)[^\s\"\\,}]+")
# "key": "value" for credential-looking keys, plain or backslash-escaped, in text that isn't valid JSON
KEY_VALUE = re.compile(r'(\\?"[\w-]*(?:passw(?:or)?d|secret|token|api[_-]?key|authorization|cookie|session)'
                       r'[\w-]*\\?"\s*:\s*\\?")((?:[^"\\]|\\[^"])+)', re.I)


class SecretLeak(RuntimeError):
    pass


def _mask(value):
    """Keep what a reader needs to see (that a password hash is there, and its scheme), drop the rest."""
    if not isinstance(value, str) or not value:
        return value
    if "$" in value[:40]:  # "scrypt:32768:8:1$salt$hash" or "pbkdf2:sha256:600000$..."
        return value.split("$", 1)[0] + "$" + MASK
    return MASK


def _scrub_text(text: str) -> str:
    text = HASH.sub(lambda m: m.group(1) + MASK, text)
    text = KEY_VALUE.sub(lambda m: m.group(1) + (m.group(2) if m.group(2).startswith(("[hidden", "{")) or
                                                  HASH.match(m.group(2)) or m.group(2).endswith(MASK) else MASK), text)
    text = JWT.sub(MASK, text)
    return BEARER.sub(lambda m: m.group(1) + MASK, text)


def sanitize(value, key: str = ""):
    """A copy of value with credentials masked. JSON held inside strings (recorded bodies) is
    parsed and masked too, then put back as a string."""
    if isinstance(value, dict):
        return {k: (_mask(v) if SENSITIVE_KEY.search(str(k)) and isinstance(v, str) and not PLACEHOLDER.fullmatch(v)
                    else sanitize(v, str(k))) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize(v, key) for v in value]
    if isinstance(value, str):
        stripped = value.strip()
        if stripped[:1] in "{[" and len(stripped) > 1:
            try:
                return json.dumps(sanitize(json.loads(stripped)), ensure_ascii=False)
            except ValueError:
                pass
        return _scrub_text(value)
    return value


def env_secrets(env: dict | None = None) -> list[str]:
    env = os.environ if env is None else env
    return [v for k, v in env.items() if SECRET_ENV.search(k) and isinstance(v, str) and len(v.strip()) >= 8]


def check_secrets(text: str, where: str, env: dict | None = None) -> None:
    """Fail closed: a page that would show a credential from this environment is never written."""
    for secret in env_secrets(env):
        if secret.strip() in text:
            raise SecretLeak(f"{where} would publish the value of a credential from the environment; "
                             "remove it from the recording before building the site")


def _maybe_json(text):
    if isinstance(text, str):
        try:
            return json.loads(text)
        except ValueError:
            return text
    return text


PHASES = {"personas": "invent users (Nemotron 3.5 Lightning)", "suspect": "AI reviewer lists suspicions",
          "twin": "replay in both worlds", "judge": "judge the differences (Nemotron 3 Ultra)",
          "check": "check each suspicion"}


def from_bench(result: dict, arm: str = "doppel_guided", pr: dict | None = None) -> dict:
    """A benchmark result (bench/results/*.json) as the dict `doppel run --json` writes, so the
    same page can show it. Benchmark runs keep stage timings, not per-sandbox ones."""
    g = result[arm]
    worlds = {w: [{"persona": r["persona"], "steps": [{"request": st["request"], "status": st["status"],
                                                       "body": _maybe_json(st.get("body"))} for st in r["steps"]]}
                  for r in g["recordings"][w]] for w in ("base", "head")}
    head_by = {r["persona"]: r for r in worlds["head"]}
    findings, verdicts = [], []
    for row in g["findings"]:
        rb = next((r for r in worlds["base"] if r["persona"] == row["persona"]), {"steps": []})
        rh = head_by.get(row["persona"], {"steps": []})
        matches = [k for k, st in enumerate(rb["steps"]) if st["request"] == row["request"]]
        differs = [k for k in matches if k < len(rh["steps"]) and
                   (rh["steps"][k]["status"], rh["steps"][k]["body"]) != (rb["steps"][k]["status"], rb["steps"][k]["body"])]
        step = (differs or matches or [0])[0]
        findings.append({"kind": row["kind"], "persona": row["persona"], "step": step, "request": row["request"],
                         "detail": row["detail"], "base": None, "head": None})
        verdicts.append({"label": row["label"], "why": row.get("why", "")})
    events, t = [], 0.0
    for name, secs in (g.get("seconds") or {}).items():
        events.append({"lane": "phase", "label": PHASES.get(name, name), "start": round(t, 2),
                       "end": round(t + float(secs), 2), "ok": True, "kind": "phase", "part": name})
        t += float(secs)
    pr = pr or {}
    return {"findings": findings, "verdicts": verdicts, "coverage": g.get("coverage", {}),
            "personas": g.get("personas", []), "suspicions": g.get("suspicions", []), "changes": [],
            "worlds": {**worlds, "failed": g.get("failed_personas", []),
                       "timeline": {"backend": g.get("backend", ""), "total": round(t, 2), "events": events,
                                    "note": "Recorded during the benchmark, which logged time per stage, "
                                            "not per sandbox."}},
            "pr": {"title": pr.get("title") or result.get("title", ""), "body": pr.get("body", "")},
            "app": "microblog-api"}


def bench_scores(folder: Path, set_name: str = "main") -> dict:
    """Same scoring as bench/summarize.py: caught on regression PRs, alarms on safe PRs."""
    rows = [json.loads(p.read_text("utf-8")) for p in sorted(Path(folder).glob("*.json"))]
    rows = [r for r in rows if r.get("set", "main") == set_name]
    reg = [r for r in rows if r["kind"] == "regression"]
    safe = [r for r in rows if r["kind"] == "safe"]
    out = {}
    for arm in ("tests", "review", "doppel", "doppel_guided"):
        have = [r for r in rows if isinstance(r.get(arm), dict)]
        if not have:
            continue

        def alarm(r, arm=arm):
            return r[arm].get("any_regression", r[arm]["caught"]) if arm.startswith("doppel") else r[arm]["caught"]
        out[arm] = {"caught": sum(bool(r[arm]["caught"]) for r in reg if isinstance(r.get(arm), dict)),
                    "regressions": len(reg),
                    "alarms": sum(bool(alarm(r)) for r in safe if isinstance(r.get(arm), dict)), "safe": len(safe)}
    both = sum(bool(r["tests"]["caught"] or r["doppel_guided"]["caught"]) for r in reg
               if isinstance(r.get("tests"), dict) and isinstance(r.get("doppel_guided"), dict))
    out["tests_plus_guided"] = {"caught": both, "regressions": len(reg)}
    return out


def _load_entry(entry: dict, root: Path) -> tuple[dict, dict]:
    """(raw run dict, comparison {tests, review} or {})."""
    src = json.loads((root / entry["source"]).read_text("utf-8"))
    if "worlds" in src:
        compare = {}
        if entry.get("compare"):  # a re-recorded benchmark PR keeps the benchmark's tests and review results
            bench = json.loads((root / entry["compare"]).read_text("utf-8"))
            compare = {k: bench[k] for k in ("tests", "review") if isinstance(bench.get(k), dict)}
        return src, compare
    prs = {}
    index = root / entry.get("pr_index", "bench/microblog/prs/index.json")
    if index.is_file():
        prs = {p["id"]: p for p in json.loads(index.read_text("utf-8"))}
    raw = from_bench(src, entry.get("arm", "doppel_guided"), prs.get(src.get("id")))
    return raw, {k: src[k] for k in ("tests", "review") if isinstance(src.get(k), dict)}


def _verdict(data: dict) -> tuple[str, str]:
    reg = [f for f in data["findings"] if f["label"] == "regression"]
    if reg:
        n = len(reg)
        return "red", f"Regression the pull request doesn't mention, seen in {n} answer{'s' if n > 1 else ''}"
    if not data["findings"]:
        return "green", "No behavior changed"
    if not data["judged"]:
        return "plain", f"{len(data['findings'])} behavior changes, not judged"
    return "green", "Every change matches the pull request"


def build(gallery: Path, out: Path, root: Path | None = None, env: dict | None = None) -> list[Path]:
    """Writes index.html and one page per run under out/runs/. Returns the files written."""
    gallery = Path(gallery)
    root = Path(root) if root else gallery.resolve().parent.parent
    cfg = json.loads(gallery.read_text("utf-8"))
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    (out / "runs").mkdir(parents=True)
    written, cards, fork = [], [], {}
    nav = '<p class="crumb"><a href="../index.html">Doppel replays</a></p>\n'
    for entry in cfg["runs"]:
        raw, compare = _load_entry(entry, root)
        raw = sanitize(raw)
        raw.setdefault("recorded", {})["note"] = entry.get("recorded", "")
        if entry.get("title"):
            raw.setdefault("pr", {})["title"] = entry["title"]
        data = run_data(raw, raw.get("app", "app"))
        page = render_run(data, nav=nav).replace("</style>", CRUMB_CSS + "</style>", 1)
        check_secrets(page, f"runs/{entry['slug']}.html", env)
        path = out / "runs" / f"{entry['slug']}.html"
        path.write_text(page, encoding="utf-8")
        written.append(path)
        tl = data.get("timeline") or {}
        people = [e for e in tl.get("events", []) if e.get("kind") == "persona" and e.get("lane") in ("base", "head")]
        if tl.get("backend") == "nebius" and people and (entry["slug"] == cfg.get("hero", {}).get("slug") or not fork):
            peak = max(sum(1 for o in people if o["start"] < e["end"] and o["end"] > e["start"]) for e in people)
            fork.update(users=len(data["users"]), runs=len(people), peak=peak, total=tl.get("total", 0),
                        slug=entry["slug"])
        tone, verdict = _verdict(data)
        cards.append({"slug": entry["slug"], "title": data["pr"].get("title") or entry["slug"],
                      "story": entry.get("story", ""), "tone": tone, "verdict": verdict, "compare": compare,
                      "recorded": entry.get("recorded", ""), "users": len(data["users"]),
                      "requests": sum(len(u["steps"]) for u in data["users"])})
    scores = bench_scores(root / cfg["bench"]) if cfg.get("bench") and (root / cfg["bench"]).is_dir() else {}
    chart = root / cfg["chart"] if cfg.get("chart") else None
    if chart and chart.is_file():
        shutil.copy(chart, out / "chart.svg")
    hero = cfg.get("hero", {})
    if hero.get("source"):
        hero_raw, hero_cmp = _load_entry(hero, root)
        hero = {**hero, "evidence": _hero_evidence(sanitize(hero_raw), hero.get("field", "")),
                "compare": hero_cmp}
    hero["fork"] = fork
    index = render_index(cfg, cards, scores, hero, has_chart=(out / "chart.svg").exists())
    check_secrets(index, "index.html", env)
    (out / "index.html").write_text(index, encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    return [out / "index.html", *written]


def _hero_evidence(raw: dict, field: str) -> dict:
    """The first recorded request where `field` appears in the head answer but not the base one."""
    base = {r["persona"]: r for r in raw["worlds"]["base"]}
    for rh in raw["worlds"]["head"]:
        rb = base.get(rh["persona"])
        if not rb:
            continue
        for sb, sh in zip(rb["steps"], rh["steps"], strict=False):
            b, h = json.dumps(sb.get("body"), ensure_ascii=False), json.dumps(sh.get("body"), ensure_ascii=False)
            if field and field in h and field not in b:
                return {"request": sh["request"], "persona": rh["persona"], "base": sb.get("body"),
                        "head": sh.get("body"), "base_status": sb["status"], "head_status": sh["status"]}
    return {}


def _json_lines(value, field: str) -> str:
    """Pretty JSON as HTML, with the line holding `field` marked."""
    text = json.dumps(value, indent=2, ensure_ascii=False) if not isinstance(value, str) else value
    lines = []
    for line in text.splitlines()[:40]:
        esc = html.escape(line)
        lines.append(f'<mark>{esc}</mark>' if field and f'"{field}"' in line else esc)
    return "\n".join(lines)


def fork_figure(stats: dict) -> str:
    """The idea in one picture: one seeded checkpoint, two worlds, the same users through both, one
    answer that differs. Drawn once on load; the caption carries real numbers from a Nebius run."""
    lanes = []
    for k in range(5):
        y_b, y_h, delay = 58 + k * 12, 178 + k * 12, 1.0 + k * 0.12
        red = k == 2
        for y, cls in ((y_b, "b"), (y_h, "h hit" if red else "h")):
            lanes.append(f'<line class="tr {cls}" x1="160" x2="354" y1="{y}" y2="{y}" style="--d:{delay:.2f}s"/>'
                         f'<circle class="u {cls}" cx="0" cy="0" r="3.4" style="--y:{y}px;--d:{delay:.2f}s"/>')
    if stats:
        cap = (f'{stats["users"]} users through both worlds: {stats["runs"]} throwaway sandboxes, {stats["peak"]} at once, '
               f'{stats["total"]:.0f} s for the whole twin. <a href="runs/{html.escape(stats["slug"])}.html">See the run</a>')
    else:
        cap = "Every user gets a fresh copy of each world, so no run can leak into another."
    return f'''<figure class="fork" aria-label="One checkpoint forks into two worlds; the same users go through both; one answer differs">
<svg viewBox="0 0 380 300" role="img" aria-hidden="true">
<path class="br" d="M70 150 C 110 150, 110 90, 150 90"/><path class="br" d="M70 150 C 110 150, 110 210, 150 210"/>
<rect class="cp" x="14" y="128" width="58" height="44" rx="8"/><text class="cpt" x="43" y="147">seeded</text><text class="cpt" x="43" y="161">checkpoint</text>
<g class="w"><rect x="150" y="48" width="214" height="84" rx="10" class="wb"/><text x="160" y="42" class="wl">Before: main branch</text></g>
<g class="w after"><rect x="150" y="168" width="214" height="84" rx="10" class="wb"/><text x="160" y="162" class="wl">After: the pull request</text></g>
{"".join(lanes)}
<g class="flag"><rect x="236" y="262" width="128" height="24" rx="6"/><text x="300" y="278">+ password_hash</text></g>
</svg>
<figcaption>{cap}</figcaption>
</figure>'''


def _mark(res: dict | None, kind: str) -> str:
    if not res:
        return ""
    if kind == "tests":
        return f"Passed ({html.escape(res.get('summary', ''))})" if not res["caught"] else "Failed: caught it"
    return f"Said {html.escape(res.get('verdict', ''))}" if not res["caught"] else "Flagged it"


def render_index(cfg: dict, cards: list[dict], scores: dict, hero: dict, has_chart: bool) -> str:
    e = html.escape
    parts = []
    ev = hero.get("evidence") or {}
    if ev:
        cmp = hero.get("compare", {})
        review_why = (cmp.get("review") or {}).get("why", "")
        parts.append(f'''<section class="hero" aria-labelledby="hero-title">
<div class="hero-top"><div>
<h1 id="hero-title">{e(hero.get("headline", ""))}</h1>
<p class="lede">{e(hero.get("lede", ""))}</p>
</div>{fork_figure(hero.get("fork") or {})}</div>
<ol class="verdicts">
<li><span class="who">The app's own tests</span><span class="said miss">{_mark(cmp.get("tests"), "tests")}</span></li>
<li><span class="who">An AI code reviewer</span><span class="said miss">{_mark(cmp.get("review"), "review")}</span>
{f'<q>{e(review_why)}</q>' if review_why else ''}</li>
<li><span class="who">Doppel</span><span class="said stop">Regression, with the recorded answer</span></li>
</ol>
<div class="evidence">
<p class="req"><code>{e(ev["request"])}</code>, sent by the invented user <b>{e(ev["persona"])}</b> to both worlds</p>
<div class="pair">
<figure><figcaption>Before the pull request: {ev["base_status"]}</figcaption><pre>{_json_lines(ev["base"], "")}</pre></figure>
<figure><figcaption>After the pull request: {ev["head_status"]}</figcaption><pre>{_json_lines(ev["head"], hero.get("field", ""))}</pre></figure>
</div>
<p class="note">The hash itself is hidden on this site; the twin recorded it in full.
<a href="runs/{e(hero.get("slug", ""))}.html">See the whole run</a></p>
</div>
</section>''')
    parts.append('''<section aria-labelledby="how"><h2 id="how">How Doppel checks a pull request</h2>
<ol class="how">
<li><b>Build the twin once.</b> Install the app and load its data in a Nebius sandbox, then save a checkpoint.</li>
<li><b>Fork two worlds.</b> The main branch and the pull request, both starting from that identical data.</li>
<li><b>Send the same invented users through both.</b> Nemotron writes them from the code; each gets a fresh copy of each world.</li>
<li><b>Compare and judge.</b> Every answer that differs is labelled intended or regression against what the pull request says.</li>
</ol></section>''')
    rows = []
    for c in cards:
        cmp = c["compare"]
        others = "".join(f'<span>{label}: {_mark(cmp.get(k), k)}</span>' for k, label in
                         (("tests", "Tests"), ("review", "AI review")) if cmp.get(k))
        rows.append(f'''<li class="run {c["tone"]}"><a href="runs/{e(c["slug"])}.html">
<span class="t">{e(c["title"])}</span>
<span class="v">{e(c["verdict"])}</span>
<span class="s">{e(c["story"])}</span>
{f'<span class="o">{others}</span>' if others else ''}
<span class="r">{c["users"]} users, {c["requests"]} requests each world. {e(c["recorded"])}</span></a></li>''')
    parts.append(f'''<section aria-labelledby="runs"><h2 id="runs">Recorded runs</h2>
<p class="sub">Each page replays one real run: what happened inside the twin, every request in both worlds, and the judge's verdicts.</p>
<ul class="gallery">{"".join(rows)}</ul></section>''')
    if scores:
        names = {"tests": "The app's own tests", "review": "AI code review", "doppel": "Doppel alone",
                 "doppel_guided": "Review-guided Doppel"}
        trs = "".join(f'<tr><td>{names[k]}</td><td>{v["caught"]}/{v["regressions"]}</td><td>{v["alarms"]}/{v["safe"]}</td></tr>'
                      for k, v in scores.items() if k in names)
        both = scores.get("tests_plus_guided")
        parts.append(f'''<section aria-labelledby="bench"><h2 id="bench">On a real app</h2>
<p class="sub">{e(cfg.get("bench_text", ""))}</p>
{'<img class="chart" src="chart.svg" alt="Regressions caught and false alarms per method">' if has_chart else ''}
<table class="scores"><thead><tr><th>Method</th><th>Regressions caught</th><th>False alarms</th></tr></thead><tbody>{trs}
{f'<tr class="sum"><td>Tests and review-guided Doppel together</td><td>{both["caught"]}/{both["regressions"]}</td><td></td></tr>' if both else ''}
</tbody></table>
<p class="sub"><a href="{e(cfg.get("bench_link", ""))}">Method, every PR and honest limits</a></p></section>''')
    parts.append(f'''<footer><p>{e(cfg.get("footer", ""))}</p>
<p><a href="{e(cfg.get("repo", ""))}">Source on GitHub</a></p></footer>''')
    return (INDEX.replace("__CSS__", CSS + INDEX_CSS).replace("__TITLE__", e(cfg.get("title", "Doppel")))
            .replace("__DESC__", e(cfg.get("description", ""))).replace("__BODY__", "\n".join(parts)))


CRUMB_CSS = """.crumb{margin:0 0 18px;font-size:.92rem}.crumb a{color:var(--free)}
"""

INDEX_CSS = r"""
a{color:var(--free)}
a:focus-visible{outline:3px solid var(--free);outline-offset:2px}
.brand{font-weight:600;margin:0 0 40px;font-size:1rem}
.hero-top{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(0,1fr);gap:28px;align-items:center;margin:0 0 6px}
.hero h1{font-size:clamp(1.9rem,4.2vw,2.9rem);line-height:1.12;max-width:20ch;margin:0 0 14px;letter-spacing:-.02em}
.fork{margin:0}.fork svg{width:100%;height:auto;display:block}
.fork figcaption{font-size:.85rem;color:var(--quiet);margin-top:4px}
.fork .br{fill:none;stroke:var(--rail);stroke-width:2.5;stroke-dasharray:120;stroke-dashoffset:120;animation:draw .6s ease-out .25s forwards}
.fork .cp{fill:var(--ink)}.fork .cpt{fill:#fff;font-size:9.5px;text-anchor:middle}
.fork .wb{fill:var(--paper);stroke:var(--line);stroke-width:1.5}.fork .after .wb{stroke:#C9D3DE}
.fork .wl{fill:var(--quiet);font-size:11px}
.fork .w{opacity:0;animation:fade .4s ease-out .7s forwards}
.fork .u{fill:#7C93AE;opacity:0;transform:translate(160px,var(--y));animation:walk 1.5s cubic-bezier(.4,0,.2,1) var(--d) forwards}
.fork .u.h{fill:#3E5C7E}
.fork .tr{stroke:#DCE3EA;stroke-width:2;stroke-dasharray:194;stroke-dashoffset:194;animation:draw 1.5s cubic-bezier(.4,0,.2,1) var(--d) forwards}
.fork .tr.hit{animation:draw 1.5s cubic-bezier(.4,0,.2,1) var(--d) forwards,line-red .3s ease-out 2.7s forwards}
@keyframes line-red{to{stroke:#E8B4BC}}
.fork .u.hit{fill:#3E5C7E;animation:walk 1.5s cubic-bezier(.4,0,.2,1) var(--d) forwards,turn .3s ease-out 2.7s forwards}
.fork .flag{opacity:0;animation:fade .35s ease-out 2.8s forwards}
.fork .flag rect{fill:#F6DADF;stroke:var(--stop)}.fork .flag text{fill:var(--stop);font:600 11px "IBM Plex Mono",ui-monospace,monospace;text-anchor:middle}
@keyframes draw{to{stroke-dashoffset:0}}
@keyframes fade{to{opacity:1}}
@keyframes walk{0%{opacity:0;transform:translate(160px,var(--y))}15%{opacity:1}100%{opacity:1;transform:translate(354px,var(--y))}}
@keyframes turn{to{fill:var(--stop)}}
@media (prefers-reduced-motion:reduce){.fork *{animation:none!important}.fork .br{stroke-dashoffset:0}.fork .w,.fork .flag{opacity:1}
.fork .u{opacity:1;transform:translate(354px,var(--y))}.fork .u.hit{fill:var(--stop)}.fork .tr{stroke-dashoffset:0}.fork .tr.hit{stroke:#E8B4BC}}
.hero .lede{font-size:1.08rem;margin:0 0 28px}
.verdicts{list-style:none;padding:0;margin:0 0 22px;display:grid;gap:2px;background:var(--line);border:1px solid var(--line);border-radius:14px;overflow:hidden}
.verdicts li{display:grid;grid-template-columns:200px 1fr;gap:6px 16px;padding:14px 18px;background:var(--paper);align-items:baseline}
.verdicts .who{color:var(--quiet)}
.verdicts .said{font-weight:600}.said.miss{color:var(--ink)}.said.stop{color:var(--stop)}
.verdicts q{grid-column:2;color:var(--quiet);font-size:.9rem}
.evidence{background:var(--paper);border:1px solid var(--line);border-left:6px solid var(--stop);border-radius:14px;padding:18px 20px}
.evidence .req{margin:0 0 12px}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.pair figure{margin:0}.pair figcaption{font-size:.85rem;color:var(--quiet);margin:0 0 6px}
.pair pre{max-height:340px}
mark{background:#F6DADF;color:var(--stop);font-weight:600;border-radius:3px;padding:0 2px}
.note{font-size:.88rem;color:var(--quiet);margin:12px 0 0}
section{margin:0 0 8px}
.how{padding:0 0 0 1.3em;margin:0;max-width:70ch}.how li{margin:0 0 10px}
.gallery{list-style:none;padding:0;margin:0;display:grid;gap:10px}
.run a{display:grid;gap:4px;padding:16px 20px;background:var(--paper);border:1px solid var(--line);border-left:6px solid var(--rail);border-radius:12px;color:inherit;text-decoration:none}
.run a:hover{background:#F6F8FA}
.run.red a{border-left-color:var(--stop)}.run.green a{border-left-color:var(--pass)}
.run .t{font-weight:600}.run.red .v{color:var(--stop)}.run.green .v{color:var(--pass)}.run .v{font-weight:500}
.run .s{color:var(--ink);max-width:72ch}
.run .o{display:flex;flex-wrap:wrap;gap:16px;font-size:.88rem;color:var(--quiet)}
.run .r{font-size:.85rem;color:var(--quiet)}
.chart{max-width:100%;height:auto;background:var(--paper);border:1px solid var(--line);border-radius:12px;padding:12px;margin:0 0 14px}
table.scores{width:100%;background:var(--paper);border:1px solid var(--line);border-radius:12px;border-collapse:separate;overflow:hidden;font-variant-numeric:tabular-nums}
.scores th,.scores td{padding:10px 14px;text-align:left;border-bottom:1px solid var(--line)}
.scores th{font-weight:500;color:var(--quiet);font-size:.88rem}
.scores tr.sum td{font-weight:600;border-bottom:0}
footer{margin:56px 0 0;color:var(--quiet);font-size:.9rem;border-top:1px solid var(--line);padding-top:18px}
@media (max-width:720px){.hero-top{grid-template-columns:1fr;margin-bottom:22px}.pair{grid-template-columns:1fr}.verdicts li{grid-template-columns:1fr}.verdicts q{grid-column:1}}
"""

INDEX = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<meta name="description" content="__DESC__">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400&display=swap" rel="stylesheet">
<style>
__CSS__
</style>
</head>
<body>
<main>
<p class="brand">Doppel</p>
__BODY__
</main>
</body>
</html>
"""
