<div align="center">

# Doppel

**A twin of your app that tries every pull request first.**

*Nebius x NVIDIA Global AI Hackathon, Coding and Agentic Engineering track*

</div>

Tests check what you thought of. Doppel checks what your users do.

Before a pull request is merged, Doppel builds a running copy of your app and its data,
fills it with synthetic users, forks it into two worlds (the main branch and the pull request),
replays the same users in both, and reports every difference: changed responses, new errors,
slower requests. Then it tells you which differences the PR meant to make and which are regressions.

```
            seed once                       fork per world (and per persona)
  base code ──────────► seeded twin ──┬──► base world ──► replay personas ──┐
                                      └──► head world ──► replay personas ──┴──► diff ──► PR report
```

## Status

| Stage | What | State |
|---|---|---|
| 1 | Local twin: seed once, fresh world per persona, replay, diff, Markdown report | done |
| 2 | Nebius Sandboxes backend (checkpoint + parallel forks) and Nemotron personas | done: live run found all 6 changes in 7.4 s |
| 3 | Nemotron judge: intended vs regression, using the PR description; writes a test per regression | built, first live run next |
| 4 | GitHub Action: comment on every PR, replay link | |
| 5 | Real open-source app demo + benchmark of planted regressions vs its own test suite | |
| 6 | README results, video, submission | |

## Try it (stage 1, offline)

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q
doppel run --base examples/shop/base --head examples/shop/head --personas examples/shop/personas.json
```

The shop example's pull request (`examples/shop/PR.md`) says it only adds an `in_stock` flag.
Doppel also finds the two changes it doesn't mention: coupon orders now cost more, and an order
with quantity 0 is accepted instead of rejected.

## Run it on Nebius (stage 2)

```bash
pip install -e ".[nebius,dev]"
cp .env.example .env        # fill in NEBIUS_API_KEY and NEBIUS_PROJECT_ID
# 1. the same hand-written personas, now in sandboxes: must match the local report
doppel run --backend nebius --base examples/shop/base --head examples/shop/head --personas examples/shop/personas.json
# 2. let Nemotron 3.5 Lightning invent personas, half of them aimed at what the PR changed
doppel personas --app examples/shop/base --head examples/shop/head -n 12 --out generated.json
doppel run --backend nebius --base examples/shop/base --head examples/shop/head --personas generated.json
```

How the twin uses sandbox checkpoints: base code is uploaded, dependencies installed and data
seeded once (checkpoint). The base world forks from it; the head world forks from it with the
PR's files laid on top. Every persona then runs in its own disposable fork of its world, all in
parallel, so no persona sees another's side effects.

## Judge the changes (stage 3)

```bash
doppel run --backend nebius --base examples/shop/base --head examples/shop/head \
  --personas examples/shop/personas.json --pr examples/shop/PR.md \
  --out report.md --tests-out test_doppel_regressions.py
```

Nemotron 3 Ultra reads the PR description and diff and labels each distinct change `intended`,
`regression` or `needs_human`. The report leads with regressions; intended changes fold away.
`--tests-out` writes one pytest per regression that replays the persona and expects the base
behavior: these tests pass on the main branch and fail on the PR. `--strict` exits 1 on regressions.

## Twin spec

Put a `doppel.toml` next to your app:

```toml
[app]
image = "python:3.12-slim"              # sandbox image
install = "pip install -r requirements.txt"
start = "python app.py"   # must listen on $PORT
ready = "/health"
seed = "python seed.py"   # runs once on the base code; both worlds start from this data
migrate = ""              # runs in each world after its code is in place

[diff]
ignore = ["created_at"]   # JSON keys allowed to differ
slow_ratio = 2.0
slow_min_ms = 50
```

## License

[MIT](LICENSE)
