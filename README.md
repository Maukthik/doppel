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
| 5 | Benchmark on a real app (30 PRs to microblog-api + 10 held out): tests 12/20, AI review 17/20, review-guided Doppel 16/20 with 0 false alarms; tests + Doppel together 20/20 | done |
| 5b | `doppel init`: Nemotron drafts the twin spec and seed, boots them, fixes what breaks, finds noisy keys | done: microblog-api (Flask) and linkding (Django) set up with no hand edits; on linkding it found the same 3 noise keys as the hand-written answer key ([bench/init](bench/init)) |
| 6 | README results, video, submission | in progress |

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

## See a run

Every run now says whether the users actually ran the code the PR changed ("Changed code reached by the
users: 3 of 3 change(s)"): a line tracer in each world records which changed functions executed.

```bash
doppel run ... --json run.json
doppel view run.json        # one web page: both worlds per user, every difference, verdicts, reach
```

The GitHub Action saves the same page as `doppel-run.html` in each run's artifacts.

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
raw recordings and regression tests as run artifacts. The check fails on regressions, and a persona that
runs on base but fails on head (say the app no longer starts) counts as one; a run where no persona replayed
at all exits 2 instead of passing. Personas come from `doppel-personas.json` on the base branch, so a PR
can't pick the users that test it; without one, Nemotron invents them. PRs from forks don't get secrets, so the twin
runs on branches of the repo itself. `examples/demo-repo` sets up a repo to watch it work.

## Benchmark on a real app (stage 5)

30 pull requests to [microblog-api](https://github.com/miguelgrinberg/microblog-api) (Flask, 44 tests):
20 carry a regression (3 are reverted upstream bug fixes), 10 are safe. Its own test suite catches
12 of the 20, an AI code reviewer (Nemotron 3 Ultra reading the diff) 17 with 1 false alarm, and
review-guided Doppel 16 with no false alarms. They catch different bugs: the app's tests plus Doppel
catch all 20. On 10 held-out PRs written afterwards: tests 6/7, AI review 5/7, Doppel 6/7, no false alarms.
Results vary between runs, and the judge was improved after the held-out run; both are disclosed in the method.
Method and honest limits: [bench/README.md](bench/README.md). Results: [bench/results/results.md](bench/results/results.md).

Personas can log in: a step can send HTTP Basic credentials (`"auth": ["alice", "pw"]`), save the
returned token, and later steps send it in a header (`"headers": {"Authorization": "Bearer {token}"}`).
With `--rehearse` (on in the Action), personas first run once on base; any stuck at 401/403/404 go back
to Nemotron with the app's real answers and are fixed before the comparison. Every report states
coverage ("the app served X of Y requests"), so "no regressions" is never confused with "couldn't test".

## Replay site

`doppel site` turns recorded runs into a static site: the hash-leak story up front, a gallery of
runs, and the benchmark. Each run page shows what happened inside the twin on one time axis (the
checkpoint, the two worlds, every user's throwaway run), then every request in both worlds and the
judge's verdicts. Nothing runs when a page opens; it replays what was recorded and says when.

```bash
doppel site --gallery site/gallery.json --out _site    # then open _site/index.html
```

`site/gallery.json` lists the runs to publish: a `doppel run --json` file, or a benchmark result with
the arm to show. Credentials and password hashes are masked before publishing (the hash scheme stays
visible, so a leak is still a leak), and the build fails if any credential from the environment would
appear on a page. `.github/workflows/site.yml` publishes it on GitHub Pages on every push to main
(one-time setup: Settings, Pages, Source: GitHub Actions).

## Twin spec

Let Doppel write it (needs `NEBIUS_API_KEY` and `NEBIUS_PROJECT_ID`):

```bash
cd your-app
doppel init            # writes doppel.toml (+ doppel_seed.json / doppel_seed.py), proven in a sandbox
doppel probe           # just print what Doppel learns about the app: routes, database, login, models
```

`doppel init` sets an app up in three layers, each catching what the one before missed:

1. **Ask the app, don't guess.** It installs the app in a sandbox and imports it the way its framework does
   (Flask, Django, FastAPI): the real routes and what each view reads, where the database lives, which view
   is the login, the models' required fields, the app's own commands, and any import error.
2. **Seed through the front door.** The preferred starting data is `doppel_seed.json`: ordinary HTTP steps
   (sign up, log in, create records) sent once to the running app before the checkpoint, so the app itself
   hashes passwords, fills required fields and picks its database. A seed script remains for data no route
   can create.
3. **An agent when the drafts fail.** Nemotron 3 Ultra drafts the files from those facts (`--tries 2`); if
   they fail, a setup agent takes over in a live sandbox with `shell`, `read`, `serve` (start the app and
   send it requests) and `try` (the full twin check), up to 20 steps, like a developer setting up an
   unfamiliar repo. `--no-agent` stops after the drafts.

Finally it replays invented users against the **same** code twice: any JSON key that differs between two
identical runs is noise and is added to `[diff] ignore`. Review the files before committing. Use
`--backend local` to try it on your machine.

- `doppel init --trace` saves every attempt to `.doppel-trace/`: the prompt, the reply, each check, what the
  twin did, the database scan, and why init kept or dropped the attempt.
- `doppel view .doppel-trace` turns that trace into one web page (opened in your browser): each attempt on a
  five-stage rail, with the checks, smoke requests, database scan and drafted files one click away.
- `doppel init --check` tests the `doppel.toml` and `doppel_seed.py` already in the app, with no drafting:
  the way to finish a setup by hand (add `--calibrate` to replay invented users too).

Or write it by hand. Put a `doppel.toml` next to your app:

```toml
[app]
image = "python:3.12-slim"              # sandbox image
install = "pip install -r requirements.txt"
start = "python app.py"   # must listen on $PORT
ready = "/health"
seed = "python seed.py"   # runs once on the base code; both worlds start from this data
migrate = ""              # runs in each world after its code is in place
env = { DATABASE_URL = "sqlite:///twin.db" }   # one set of env vars for install, seed, migrate and start

[diff]
ignore = ["created_at"]   # JSON keys allowed to differ
slow_ratio = 2.0
slow_min_ms = 50
```

On a pull request, `[app]` is read from the PR (it may add a dependency or a migration) and `[diff]` from
the base branch, so a PR can't add the fields it changes to `ignore`. A PR that edits `doppel.toml`,
`doppel_seed.py` or the personas file gets a warning in its comment.

## Test mode: no setup at all

```bash
doppel tests --base main-checkout --head pr-checkout --pr PR.md --strict
```

Doppel runs the app's **own test suite** three times (base, base again, head), with a recorder inside the test
process that captures every request the tests send through a framework test client (Flask/Werkzeug, Django/DRF,
FastAPI/Starlette) and how each test ended. Then it compares every response field by field. The tests already
know how to build the app, its database and its logged-in users, so there is nothing to set up: the test and
install commands come from the repo's `tox.ini`, CI workflow or layout. The tests' assertions catch what someone
thought to check; test mode catches everything else in the same responses, like a password hash that appears in
`/api/users` while every test still passes.

Safeguards: the base must run (no tests, a collection error or mostly failing tests stop the check with the test
runner's own output, never a verdict); base vs base again finds noisy fields and flaky tests (Diffy's
primary/secondary idea); every world gets the same clean, seeded environment; the head world runs the **base
branch's tests**, so a PR can't hide a change by editing a test. Apps without tests, or whose tests never call
what a PR changed, still have the HTTP twin. [bench/results/testmode](bench/results/testmode) has the first run.

## License

[MIT](LICENSE)
