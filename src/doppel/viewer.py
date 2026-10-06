"""doppel view: an init trace (`doppel init --trace`) as one web page you open in a browser.

Each attempt is a row on a five-stage rail (install, seed, start, login, users): the stages it got
past are lit, the one it stopped at is marked, and an attempt the pre-boot checks rejected is shown
as caught for free. Click a row to see what the model wrote, each check, the smoke requests, the
database after the seed, and why init kept or dropped it.

The page is static: the trace is embedded as JSON, nothing is fetched, and it works offline.
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

STAGES = ["install", "seed", "start", "login", "users"]
PROMPT_LIMIT = 300_000


def is_trace(path: Path) -> bool:
    return path.is_dir() and any(p.is_dir() and p.name.startswith("attempt-") for p in path.iterdir())


def _read(path: Path, limit: int | None = None) -> str:
    if not path.is_file():
        return ""
    text = path.read_text("utf-8", "replace")
    return text if limit is None or len(text) <= limit else text[:limit] + f"\n... [{len(text) - limit} more]"


def _checks(text: str) -> list[dict]:
    out: list[dict] = []
    for line in text.splitlines():
        m = re.match(r"^(PASS|FAIL)\s+(.*)$", line)
        if m:
            name, _, what = m.group(2).partition(": ")
            out.append({"ok": m.group(1) == "PASS", "name": name, "what": what, "problems": []})
        elif line.strip().startswith("- ") and out:
            out[-1]["problems"].append(line.strip()[2:])
    return out


def _boot(text: str) -> dict:
    head, _, error = text.partition("error sent back to the model:")
    smoke = [{"request": m.group(1), "status": int(m.group(2))}
             for m in re.finditer(r"smoke: (.+?) -> (\d+)", head)]
    return {"ran": bool(text), "works": "outcome: works" in head, "smoke": smoke, "error": error.strip()}


def _summary(trace: Path) -> dict[int, str]:
    lines = {}
    for line in _read(trace / "summary.txt").splitlines():
        m = re.match(r"attempt (\d+): (.*)", line)
        if m:
            lines[int(m.group(1))] = m.group(2)
    return lines


def classify(line: str, boot: dict) -> dict:
    """How far an attempt got and what init decided, from its summary line (or its files)."""
    if "WORKS" in line or boot.get("works"):
        return {"status": "works", "stage": len(STAGES), "decision": "The setup works: these files were written."}
    if "checked before booting" in line:
        return {"status": "caught", "stage": None,
                "decision": "Caught by the checks before booting. No sandbox was used; the problems went "
                            "back to the model."}
    if "unusable reply" in line:
        return {"status": "unusable", "stage": None, "decision": "The model's reply could not be used."}
    if "network failed" in line:
        return {"status": "network", "stage": None, "decision": "The network failed twice; the setup was never tried."}
    m = re.search(r"reached (\w+)", line)
    stage = STAGES.index(m.group(1)) if m and m.group(1) in STAGES else None
    if "[dropped" in line:
        return {"status": "dropped", "stage": stage,
                "decision": "Went backwards compared with an earlier attempt, so it was dropped. The next "
                            "draft started from the best attempt instead."}
    return {"status": "kept", "stage": stage,
            "decision": "Got the furthest so far, so the next draft started from these files."}


def load_trace(trace: Path) -> dict:
    trace = Path(trace)
    summary = _summary(trace)
    attempts = []
    for folder in sorted((p for p in trace.iterdir() if p.is_dir() and p.name.startswith("attempt-")),
                         key=lambda p: int(p.name.split("-")[1]) if p.name.split("-")[1].isdigit() else 0):
        n = int(folder.name.split("-")[1])
        boot = _boot(_read(folder / "4-boot.txt"))
        evidence = _read(folder / "5-evidence.txt")
        drafted = {p.name.removeprefix("2-drafted-"): _read(p) for p in sorted(folder.glob("2-drafted-*"))}
        attempts.append({
            "n": n,
            "summary": summary.get(n, ""),
            **classify(summary.get(n, ""), boot),
            "checks": _checks(_read(folder / "3-checks.txt")),
            "boot": boot,
            "database": [ln[len("[dbscan] "):] for ln in evidence.splitlines() if ln.startswith("[dbscan]")],
            "seed_output": [ln for ln in evidence.splitlines() if ln.strip() and not ln.startswith("[dbscan]")][-20:],
            "calibration": [ln for ln in _read(folder / "6-calibration.txt").splitlines() if ln.strip()],
            "files": drafted,
            "prompt": _read(folder / "1-prompt.txt", PROMPT_LIMIT),
            "reply": _read(folder / "2-reply.txt", PROMPT_LIMIT),
        })
    app = trace.resolve().parent.name
    return {"app": app, "stages": STAGES, "attempts": attempts,
            "outcome": next((a["n"] for a in attempts if a["status"] == "works"), None)}


def render(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")  # keep </script> out of the JSON
    return PAGE.replace("__TITLE__", html.escape(f"doppel init: {data['app']}")).replace("__DATA__", payload)


def write_view(trace: Path, out: Path | None = None) -> Path:
    trace = Path(trace)
    out = Path(out) if out else trace.resolve().parent / "doppel-view.html"
    out.write_text(render(load_trace(trace)), encoding="utf-8")
    return out


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400&display=swap" rel="stylesheet">
<style>
:root{--fog:#EEF1F4;--paper:#FFFFFF;--ink:#1E2A38;--quiet:#5B6878;--rail:#CDD5DE;--pass:#2F7D6D;--stop:#B23A48;
--free:#6B5CA5;--line:#DCE2E8;--sans:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif;
--mono:"IBM Plex Mono",ui-monospace,Consolas,monospace}
*{box-sizing:border-box}
body{margin:0;background:var(--fog);color:var(--ink);font:16px/1.6 var(--sans)}
main{max-width:980px;margin:0 auto;padding:48px 24px 80px}
h1{font-size:2.1rem;line-height:1.2;font-weight:600;margin:0 0 6px;letter-spacing:-.01em}
.lede{color:var(--quiet);margin:0 0 36px;max-width:64ch}
.key{display:flex;flex-wrap:wrap;gap:18px;color:var(--quiet);font-size:.85rem;margin:0 0 14px}
.key span{display:inline-flex;align-items:center;gap:6px}
.sw{width:22px;height:6px;border-radius:3px;display:inline-block}
.rows{background:var(--paper);border:1px solid var(--line);border-radius:14px;overflow:hidden}
.head,.row{display:grid;grid-template-columns:96px 1fr 210px;align-items:center;gap:20px;padding:14px 22px}
.head{color:var(--quiet);font-size:.8rem;border-bottom:1px solid var(--line)}
.stagenames{display:grid;grid-template-columns:repeat(5,1fr);gap:6px}
.row{width:100%;border:0;border-bottom:1px solid var(--line);background:none;font:inherit;color:inherit;text-align:left;cursor:pointer}
.row:last-child{border-bottom:0}
.row:hover{background:#F6F8FA}
.row:focus-visible{outline:3px solid var(--free);outline-offset:-3px}
.row[aria-expanded=true]{background:#F2F4F7}
.num{font-weight:600}
.rail{display:grid;grid-template-columns:repeat(5,1fr);gap:6px}
.seg{height:10px;border-radius:5px;background:var(--rail)}
.seg.pass{background:var(--pass)}
.seg.stop{background:var(--stop)}
.rail.free .seg{background:repeating-linear-gradient(90deg,var(--free) 0 6px,transparent 6px 10px);opacity:.55}
.verdict{font-size:.9rem;font-weight:500}
.verdict.works{color:var(--pass)}.verdict.caught{color:var(--free)}.verdict.stop{color:var(--stop)}.verdict.dropped{color:var(--quiet)}
.detail{display:none;padding:6px 22px 26px 138px;border-bottom:1px solid var(--line);background:#FAFBFC}
.detail.open{display:block}
.detail h3{font-size:.95rem;font-weight:600;margin:22px 0 8px}
.decision{margin:16px 0 0;padding:12px 16px;border-left:4px solid var(--rail);background:var(--paper);border-radius:0}
.decision.works{border-color:var(--pass)}.decision.caught{border-color:var(--free)}.decision.kept,.decision.dropped{border-color:var(--stop)}
ul.checks{list-style:none;padding:0;margin:0}
ul.checks li{padding:4px 0}
.ok{color:var(--pass);font-weight:600}.no{color:var(--stop);font-weight:600}
.problem{color:var(--stop);margin:2px 0 0 22px;font-size:.92rem}
table{border-collapse:collapse;font-size:.92rem}
td{padding:3px 18px 3px 0}
code,pre{font-family:var(--mono);font-size:.84rem}
pre{background:var(--paper);border:1px solid var(--line);border-radius:8px;padding:12px 14px;overflow:auto;max-height:420px;white-space:pre-wrap;word-break:break-word;margin:0}
details{margin:10px 0}
summary{cursor:pointer;color:var(--quiet);font-size:.92rem}
summary:focus-visible{outline:3px solid var(--free);outline-offset:2px}
.empty{color:var(--quiet);font-style:italic}
@media (max-width:720px){.head{display:none}.row{grid-template-columns:1fr;gap:8px}.detail{padding:6px 18px 22px}}
</style>
</head>
<body>
<main>
<h1 id="title"></h1>
<p class="lede" id="lede"></p>
<div class="key" aria-hidden="true">
<span><i class="sw" style="background:var(--pass)"></i>stage passed</span>
<span><i class="sw" style="background:var(--stop)"></i>stopped here</span>
<span><i class="sw" style="background:repeating-linear-gradient(90deg,var(--free) 0 6px,transparent 6px 10px);opacity:.55"></i>caught before booting, free</span>
</div>
<div class="rows" id="rows">
<div class="head"><span>Attempt</span><div class="stagenames" id="stagenames"></div><span>What happened</span></div>
</div>
</main>
<script id="trace" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('trace').textContent);
const $ = (tag, attrs = {}, ...kids) => { const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) k === 'class' ? e.className = v : e.setAttribute(k, v);
  for (const k of kids) e.append(k instanceof Node ? k : document.createTextNode(String(k))); return e; };
const NAMES = {install:'Install', seed:'Seed data', start:'Start', login:'Log in', users:'Users served'};
const VERDICT = {works:['Works','works'], caught:['Caught by checks','caught'], kept:['Stopped at ','stop'],
  dropped:['Went backwards','dropped'], unusable:['Reply unusable','stop'], network:['Network failed','stop']};

const n = D.attempts.length, won = D.outcome;
document.getElementById('title').textContent = won ? `${D.app} set itself up in ${won} attempt${won > 1 ? 's' : ''}`
                                                   : `${D.app}: no working setup yet`;
const free = D.attempts.filter(a => a.status === 'caught').length;
document.getElementById('lede').textContent =
  `${n} attempt${n > 1 ? 's' : ''}. ${free ? free + ' caught by the checks before any sandbox was used. ' : ''}` +
  'Each row shows how far that draft got; select one to see what the model wrote and what the twin found.';
for (const s of D.stages) document.getElementById('stagenames').append($('span', {}, NAMES[s]));

function rail(a) {
  const r = $('div', {class: 'rail' + (a.status === 'caught' ? ' free' : ''), 'aria-hidden': 'true'});
  D.stages.forEach((_, i) => r.append($('div', {class: 'seg' + (a.stage === null ? '' : i < a.stage ? ' pass' :
    i === a.stage && a.status !== 'works' ? ' stop' : '')})));
  return r;
}
function verdict(a) {
  const [text, cls] = VERDICT[a.status] || ['', ''];
  return $('span', {class: 'verdict ' + cls}, a.status === 'kept' && a.stage !== null ? text + NAMES[D.stages[a.stage]].toLowerCase() : text);
}
function section(title, body) { return [$('h3', {}, title), body]; }
function pre(text) { return $('pre', {}, text || ''); }

function detail(a) {
  const box = $('div', {class: 'detail', id: 'd' + a.n, role: 'region', 'aria-label': 'Attempt ' + a.n});
  box.append($('p', {class: 'decision ' + a.status}, a.decision));
  if (a.checks.length) {
    const ul = $('ul', {class: 'checks'});
    for (const c of a.checks) { const li = $('li', {}, $('span', {class: c.ok ? 'ok' : 'no'}, c.ok ? '✓ ' : '✗ '), c.what || c.name);
      for (const p of c.problems) li.append($('div', {class: 'problem'}, p)); ul.append(li); }
    box.append(...section('Checks before booting', ul));
  }
  if (a.boot.ran) {
    if (a.boot.smoke.length) { const t = $('table'); for (const s of a.boot.smoke)
      t.append($('tr', {}, $('td', {}, $('code', {}, s.request)), $('td', {class: s.status < 400 ? 'ok' : 'no'}, s.status)));
      box.append(...section('Smoke requests in the twin', t)); }
    if (a.boot.error) box.append(...section('What went back to the model', pre(a.boot.error)));
  } else if (a.status === 'caught') box.append(...section('Sandbox', $('p', {class: 'empty'}, 'Not booted: the checks stopped it first.')));
  if (a.database.length) box.append(...section('Databases after the seed', pre(a.database.join('\n'))));
  if (a.seed_output.length) box.append(...section('What the seed printed', pre(a.seed_output.join('\n'))));
  if (a.calibration.length) box.append(...section('Invented users', pre(a.calibration.join('\n'))));
  const files = Object.entries(a.files);
  if (files.length) { box.append($('h3', {}, 'Files the model drafted'));
    for (const [name, text] of files) box.append($('details', {}, $('summary', {}, name), pre(text))); }
  if (a.prompt) box.append($('details', {}, $('summary', {}, `Everything the model was asked (${a.prompt.length.toLocaleString()} characters)`), pre(a.prompt)));
  return box;
}

const rows = document.getElementById('rows');
for (const a of D.attempts) {
  const d = detail(a);
  const b = $('button', {class: 'row', 'aria-expanded': 'false', 'aria-controls': 'd' + a.n},
    $('span', {class: 'num'}, 'Attempt ' + a.n), rail(a), verdict(a));
  b.addEventListener('click', () => { const open = b.getAttribute('aria-expanded') !== 'true';
    b.setAttribute('aria-expanded', String(open)); d.classList.toggle('open', open); });
  rows.append(b, d);
}
const last = rows.querySelector('.row:last-of-type'); if (last) last.click();
</script>
</body>
</html>
"""
