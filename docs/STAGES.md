# Build stages

Target: submit 28 Oct 2026 (hard deadline 30 Oct, 10:00 PT = 22:30 IST). About 14 h a week.
Each stage ends with something that runs, a commit, and a tag. Planned dates were the original
schedule; the actual tag dates are shown next to each stage.

## Stage 0: repo, VS Code, GitHub (done 1 Oct)
- [x] Open `D:\MyAIProjects\doppel` in VS Code, create `.venv`, `pip install -e ".[dev]"`, `python -m pytest -q`
- [x] Create a public repo `doppel` on GitHub, push
- [x] Check the Actions tab: `tests` must be green, and its summary shows the twin report
- [x] Run `python scripts/probe_sandbox.py` and keep `logs/sandbox_probe.json`

## Stage 1: local twin (done 1 Oct, `v0.1-local-twin`)
Seed once, a fresh world per persona, replay, diff, Markdown report, offline tests.

## Stage 2: Nebius twin + Nemotron personas (done 1 Oct, `v0.2-nebius-twin`; planned Oct 2–9)
- [x] `backends/nebius.py`: base image -> install -> upload base code -> seed -> **checkpoint**.
  Head world = checkpoint + head files overlaid -> migrate -> checkpoint.
  Each persona = one disposable run from its world checkpoint; all personas run in parallel.
- [x] `personas.py`: Nemotron 3.5 Lightning reads the routes and writes N personas in the runner's
  JSON format. Validate the JSON; drop broken steps.
- [x] Parallelism set from the probe's numbers. Live run: all 6 changes found in 7.4 s.

## Stage 3: the judge (done 1 Oct, `v0.3-judge`; planned Oct 10–14)
- [x] Nemotron Ultra gets the PR description, the diff and each finding, and labels it
  `intended`, `regression` or `needs human`, with one sentence of reasoning.
- [x] For each regression, write a pytest that reproduces it (request + expected base answer).
- [x] Report groups findings by label; regressions first.

## Stage 4: the product loop (done 1 Oct, `v0.4-action`; planned Oct 15–18)
- [x] `action.yml`: on `pull_request`, check out base and head, run Doppel on Nebius, post or update
  one PR comment and a check run. Users add `NEBIUS_API_KEY` and `NEBIUS_PROJECT_ID` as secrets.
- [ ] Hosted report page with a replay of every persona: moved to Stage 6 as the optional Doppel Viewer.

## Stage 5: real app + evidence (done 3 Oct, `v0.5-evidence`; planned Oct 19–24)
- [x] microblog-api (Flask, 44 tests): 20 regression PRs + 10 safe PRs, plus 10 held-out PRs.
- [x] Measured tests vs AI review vs Doppel vs review-guided Doppel: catches, false alarms, time and $ per PR.
  Results: `bench/results/results.md` (the single source for every number quoted elsewhere).

## Stage 5b: `doppel init` and glass box (done 6 Oct, `v0.6-init`, `v0.7-glass-box`)
- [x] `doppel init` drafts `doppel.toml` + `doppel_seed.py` with Nemotron, boots them in the twin,
  fixes what breaks, calibrates noisy keys. microblog-api: 4 attempts, 81% served, no hand edits.
- [x] `--trace` (every attempt as readable files) and `--check` (test hand-made files).
- [x] Suspicion table agrees with the verdicts; transport errors retried.
- [x] `doppel init` on linkding (Django), compared with the answer key in `bench/init/linkding/`:
  no hand edits, 64% served, the same 3 noise keys; 15 attempts over four runs, with four fixes to init's feedback.
- [x] Doppel on real merged PRs of other repos: five apps it was never tuned for, each with a real PR
  ([TESTING-APPS.md](../TESTING-APPS.md)), and 14 real fixes ([bench/results/fixes](../bench/results/fixes)).

## Stage 6: ship (planned Oct 25–28)
- [ ] README first screen: claim with the number, chart, 15-second GIF, real badges.
- [ ] Red demo PR comment with the guided section, then the fix turning green.
- [ ] 3-minute video, Devpost text, feedback on Token Factory and Nemotron.
- [x] Doppel Viewer: `doppel view` for runs and init traces, with the twin's timeline.
- [x] Replay site: `doppel site` + GitHub Pages workflow (hash-leak hero, gallery, benchmark).
- [x] `doppel check`: test mode and the guided twin in parallel, one report and exit code; the GitHub
  Action runs it (test mode needs no doppel.toml).
- [x] Test mode (`doppel tests`, `src/doppel/traffic.py`): the app's own tests as the users, three
  worlds, four safeguards; first run on microblog-api in `bench/results/testmode`.
- [x] Setup that generalizes: the probe (facts from the running app), the front-door seed
  (`doppel_seed.json`), and the setup agent (Nemotron in a live sandbox) when drafts fail;
  the generality benchmark (`bench/generality.py`, `bench/apps/`).
- [x] A pass means something: a persona that fails only on head is an error finding (a head that no
  longer starts used to pass as "no behavior changes"), a run with nothing replayed exits 2, `[diff]` and
  personas come from base, and PRs that edit Doppel's setup are flagged (`tests/test_trust.py`).
- [x] Record one fresh Nebius run for the site, so its timeline shows parallel sandboxes.
- Tags: `v1.0.0`, `v1.1.0`; `v1.0-submission` marks the submitted commit.
