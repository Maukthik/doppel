# Build stages

Target: submit 28 Oct 2026 (hard deadline 30 Oct, 10:00 PT = 22:30 IST). About 14 h a week.
Each stage ends with something that runs, a commit, and a tag.

## Stage 0: repo, VS Code, GitHub (today, ~1 h)
- [ ] Open `D:\MyAIProjects\doppel` in VS Code, create `.venv`, `pip install -e ".[dev]"`, `python -m pytest -q`
- [ ] Create an empty public repo `doppel` on GitHub (no README, no license), push
- [ ] Check the Actions tab: `tests` must be green, and its summary shows the twin report
- [ ] Run `python scripts/probe_sandbox.py` (needs `.env` with your Nebius key; no model credits) and keep `logs/sandbox_probe.json`
- Tag: `v0.1-local-twin`

## Stage 1: local twin (done in the starter code)
Seed once, a fresh world per persona, replay, diff, Markdown report, 12 offline tests.

## Stage 2: Nebius twin + Nemotron personas (Oct 2–9, ~14 h)
- `backends/nebius.py`: base image -> install -> upload base code -> seed -> **checkpoint**.
  Head world = checkpoint + head files overlaid -> migrate -> checkpoint.
  Each persona = one disposable run from its world checkpoint that starts the app in the
  background and runs `runner.py` (stdlib) against it; all personas run in parallel.
- `personas.py`: Nemotron 3.5 Lightning reads the routes (OpenAPI spec or the route code) and
  writes N personas in the runner's JSON format. Validate the JSON; drop broken steps.
- Use the probe's numbers to set the parallelism.
- Tag: `v0.2-nebius-twin`

## Stage 3: the judge (Oct 10–14, ~10 h)
- Nemotron Ultra gets the PR description, the diff and each finding, and labels it
  `intended`, `regression` or `needs human`, with one sentence of reasoning.
- For each regression, write a pytest that reproduces it (request + expected base answer).
- Report groups findings by label; regressions first.
- Tag: `v0.3-judge`

## Stage 4: the product loop (Oct 15–18, ~8 h)
- `action.yml`: on `pull_request`, check out base and head, run Doppel on Nebius, post or update
  one PR comment and a check run. Users add `NEBIUS_API_KEY` and `NEBIUS_PROJECT_ID` as secrets.
- Hosted report page with a replay of every persona (no access code, rate-limited).
- Tag: `v0.4-action`

## Stage 5: real app + evidence (Oct 19–24, ~12 h)
- Pick one well-known open-source Python web app (for example a RealWorld "Conduit" backend).
- Plant ~20 realistic regressions as pull requests (wrong totals, lost validation, renamed
  fields, N+1 queries, migrations that drop data) plus ~10 safe PRs.
- Measure: how many the app's own test suite catches vs how many Doppel catches, false alarms on
  the safe PRs, minutes and $ per PR. Commit the raw results and the chart script.
- Tag: `v0.5-evidence`

## Stage 6: ship (Oct 25–28, ~8 h)
- README first screen: claim with the number, chart, 15-second GIF, real badges.
- 3-minute video, Devpost text, feedback on Token Factory and Nemotron.
- Tag: `v1.0-submission`
