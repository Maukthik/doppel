<div align="center">

# Doppel

**Tests check what someone thought of. Reviewers read the diff. Doppel runs it.**

A GitHub check for Python web APIs (Flask, Django, FastAPI). It runs every pull request against the main
branch in Nebius sandboxes, sends the same requests through both, and proves each regression with the
request, the old answer and the new answer.

[![tests](https://github.com/Maukthik/doppel/actions/workflows/tests.yml/badge.svg)](https://github.com/Maukthik/doppel/actions/workflows/tests.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Nebius Token Factory](https://img.shields.io/badge/sandboxes-Nebius%20Token%20Factory-4b3bff)
![NVIDIA Nemotron](https://img.shields.io/badge/models-NVIDIA%20Nemotron-76b900)

[Quickstart](#put-it-on-your-pull-requests) · [Live demo PR](https://github.com/Maukthik/doppel-demo/pull/1) · [How it works](#how-it-works) · [Results](bench/results/README.md) · [Replay site](https://maukthik.github.io/doppel/) · [Changelog](CHANGELOG.md)

*Nebius x NVIDIA Global AI Hackathon · Coding and Agentic Engineering track*

</div>

<p align="center">
  <a href="https://github.com/Maukthik/doppel-demo/pull/1"><img src="docs/demo-checks.png" alt="The pull request's checks: tests pass, doppel fails" width="49%"></a>
  <a href="https://github.com/Maukthik/doppel-demo/pull/1"><img src="docs/demo-comment.png" alt="Doppel's comment: 1 regression the PR doesn't mention, password_hash added on 14 endpoints, seen in 38 requests" width="49%"></a>
</p>
<p align="center"><sub>A live pull request on a fresh repo, zero setup: all 44 tests pass, Doppel fails the check and shows the leak in 38 requests. <a href="https://github.com/Maukthik/doppel-demo/pull/1">Open the PR</a></sub></p>

## One line leaked every password hash

A pull request titled *"Describe has_password in the API schema"* changed one line in
[microblog-api](https://github.com/miguelgrinberg/microblog-api), a well-tested open-source Flask app.

| | Verdict |
|---|---|
| The app's 44 tests | ✅ all pass |
| AI code review (Nemotron 3 Ultra, full source + diff) | ✅ "safe" |
| **Doppel** | ❌ **regression**: `GET /api/users` answers with `"password_hash": "scrypt:32768:8:1$…"` on every user object |

The tests never looked at that field, and the reviewer didn't notice what one schema line does. Doppel
didn't need to notice anything. It ran both versions, saw the field appear and showed the evidence.

You can open this one: [doppel-demo#1](https://github.com/Maukthik/doppel-demo/pull/1) is that PR on a fresh
repo with one workflow file and no other setup. Doppel ran the app's own tests on both versions in about a
minute and flagged the same 38 requests on two runs ([recorded results](bench/results/demo-pr)).

## Put it on your pull requests

1. **Get a Nebius Token Factory API key.** On the [API keys page](https://tokenfactory.nebius.com/project/api-keys),
   create a key in the project that will run the sandboxes. Save it right away: it can't be shown again.
2. **Copy the project ID** from **Project settings** in Token Factory, under the project name.
3. **Add both to your repo as secrets**: *Settings → Secrets and variables → Actions → New repository secret*,
   named `NEBIUS_API_KEY` and `NEBIUS_PROJECT_ID`.
4. **Add the workflow** as `.github/workflows/doppel.yml`:

   ```yaml
   name: doppel
   on: pull_request
   jobs:
     doppel:
       runs-on: ubuntu-latest
       permissions:
         contents: write        # only to publish the check page (see "Data and permissions")
         pull-requests: write   # to post the comment
       steps:
         - uses: Maukthik/doppel@v1
           with:
             nebius-api-key: ${{ secrets.NEBIUS_API_KEY }}
             nebius-project-id: ${{ secrets.NEBIUS_PROJECT_ID }}
   ```

5. **Open a pull request.** Test mode needs nothing else: Doppel finds the test command and the install in
   `tox.ini`, your CI workflow, a Makefile or a noxfile. With a `doppel.toml` ([below](#setting-up-the-twin)),
   the guided twin runs too.

If the key works but no sandbox starts, the project may not have Sandboxes enabled yet:
[Nebius's access guide](https://docs.tokenfactory.nebius.com/sandboxes/start/set-up-access) shows how to check.

### What you get on every pull request

- **One comment, updated on each push.** The regressions the PR doesn't mention come first, each with the
  request, the old value, the new value and the reason. Then what needs a person, then the reviewer's
  suspicions as the twin checked them. Changes the PR announces are folded away.
- **A check that fails on a regression.** If a flagged change is what you want, a maintainer adds the label
  `doppel: accepted` and re-runs the check: it passes, and the comment keeps the findings. Adding labels
  takes triage access, so an outside contributor can't wave their own PR through, and the PR's timeline shows
  who added it.
- **No false red when Doppel can't run.** If the app won't install or no test runs, the check passes with a
  warning and the comment says why (`fail-on-error: true` makes it fail instead). A pull request that breaks
  the app is different: a head that no longer starts counts as a regression.
- **A check page** with every request behind every finding, on GitHub Pages or in the run's artifacts.

[`examples/demo-repo`](examples/demo-repo) sets up a repo where you can watch a red comment turn green.

| Input | Default | What it does |
|---|---|---|
| `nebius-api-key`, `nebius-project-id` | (required) | Your Token Factory key and project ID, from repository secrets |
| `app-dir` | `.` | Folder holding `doppel.toml` |
| `twin` | `auto` | `auto` also runs the guided twin when there's a `doppel.toml`; `off` = test mode only |
| `guided` | `true` | AI reviewer suspicions become probe personas |
| `personas` | `doppel-personas.json` | Personas file, **read from the base branch**; if missing, Nemotron writes `personas-count` (12) |
| `judge` | `true` | Label each change intended / regression against the PR description |
| `publish-page` | `true` | Publish the check page to `gh-pages` and link it from the comment |
| `fail-on-regression` | `true` | Fail the check when the judge finds a regression |
| `accept-label` | `doppel: accepted` | The label a maintainer adds (then re-runs the check) to accept the findings |
| `fail-on-error` | `false` | `true` fails the check when Doppel couldn't compare the two versions |

## How it works

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/doppel-architecture-dark.svg">
  <img src="docs/doppel-architecture-light.svg" alt="Doppel architecture: the main branch is seeded once into a twin of the app in Nebius sandboxes, forked into a base world and a head world (with the PR's code); the same users (your tests plus Nemotron-written users) run in both; every response is compared; a Nemotron judge asks whether the PR meant each change, and regressions go to the PR comment and check. On 30 PRs to a real app: tests 12/20, AI review 17/20, review-guided Doppel 16/20 with 2/10 false alarms, tests + Doppel 19/20." width="100%">
</picture>

1. **Build the twin once.** In a Nebius sandbox, Doppel installs the app, loads its starting data and saves a checkpoint.
2. **Fork two worlds.** The base world is the main branch. The head world is the same checkpoint with the PR's files laid on top, then installed and migrated again. Both start from identical data, so any difference comes from the code change.
3. **Write the users.** Nemotron 3.5 Lightning reads the app and the diff and writes personas: ordinary users, power users and careless users who send zero, negative, empty or unknown values. In **review-guided mode**, Nemotron 3 Ultra first lists the regressions it suspects, with the exact inputs that would show each one, and Doppel writes a probe persona to test it.
4. **Replay in isolation.** Every persona runs in its own throwaway fork of each world, all in parallel, so one user's order or deleted post can't leak into another user's results. Personas and probes that get stuck (a failed login, a route that doesn't exist) are rehearsed on the base world and repaired first.
5. **Compare.** Doppel lines up every request in the two worlds and flags new errors, status changes, changed values (naming the exact field) and large slowdowns. It ignores fields such as timestamps that differ even when the same code runs twice.
6. **Judge and report.** Nemotron 3 Ultra labels each distinct difference as *intended*, *regression* or *needs a human*. It checks each one against the PR description, never the diff alone. Each suspicion ends up **confirmed**, **refuted** (only by a probe that really sent the triggering input) or **untested**, with the recorded requests as evidence. Doppel posts one PR comment, writes a pytest for each regression, and fails the check.

**The reviewer guesses, the twin proves.** The AI reviewer is good at suspecting and Doppel is good at proving.
Review-guided mode combines them.

### Two modes, one check

| | **Test mode** | **Guided twin** |
|---|---|---|
| Who the users are | Your own test suite, recorded request by request | Personas and probes written by Nemotron |
| Setup | None: the commands come from `tox.ini`, your CI workflow, a Makefile or a noxfile | A `doppel.toml`, written by `doppel init` |
| Catches | Everything that changed in responses your tests already produce, including fields no assertion checks | Paths no test calls at all |
| Frameworks | Flask/Werkzeug, Django (+ DRF), FastAPI/Starlette, live-server tests via `requests`/`httpx` | Any HTTP + JSON app |

`doppel check` runs both in parallel and merges them into one report. When a mode can't run, the report
says why; it never reads as "no regressions".

## Trust and data

### A PR can't game its own check

- **Test mode runs the base branch's tests on the head**, so editing a test can't hide a change.
- **`[diff] ignore` and the personas come from the base branch**, so a PR can't add the fields it changes to the ignore list or choose its own testers.
- **The description can't excuse a secret.** A change to a field that looks like it holds one (`password_hash`, `apiKey`, `refresh_token`) always goes to a person, even when the PR description announces it.
- **Every difference is confirmed** by rerunning the tests involved on base and head. One that doesn't happen again is dropped as noise.
- **A pass has to mean something.** If the head no longer starts, that counts as a regression. If Doppel couldn't compare at all, the comment says "couldn't check this PR", never "no regressions" (the CLI exits with 2; the Action warns, or fails with `fail-on-error: true`). If a suspicion couldn't be tested, the comment says so plainly. Each report states its coverage ("the app served X of Y requests").
- **A PR that edits Doppel's own setup** (`doppel.toml`, the seed or the personas) gets a warning in its comment.

### Data and permissions

- **Where your code runs.** Doppel uploads the base and head code to Nebius Token Factory sandboxes, where it is installed and run. On the GitHub runner, Doppel only reads it.
- **What the models see.** The PR description, the diff, the app's source and the recorded responses go to NVIDIA Nemotron models on Nebius Token Factory, to write users, list suspicions and judge changes. Apart from GitHub itself, nothing is sent anywhere else; how Nebius handles the data is set by its Token Factory terms.
- **Permissions.** `pull-requests: write` posts the comment and reads the PR's labels. `contents: write` is only for publishing the check page to the `gh-pages` branch: with `publish-page: false` you can use `contents: read`, and the page stays in the run's artifacts.
- **Secrets.** The key reaches only the step that runs Doppel. When a comment has to quote Doppel's own log, the key and project ID are replaced with `***` first, since a comment isn't masked the way the job log is. The check page masks credentials, tokens and password hashes found in responses.

## Results

### 30 pull requests to a real app

20 PRs carry a regression (3 of them are real upstream bug fixes, reverted) and 10 are safe.
Each PR has a description written the way authors write them: *"simplify the feed query"*, *"cheaper count for pagination"*.

| | Regressions caught | False alarms on safe PRs | Cost / PR | Time / PR |
|---|---|---|---|---|
| App's own test suite (44 tests) | 12/20 | 0/10 | $0 | 11 s |
| AI code review (Nemotron 3 Ultra) | 17/20 | 1/10 | $0.03 | 3 s |
| **Review-guided Doppel** | **16/20** | **2/10** | $0.25 | 89 s |
| **Tests + Doppel** | **19/20** | 2/10 | | |
| AI review + Doppel | 19/20 | 3/10 | | |

These methods catch **different** bugs. Doppel caught 7 of the 8 regressions the test suite missed, including
the two the AI reviewer called safe: the hash leak, and a token-revoke request that crashes with a 500.

The Doppel row is the latest run (17 Oct). 2 of 10 safe PRs were flagged: s09, because account creation now
accepts the new `location` field, which the description doesn't mention; and s07, a judge miss on a knock-on
effect of the announced change. Disclosed: one judge rule was added after that run (a route the PR adds can't
regress, since it didn't exist before), and the numbers are the same recordings judged again; another change
was tried and dropped because it lost a real catch ([rejudged](bench/results/guided-rerun-fixed),
[dropped trial](bench/results/guided-rerun-knockon-trial)). The secret-field rule above was added later still
and changes none of these numbers. Complete runs have scored 15 to 17 of 20, so treat each number as one
sample. What doesn't vary: **every catch and every false alarm comes with the recorded requests**. On a
held-out set of 10 PRs written after tuning: tests 6/7, AI review 5/7, Doppel 7/7 with 0/3 false alarms.

### Beyond the benchmark

**Zero setup on real apps.** Test mode was run on 30 open-source Python web apps (Flask, Django and FastAPI)
pinned to fixed commits, with nothing configured by hand. The predictions and the run rules were committed
before each set ran.

| App set | How it was used | Works | False differences |
|---|---|---|---|
| [Dev set](bench/results/fresh-rerun) (ctfd, wger, paperless-ngx, label-studio, mealie…) | Gaps were fixed against these apps: 1/10 before, 8/10 after | 8/10 | 0 |
| [Held-out, round 1](bench/results/heldout) (pretalx, tandoor, ihatemoney…) | Run once, then fixed: 3/10, then [5/10](bench/results/heldout-rerun) on rerun | 3/10 → 5/10 | 0 |
| [Held-out, round 2](bench/results/round2) (prefect, privacyidea, fava, isso…) | **First sight**, predicted 3–6 | **4/10** | **0** |

Where it works, it ran up to 10,214 tests and recorded thousands of requests per app without a single false
difference. Every stop gives a readable reason, usually that the tests didn't run, the install failed, or the
tests need PostgreSQL or another service. A stop never turns into a verdict.

**Test mode on the benchmark (raw, before judging).** 21 of the 27 regressions changed a response the suite
already sends. In 3 of those, every test still passed (the hash leak, a renamed error field, a field that
disappeared). The other 6 were never requested by the suite. The guided twin caught 5 of those 6
([details](bench/results/testmode)).

**Real bugs, replayed.** We took real fixes from BugsInPy (FastAPI, Tornado) and from fava, isso, privacyidea
and prefect, and replayed each one as the PR that brings its bug back. Test mode caught 2 of 15 runnable
BugsInPy bugs. It caught 0 of 14 recent real fixes and raised 0 false alarms. In nearly every case, **the
test suite never sent a request through the buggy code**, so there was nothing to compare. This is test
mode's ceiling, and it's why the guided twin exists: it writes the requests no test sends
([BugsInPy](bench/results/bugsinpy), [real fixes](bench/results/fixes)). The guided twin, with a setup written
by hand, **did catch isso's stored-XSS fix**: probes sent `"quoted"` and `<script>` through `POST /new` and
`PUT /id/<id>`, and the head returned them unescaped. That came on the fourth run of that PR, after fixes the
earlier runs prompted ([write-up, all four runs](bench/results/fixes/twin)).

**`doppel init` with no hand edits.** It set up microblog-api (Flask) and linkding (Django). On linkding it
found the same 3 noisy fields as the hand-written answer key ([bench/init](bench/init)).

Every folder of results, with its date and headline number: [bench/results/README.md](bench/results/README.md).

## Try it in one minute (offline, no keys)

```bash
git clone https://github.com/Maukthik/doppel && cd doppel
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
doppel run --base examples/shop/base --head examples/shop/head --personas examples/shop/personas.json
```

The shop's PR ([`examples/shop/PR.md`](examples/shop/PR.md)) says it only adds an `in_stock` flag. Doppel
finds two more changes the PR doesn't mention: coupon orders now cost more, and an order for quantity 0 is
accepted instead of rejected.

With a Nebius key in `.env` (copy [`.env.example`](.env.example)), the same run happens in sandboxes, with
invented users and the judge:

```bash
pip install -e ".[nebius,dev]"
doppel run --backend nebius --base examples/shop/base --head examples/shop/head \
  --personas examples/shop/personas.json --pr examples/shop/PR.md --tests-out test_regressions.py
```

To use the CLI without cloning: `pip install "doppel-check[nebius] @ git+https://github.com/Maukthik/doppel@v1"`.
The package is named `doppel-check` because `doppel` on PyPI is an unrelated project; the command is `doppel`.

## Setting up the twin

```bash
cd your-app
doppel init      # writes doppel.toml + doppel_seed.json, proven in a sandbox
doppel probe     # just print what Doppel learns: routes, database, login, models
```

`doppel init` doesn't guess:

- **It asks the app.** It installs and imports the app the way its framework does, then reads the real routes, the database, the login view, the required fields and any import error.
- **It seeds through the front door.** `doppel_seed.json` holds ordinary HTTP steps (sign up, log in, create records) that run once before the checkpoint, so the app itself hashes passwords and fills required fields.
- **It brings in an agent when drafts fail.** Nemotron 3 Ultra drafts the files. If they don't work, a setup agent takes over in a live sandbox with `shell`, `read`, `serve` and `try` tools, for up to 20 steps.
- **It calibrates noise.** It replays invented users against the same code twice, and any field that differs goes into `[diff] ignore`.

`--trace` saves every attempt, and `doppel view .doppel-trace` turns the trace into a web page.
`--check` tests a hand-written setup. The format is:

```toml
[app]
image   = "python:3.12-slim"
install = "pip install -r requirements.txt"
start   = "python app.py"          # must listen on $PORT
ready   = "/health"
seed    = "python seed.py"         # runs once; both worlds start from this data
migrate = ""                       # runs in each world after its code is in place
env     = { DATABASE_URL = "sqlite:///twin.db" }

[personas]
guide = "Log in with HTTP Basic as alice / pw, then send the token as Bearer."

[diff]
ignore      = ["created_at"]       # read from the base branch
slow_ratio  = 2.0
slow_min_ms = 50
```

## Commands

| Command | What it does |
|---|---|
| `doppel check` | Both modes in parallel, one report. Exit 0 = clean, 1 = regressions (`--strict`), 2 = couldn't compare |
| `doppel tests` | Test mode only: base, base again and head, compared response by response |
| `doppel run` | Guided twin only: replay personas on base and head, diff, judge |
| `doppel personas` | Have Nemotron write personas for an app and a diff |
| `doppel init` / `doppel probe` | Generate and prove the twin setup / print what Doppel learns about the app |
| `doppel view` | Turn a run or an init trace into one web page |
| `doppel site` | Build the [replay site](https://maukthik.github.io/doppel/) from recorded runs (credentials masked; the build fails if any would leak) |
| `doppel comment` / `doppel accepted` | Post or update the PR comment / exit 0 if a maintainer put the accept label on the PR (both used by the Action) |

## Built on

| | Role |
|---|---|
| **Nebius Token Factory Sandboxes** (ConTree) | Checkpoint a running app in ~2 s and fork a throwaway copy in ~1.4 s, dozens in parallel. This is what makes a copy per user per world affordable on every PR |
| **NVIDIA Nemotron 3.5 Lightning** | Writes personas and probes. Its long context reads a whole small app at once |
| **NVIDIA Nemotron 3 Ultra** | Reviewer (lists suspicions) and judge (intended vs regression), with reasoning off and JSON output, about $0.001–0.002 per judgment |

No model is fine-tuned.

## How it compares

Doppel isn't a new idea from scratch. Its novelty is the combination, running automatically on every PR.

| | Does well | What Doppel adds |
|---|---|---|
| Traffic replay ([Diffy](https://github.com/opendiffy/diffy), [Keploy](https://github.com/keploy/keploy), Speedscale) | Compare versions using recorded production traffic | No production traffic needed: AI writes the users. The whole app and its data are forked per user, and an AI separates intended changes from bugs |
| AI code review (Claude Code, Copilot, CodeRabbit) | Spots suspicious code quickly and cheaply | Runs the code and shows old vs new values. It refutes false suspicions too |
| Your test suite | Checks what someone thought to check | Compares every field of every response the tests already produce, then explores what they never call |

Doppel is not a simulation or a forecast. Every finding is a real request answered by real code, recorded in both worlds.

## Limits

- **It only sees what is exercised.** A bug on a path no test or persona reaches stays hidden. Coverage is the main lever (see the real-bug results above).
- **It compares two versions; it doesn't know the truth.** If old and new are wrong in the same way, there's no difference to find.
- **The judge can be wrong, both ways.** It depends on the PR description. On the benchmark it flagged 2 of 10 safe PRs, and it has let regressions through as "intended". That is why "needs a human" exists and the evidence is always shown.
- **A probe can test the right code without being able to show the result.** A refutation must name a value a probe really sent, but on isso the email probe sent a quote and was refuted although isso never returns email addresses, so no response could have differed.
- **It covers HTTP + JSON APIs in Python.** Browser UIs, background jobs and apps that need PostgreSQL or Redis inside the sandbox are not covered yet.
- **PRs from forks don't get secrets on GitHub**, so Doppel runs on branches inside the repo.
- **The benchmark is one app with PRs we wrote**, plus 3 reverted real fixes. The PR list was frozen before any method ran on it. That's evidence, not proof, and the harness reruns on any app.

## Roadmap

- **Pull requests from forks.** GitHub gives them no secrets, so today Doppel checks branches inside the repo. A GitHub App would check every PR with nothing stored in the repo.
- **More of the stack.** Tests that need PostgreSQL or another service (5 of the 13 apps that stopped in the zero-setup runs), and languages beyond Python.
- **Setup as a pull request.** `doppel init` would open a PR with the `doppel.toml` it wrote, so the twin's setup gets reviewed like any other change.
- **The same verdict for the same finding.** Keep a finding's verdict across pushes unless the finding itself changes, so a re-run can't flip it.
- **Memory.** Remember what maintainers accepted, and show history across pull requests.

## Develop

```bash
pip install -e ".[dev]" && ruff check . && python -m pytest -q    # offline: no keys, no network
```

[Changelog](CHANGELOG.md) · build history: [docs/STAGES.md](docs/STAGES.md)

## License

[MIT](LICENSE)
