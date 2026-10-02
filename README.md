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
| 3 | Nemotron judge: intended vs regression, using the PR description; writes a test per regression | done |
| 4 | GitHub Action: comment on every PR, artifacts, fails the check on regressions | done: live on a demo repo |
| 5 | Benchmark on a real app: 30 PRs to microblog-api; tests 12/20, AI review 17/20, Doppel 11/20 (0 false alarms); review-guided Doppel next | in progress |
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

## Use it on your pull requests (stage 4)

`.github/workflows/doppel.yml` in your repo:

```yaml
name: doppel
on: pull_request
permissions: { contents: read, pull-requests: write }
jobs:
  twin:
    runs-on: ubuntu-latest
    steps:
      - uses: Maukthik/doppel@main
        with:
          app-dir: .                     # folder with doppel.toml
          nebius-api-key: ${{ secrets.NEBIUS_API_KEY }}
          nebius-project-id: ${{ secrets.NEBIUS_PROJECT_ID }}
```

Every PR gets one comment (updated on each push), the report in the job summary, and the personas,
raw recordings and regression tests as run artifacts. The check fails on regressions. Without a
`doppel-personas.json`, Nemotron invents the users. PRs from forks don't get secrets, so the twin
runs on branches of the repo itself. `examples/demo-repo` sets up a repo to watch it work.

## Benchmark on a real app (stage 5)

30 pull requests to [microblog-api](https://github.com/miguelgrinberg/microblog-api) (Flask, 44 tests):
20 carry a regression (3 are reverted upstream bug fixes), 10 are safe. Its own test suite catches
12 of the 20. Doppel and an AI code reviewer (Nemotron 3 Ultra reading the diff) run on the same PRs.
Method and honest limits: [bench/README.md](bench/README.md). Results: [bench/results/results.md](bench/results/results.md).

Personas can log in: a step can send HTTP Basic credentials (`"auth": ["alice", "pw"]`), save the
returned token, and later steps send it in a header (`"headers": {"Authorization": "Bearer {token}"}`).
With `--rehearse` (on in the Action), personas first run once on base; any stuck at 401/403/404 go back
to Nemotron with the app's real answers and are fixed before the comparison. Every report states
coverage ("the app served X of Y requests"), so "no regressions" is never confused with "couldn't test".

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
