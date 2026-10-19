# Testing Doppel end to end

This guide takes you from a fresh clone to every claim in the README, one level at a time. Each level says
what it proves, what it needs, and what you should see. Levels 0 to 2 need no keys and no network beyond
installing packages; stop wherever you have seen enough.

| Level | What you prove | Needs | Time | Cost |
|---|---|---|---|---|
| [0. Install and run the suite](#level-0-install-and-run-the-test-suite) | The code works on your machine | Python 3.11+, git | 10 min | $0 |
| [1. The twin, offline](#level-1-the-twin-offline) | Invented users find what a PR didn't announce | Level 0 | 1 min | $0 |
| [2. Test mode on a real app, offline](#level-2-test-mode-on-a-real-app-offline) | Doppel catches the password-hash leak that all 44 of the app's tests pass | Python 3.12 for the app | 10 min | $0 |
| [3. Add the judge](#level-3-add-the-judge) | Nemotron labels the leak a regression the PR doesn't mention | A Nebius key | 2 min | cents |
| [4. The full product on Nebius](#level-4-the-full-product-on-nebius) | Sandboxes, the guided twin, `doppel init` | A Nebius key and project | 10–30 min | ~$0.25 per check |
| [5. The benchmark](#level-5-the-benchmark) | The README's numbers come from the recorded results, and you can re-derive them | Levels 0 and 2; a key for 5b and 5d | 1 min to 1.5 h | $0 to ~$12 |

[For contributors](#for-contributors): the development loop, how the suite stays offline, writing a test,
debugging a run. [Troubleshooting](#troubleshooting) and [sharing a result](#share-a-result) are at the end.

Every command is given for **PowerShell** (Windows) and **bash** (macOS and Linux). The guide keeps
everything it creates in one folder next to the repo, so nothing lands inside it:

```
work/
├── doppel/          the repo (this file)
└── doppel-e2e/      everything this guide creates
```

---

## Level 0: install and run the test suite

**Proves:** Doppel installs, and its 396 offline tests pass: test mode, the twin, the judge's rules, the
setup agent and the pages, all run against small Flask, Django and FastAPI apps the tests build on the fly.

PowerShell:

```powershell
mkdir work; cd work
git clone https://github.com/Maukthik/doppel
cd doppel
py -3.12 -m venv .venv                 # any Python 3.11 or newer
.\.venv\Scripts\Activate.ps1           # if blocked: Set-ExecutionPolicy -Scope Process Bypass
pip install -e ".[nebius,dev]"
doppel --version
ruff check .
python -m pytest -q
```

bash:

```bash
mkdir work && cd work
git clone https://github.com/Maukthik/doppel
cd doppel
python3 -m venv .venv && source .venv/bin/activate      # any Python 3.11 or newer
pip install -e ".[nebius,dev]"
doppel --version
ruff check .
python -m pytest -q
```

**You should see:** `doppel 1.0.0`, `All checks passed!`, then `396 passed` after about four minutes.

The `nebius` extra is only needed from Level 3 on; installing it now saves a step. Keep this terminal and
its activated `.venv`: every later command uses it.

---

## Level 1: the twin, offline

**Proves:** the twin's core loop. Doppel starts the app's code from before and after the PR as two
worlds with the same data, sends the same users through both, and lists every response that differs.

[`examples/shop`](examples/shop) is a small shop API. Its PR ([`PR.md`](examples/shop/PR.md)) says it
only adds an `in_stock` flag. The five users in [`personas.json`](examples/shop/personas.json) browse,
buy with a coupon and order a quantity of 0.

```
doppel run --base examples/shop/base --head examples/shop/head --personas examples/shop/personas.json
```

(The same line works in PowerShell and bash; `--backend local` is the default for `doppel run`.)

**You should see**, in about three seconds:

```
## Doppel: 6 behavior changes

5 personas replayed 11 requests against base and head. Coverage: the app served 8 of 11 requests (73%) on base. Changed code reached by the users: 3 of 3 changes. 🟠 1 status change · 🟡 5 response changes

| | Persona | Request | What changed | Base | Head |
|---|---|---|---|---|---|
| 🟠 | careless clicker (step 0) | `POST /orders` | status 400 -> 201 | `400` | `201` |
| 🟡 | coupon buyer (step 0) | `POST /orders` | changed: total | `total: 371593` | `total: 377892` |
...
```

Three distinct changes: `in_stock` was added (announced), coupon orders now cost more, and an order for
quantity 0 is accepted instead of rejected (neither announced). Without `--pr` nothing is judged yet, so
all of them are listed as behavior changes; Level 3 adds the judge.

Two more things to try:

```
doppel run --base examples/shop/base --head examples/shop/head --personas examples/shop/personas.json --strict
```

exits with code **1** (changes found), which is what fails a CI check. Check it with `echo $LASTEXITCODE`
(PowerShell) or `echo $?` (bash).

```
mkdir ../doppel-e2e
doppel run --base examples/shop/base --head examples/shop/head --personas examples/shop/personas.json --json ../doppel-e2e/shop-run.json
doppel view ../doppel-e2e/shop-run.json
```

writes the full recording (every request in both worlds) and opens the visual replay of the run.

---

## Level 2: test mode on a real app, offline

**Proves:** the headline. On [microblog-api](https://github.com/miguelgrinberg/microblog-api), a real,
well-tested Flask API, the benchmark's PR r17 adds one line that puts every user's password hash in the
API's answers. All 44 of the app's tests still pass. Doppel runs those same tests on the code before and
after the PR, records every response, and finds the hash in 38 of them.

### 2.1 Get the app and the PR

The benchmark pins the app at commit `e250d71`. Clone it twice, base and head, and apply the PR to head.
`core.autocrlf=false` keeps the files' line endings as they are, so the patch applies on Windows too.
`$prs` holds the full path to the benchmark's patches (`git -C <folder>` reads relative paths from inside
that folder, so a full path avoids surprises).

PowerShell (from `work/`; skip the `mkdir` if Level 1 made the folder):

```powershell
mkdir doppel-e2e; cd doppel-e2e
git clone -c core.autocrlf=false https://github.com/miguelgrinberg/microblog-api.git mb-base
git -C mb-base checkout e250d71
git clone -c core.autocrlf=false mb-base mb-head
git -C mb-head checkout e250d71
$prs = (Resolve-Path ..\doppel\bench\microblog\prs).Path
git -C mb-head apply "$prs\r17-hash-leak.patch"
git -C mb-head diff --stat
```

bash (from `work/`):

```bash
mkdir -p doppel-e2e && cd doppel-e2e
git clone -c core.autocrlf=false https://github.com/miguelgrinberg/microblog-api.git mb-base
git -C mb-base checkout e250d71
git clone -c core.autocrlf=false mb-base mb-head
git -C mb-head checkout e250d71
prs="$(cd ../doppel/bench/microblog/prs && pwd)"
git -C mb-head apply "$prs/r17-hash-leak.patch"
git -C mb-head diff --stat
```

**You should see:** `api/schemas.py | 1 +`. That is the whole PR.

### 2.2 Give the app its own Python

With the local backend, test mode runs the app's tests with an interpreter you choose. microblog-api pins
its dependencies for Python 3.12 (`greenlet` doesn't build on 3.13+ at that pin), so make it a 3.12 venv:

PowerShell:

```powershell
py -3.12 -m venv mb-venv
.\mb-venv\Scripts\python -m pip install -r mb-base\requirements-dev.txt
cd mb-head; ..\mb-venv\Scripts\python -m pytest -q -p no:logging; cd ..
```

bash:

```bash
python3.12 -m venv mb-venv          # or: uv venv -p 3.12 mb-venv && uv pip install -p mb-venv ...
mb-venv/bin/python -m pip install -r mb-base/requirements-dev.txt
(cd mb-head && ../mb-venv/bin/python -m pytest -q -p no:logging)
```

**You should see:** `44 passed`, on the head code, with the leak in it. That is what CI would show for
this PR.

### 2.3 Run test mode

Your terminal still has Doppel's `.venv` active (if not, activate `..\doppel\.venv` again).

PowerShell:

```powershell
doppel tests --backend local --base mb-base --head mb-head --python mb-venv\Scripts\python.exe
```

bash:

```bash
doppel tests --backend local --base mb-base --head mb-head --python mb-venv/bin/python
```

**You should see**, in about 35 seconds:

```
[tests] test command: python -m pytest -p no:logging   (from tox.ini)
[tests] running `python -m pytest -p no:logging` in 3 worlds (base, base again, head)
[tests] confirming 38 difference(s): rerunning 25 test(s) twice on base and head
[tests] 44 tests passed on base; 117 requests recorded; 0 flaky, 1 with varying requests, 5 noisy field name(s); 38 difference(s)
## Doppel: 38 behavior changes
...
| 🟡 | test_auth.py::test_get_token (request 1) | `GET /api/users` | changed: data.*.password_hash (added) | `no password_hash` | `password_hash: "scrypt:32768:8:1$[hidden]"` |
```

What happened: Doppel found the test command in `tox.ini`, ran the suite on base twice (whatever differs
between those two runs, like fresh tokens and timestamps, is noise and ignored) and once on head, compared
all 117 responses, then reran the 25 tests behind the differences to confirm each one.

### 2.4 The combined check and its page

`doppel check` is what the GitHub Action runs: both modes, one report, one exit code, and the check page.

PowerShell:

```powershell
doppel check --backend local --no-twin --base mb-base --head mb-head --python mb-venv\Scripts\python.exe --strict --out check.md --json check.json
echo $LASTEXITCODE
doppel view check.json
```

bash:

```bash
doppel check --backend local --no-twin --base mb-base --head mb-head --python mb-venv/bin/python --strict --out check.md --json check.json
echo $?
doppel view check.json
```

**You should see:** `## Doppel: 1 behavior change, seen in 38 requests` (the 38 requests are grouped into
one change, on 14 endpoints), exit code `1`, and the check page opening in your browser. `check.md` is the
PR comment, exactly as it would be posted.

### 2.5 Controls: a rename, and a safe PR

A check that flags everything proves nothing. Run two more benchmark PRs the same way, each in its own
head folder (`$prs` is from 2.1; set it again in a new terminal):

| PR | What it really does | Expect |
|---|---|---|
| `r03-auth-error-field` | "Small cleanup in auth error handlers", renames `message` to `name` in every 401 | `1 behavior change, seen in 13 requests`: **Field renamed in responses: message → name**, exit 1 |
| `s02-docstrings` | Only docstrings change | `no regressions`, exit 0 |

PowerShell (repeat with `s02-docstrings`):

```powershell
$pr = "r03-auth-error-field"
git clone -c core.autocrlf=false mb-base "mb-$pr"
git -C "mb-$pr" apply "$prs\$pr.patch"
doppel check --backend local --no-twin --base mb-base --head "mb-$pr" --python mb-venv\Scripts\python.exe --strict --out "check-$pr.md"
echo $LASTEXITCODE
```

bash (repeat with `s02-docstrings`):

```bash
pr=r03-auth-error-field
git clone -c core.autocrlf=false mb-base "mb-$pr"
git -C "mb-$pr" apply "$prs/$pr.patch"
doppel check --backend local --no-twin --base mb-base --head "mb-$pr" --python mb-venv/bin/python --strict --out "check-$pr.md"
echo $?
```

All 30 benchmark PRs are in [`bench/microblog/prs`](bench/microblog/prs), with their titles, descriptions
and what each really does in `index.json`.

---

## Level 3: add the judge

**Proves:** the judge. Nemotron 3 Ultra reads the PR's description and the diff, and labels each change
*intended*, *regression* or *needs a human*. Only the judge needs a key here; the code still runs on your
machine.

1. Get a key and project id from [Nebius Token Factory](https://tokenfactory.nebius.com).
2. In the `doppel` folder, copy [`.env.example`](.env.example) to `.env` and fill in `NEBIUS_API_KEY` and
   `NEBIUS_PROJECT_ID`. `.env` is git-ignored; never commit it. Doppel finds it from any folder because it
   is installed from this checkout. (Or set the two variables in your shell instead.)
3. Write the PR's description as the author did, into `doppel-e2e/PR.md`:

   ```markdown
   # Describe has_password in the API schema

   Back `has_password` with the actual column so the OpenAPI docs describe it.
   ```

4. Run the same check with `--pr`:

PowerShell:

```powershell
doppel check --backend local --no-twin --base mb-base --head mb-head --python mb-venv\Scripts\python.exe --pr PR.md --strict --out check.md --json check.json
doppel view check.json
```

bash:

```bash
doppel check --backend local --no-twin --base mb-base --head mb-head --python mb-venv/bin/python --pr PR.md --strict --out check.md --json check.json
doppel view check.json
```

**You should see:** `## Doppel: 1 regression the PR doesn't mention, seen in 38 requests`, with
Nemotron's reason in the "Why" column (in its own words, along the lines of "the diff exposes the password
hash, which the description doesn't announce") and exit code 1.

The shop from Level 1 can be judged the same way:

```
doppel run --base examples/shop/base --head examples/shop/head --personas examples/shop/personas.json --pr examples/shop/PR.md --tests-out ../doppel-e2e/test_regressions.py
```

Expected: `in_stock` lands under *Intended*, the coupon total and the quantity-0 order under
*Regressions*, and `test_regressions.py` gets one pytest per regression.

The judge is a model: the wording of its reasons changes from run to run, and so, occasionally, can a
verdict on a borderline change. The [benchmark](#level-5-the-benchmark) measures how often.

---

## Level 4: the full product on Nebius

**Proves:** what runs on a pull request: the code in Nebius sandboxes instead of your machine, and the
guided twin (an AI reviewer lists what it suspects the PR breaks; Nemotron writes users and probes that
send the exact inputs; each suspicion ends up confirmed, refuted or untested). Needs `.env` from Level 3.
`--backend nebius` is the default for `check`, `tests`, `init` and `probe`.

### 4.1 Test mode in sandboxes

No local Python for the app is needed: the sandbox installs what `tox.ini` asks for.

```
doppel check --no-twin --base mb-base --head mb-head --pr PR.md --strict --out check.md --json check.json
```

**You should see** the same verdict as Level 3, in about a minute. This is exactly what
[Try Doppel](https://maukthik.github.io/doppel-try/) runs for any public PR.

### 4.2 The guided twin

The twin needs to know how to install, seed and start the app: a `doppel.toml` and a seed, which
`doppel init` writes (4.3). For microblog-api the benchmark's are ready in
[`bench/microblog/twin`](bench/microblog/twin). Copy them into both worlds, then run the full check:

PowerShell:

```powershell
Copy-Item ..\doppel\bench\microblog\twin\* mb-base\
Copy-Item ..\doppel\bench\microblog\twin\* mb-head\
doppel check --base mb-base --head mb-head --pr PR.md --strict --out check.md --json check.json --twin-json twin.json
doppel view check.json
doppel view twin.json
```

bash:

```bash
cp ../doppel/bench/microblog/twin/* mb-base/
cp ../doppel/bench/microblog/twin/* mb-head/
doppel check --base mb-base --head mb-head --pr PR.md --strict --out check.md --json check.json --twin-json twin.json
doppel view check.json
doppel view twin.json
```

**You should see:** both modes in the report ("Your tests" and "Invented users": 12 users written by
Nemotron, replayed in both worlds, with how many of their requests the app served), the leak found by both,
and a table of the reviewer's suspicions with what the twin found for each. It takes a few minutes; the
benchmark averaged 89 s and $0.25 per PR. `twin.json` opens as the visual replay: every user's requests in
both worlds, side by side.

To run the twin alone with fixed users (no persona writing, so it repeats more closely), use the
benchmark's three smoke users:

```
doppel run --backend nebius --base mb-base --head mb-head --personas ../doppel/bench/microblog/smoke-personas.json --pr PR.md
```

### 4.3 Set up the twin from scratch with `doppel init`

On a fresh clone without the twin files, `doppel init` reads the repo, installs and imports the app in a
sandbox to learn its routes, database and login, drafts `doppel.toml` and a seed with Nemotron, proves
them by booting the twin, and repairs them (handing over to a setup agent if drafts keep failing).

```
git clone -c core.autocrlf=false https://github.com/miguelgrinberg/microblog-api.git mb-fresh
doppel probe mb-fresh
doppel init mb-fresh --trace
doppel view mb-fresh/.doppel-trace
```

**You should see:** `doppel probe` prints what is true about the app (routes, database, login, models).
`doppel init` ends by writing `doppel.toml` and `doppel_seed.json` into `mb-fresh` with its calibration
(the share of invented users' requests the app served; under 40% is not accepted), and the trace page shows
every attempt on a five-stage rail: install, seed, start, login, users. Expect 5 to 15 minutes. Results
vary by app: [bench/results/generality](bench/results/generality) records one app where init worked and
one where it didn't.

### 4.4 On a pull request

The GitHub Action runs `doppel check` on every PR. To see it end to end on a repo of your own, the way
[doppel-demo#1](https://github.com/Maukthik/doppel-demo/pull/1) was made:

1. Create an empty public repo on GitHub and add `NEBIUS_API_KEY` and `NEBIUS_PROJECT_ID` under
   Settings → Secrets and variables → Actions.
2. From the `doppel` folder, run
   `powershell -ExecutionPolicy Bypass -File demo\setup-demo-repo.ps1 -Owner <you> -Repo <repo>`. It pushes
   microblog-api with one workflow file, plus a branch with the hash-leak change.
3. Open the pull request from that branch. The check runs in about a minute, comments on the PR and fails.

To add Doppel to any repo, the README's [Put it on your pull requests](README.md#put-it-on-your-pull-requests)
has the workflow file.

---

## Level 5: the benchmark

The README's table comes from 30 PRs to microblog-api (20 regressions, 10 safe), 10 held-out PRs written
after tuning, and 8 "consequence" PRs. How they were made and scored: [bench/README.md](bench/README.md).

### 5a. Re-derive the numbers from the recorded results (offline, one second)

```
python bench/verify_numbers.py
```

**You should see:**

```
== main: 20 regressions, 10 safe
                              caught  false alarms   missed
   App's own tests            12/20         0/10     r02-revoke-auth, r03-auth-error-field, ...
   AI code review             17/20         1/10     r02-revoke-auth, r17-hash-leak, r20-page-size
   Review-guided Doppel       17/20         2/10     r14-is-followed, r18-after-cursor, r20-page-size
   Tests + Doppel             20/20         2/10
   AI review + Doppel         19/20         3/10
   Of the 8 regressions the tests missed, Doppel caught 8.
```

followed by the held-out set (Doppel 7/7, 0/3) and the consequence set (2/3, 0/5). Each number comes from
one JSON file per PR, which also holds every recorded request and every verdict with its reason. Any other
run folder can be scored the same way: `python bench/verify_numbers.py --guided bench/results/guided-rerun`.
[bench/results/README.md](bench/results/README.md) lists every run, including the ones that went worse.

### 5b. Re-judge the recorded runs (a key, cents)

The judge can be re-run on the same recordings without running anything else. **`rejudge.py --results`
rewrites the folder it's given**, so always copy first:

PowerShell (from `doppel/`):

```powershell
Copy-Item -Recurse bench\results\guided-rerun-fixed ..\doppel-e2e\rejudge
python bench\rejudge.py --results ..\doppel-e2e\rejudge
python bench\verify_numbers.py --guided ..\doppel-e2e\rejudge
```

bash (from `doppel/`):

```bash
cp -r bench/results/guided-rerun-fixed ../doppel-e2e/rejudge
python bench/rejudge.py --results ../doppel-e2e/rejudge
python bench/verify_numbers.py --guided ../doppel-e2e/rejudge
```

**You should see** one line per PR (`[rejudge] regression main r17-hash-leak CAUGHT`), then the table.
With today's judge this gave 17/20 with 2/10 false alarms three times in a row; a model can still differ
on a borderline verdict.

### 5c. Re-run the arms that need no key (offline)

[`bench/run_bench.py`](bench/run_bench.py) rebuilds each PR from the pinned app and its patch, then runs
the arms you ask for. The `tests` arm (the app's own suite) and the `testmode` arm without the judge need no
key. **Always pass `--out`**: the default is `bench/results`, which holds the published results.

PowerShell (from `doppel/`):

```powershell
python bench\run_bench.py --arms tests,testmode --no-judge --backend local --only r17-hash-leak,s02-docstrings --out ..\doppel-e2e\bench-out --app-python ..\doppel-e2e\mb-venv\Scripts\python.exe
```

bash (from `doppel/`):

```bash
python bench/run_bench.py --arms tests,testmode --no-judge --backend local --only r17-hash-leak,s02-docstrings --out ../doppel-e2e/bench-out --app-python ../doppel-e2e/mb-venv/bin/python
```

**You should see:**

```
[bench] upstream suite on base: 44 passed in 10.9s
[bench] regression r17-hash-leak            tests=- testmode_nojudge=CAUGHT
[bench] safe       s02-docstrings           tests=- testmode_nojudge=-
```

The app's tests miss the leak (`tests=-`); test mode catches it and leaves the safe PR alone. Drop `--only`
for all 30 PRs (about 20 minutes). The script clones microblog-api into `bench/.cache` (git-ignored), and
skips arms that already have a result in the `--out` folder unless you pass `--force`.

### 5d. Re-run review-guided Doppel (a key, ~1.5 hours, ~$12)

```
python bench/run_bench.py --arms doppel --guided --set all --out ../doppel-e2e/bench-full
python bench/verify_numbers.py --guided ../doppel-e2e/bench-full
```

This writes new users and probes for all 48 PRs, so it is a fresh sample, not a replay: complete runs have
scored 15 to 17 of 20. An interrupted run resumes where it stopped.

Don't run `bench/summarize.py` unless you mean to: it rewrites `bench/results/results.md` and `chart.svg`.

### Other harnesses

| Script | What it runs | Writes to |
|---|---|---|
| [`bench/fresh_apps.py`](bench/fresh_apps.py) | Zero-setup test mode on 30 real apps it has never seen (`run --set dev\|heldout\|round2`) | `bench/results/fresh`, `heldout`, `round2` |
| [`bench/bugsinpy.py`](bench/bugsinpy.py) | 37 BugsInPy bugs, each replayed as the PR that brings it back | `bench/results/bugsinpy` |
| [`bench/real_fixes.py`](bench/real_fixes.py) | 14 recent real fixes, reverted | `bench/results/fixes` |

They all need a key, and they write into `bench/results`: run them on a branch, so `git diff` shows what
changed and `git checkout bench/results` undoes it.

---

## For contributors

### The loop

```
ruff check .
python -m pytest -q                              # everything, ~4 min
python -m pytest -q tests/test_presentation.py   # one file
python -m pytest -q -k "rename"                  # tests whose name matches
```

CI ([`tests.yml`](.github/workflows/tests.yml)) runs the same on Python 3.11, 3.12 and 3.13.

### How the suite stays offline

- **No sandboxes:** every mode has a `local` backend that runs the same flow as local processes
  ([`backends/local.py`](src/doppel/backends/local.py) for the twin, `LocalSession` in
  [`traffic.py`](src/doppel/traffic.py) for test mode). Tests build small apps on the fly; see `flask_repo`
  in [`tests/test_traffic.py`](tests/test_traffic.py).
- **No models:** every function that calls Nemotron takes a `chat=` argument; tests pass a fake that returns
  canned JSON. Where the call is deep inside the CLI, tests patch the default:
  `monkeypatch.setattr("doppel.judge.judge.__defaults__", (None, fake))`.

### Writing a test

Pin behavior, not wording you might change. A test that runs a real check, offline:

```python
from doppel import cli
from tests.test_traffic import FLASK_APP, flask_repo

LEAKY = FLASK_APP.replace('"name": u["name"],', '"name": u["name"], "secret": u["secret"],')


def test_a_new_field_is_one_change(tmp_path, capsys):
    base, head = flask_repo(tmp_path / "b"), flask_repo(tmp_path / "h", LEAKY)
    code = cli.main(["check", "--backend", "local", "--no-twin", "--base", str(base), "--head", str(head), "--strict"])
    assert code == 1
    assert "Responses now include secret" in capsys.readouterr().out
```

Each fix in the [changelog](CHANGELOG.md) is pinned by a test that fails without it
([`tests/test_hardening.py`](tests/test_hardening.py) and [`tests/test_presentation.py`](tests/test_presentation.py)
are good examples).

### Debugging a run

| To see | Use |
|---|---|
| What Doppel learns about an app | `doppel probe <app> --backend local` (or `nebius`); `--json` for the raw facts |
| Why `doppel init` failed | `doppel init <app> --trace`, then `doppel view <app>/.doppel-trace` |
| Every request and verdict of a check | `--json check.json`, then `doppel view check.json` |
| Every step of every twin user | `--twin-json twin.json` (or `doppel run --json run.json`), then `doppel view` |
| The full log of a check in CI | `doppel.log` in the run's artifacts |
| The test command test mode picked | The first `[tests]` line; override with `--command` and `--install` |

Slow sandbox calls: raise `DOPPEL_HTTP_TIMEOUT` (seconds, default 180). Other models: `JUDGE_MODEL`,
`PERSONA_MODEL` and the rest in [the README](README.md#environment-variables-and-models).

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Activate.ps1 cannot be loaded because running scripts is disabled` | `Set-ExecutionPolicy -Scope Process Bypass`, then activate again |
| `greenlet` fails to build while installing microblog-api's requirements | That pin needs Python 3.12: make `mb-venv` with `py -3.12` / `python3.12` |
| `git apply`: `patch does not apply` | The clone converted line endings. Re-clone with `-c core.autocrlf=false` |
| `44 passed` on base fails, or many tests return 401 | The app's dependencies aren't the pinned ones: install `requirements-dev.txt` into a fresh 3.12 venv |
| Test mode says `couldn't compare` | Read the reason under it. Usually `--python` points at the wrong interpreter, or the tests fail on base too |
| `No module named 'openai'` with `--pr` | Install the extra: `pip install -e ".[nebius,dev]"` |
| `NEBIUS_API_KEY` not set | Put `.env` in the `doppel` folder (installed with `-e`), or set the variables in your shell |
| The twin on microblog-api fails locally on Windows | Its `doppel.toml` starts the app with gunicorn, which doesn't run on Windows. Use `--backend nebius` (Level 4), or the shop (Level 1), which runs anywhere |
| `doppel view` opens nothing | Add `--no-open` and open the `.html` it wrote |
| A Nebius call times out or is throttled | Run again; set `DOPPEL_HTTP_TIMEOUT=300` |
| `bench/results` changed after a benchmark script | You ran without `--out`, or ran `summarize.py`/`rejudge.py` on the published folder: `git checkout bench/results` |

---

## Share a result

To show someone what you saw, or to report a problem, include:

1. The command and the output of `doppel --version`, your OS and Python version.
2. `check.md` (the comment) and the page from `doppel view check.json --public`. `--public` masks secrets,
   tokens and password hashes, so the page is safe to post.
3. For a failure: the full terminal output, and the `.doppel-trace` folder for `doppel init`.

Open an issue at [github.com/Maukthik/doppel/issues](https://github.com/Maukthik/doppel/issues). For a PR
to any public repo, [Try Doppel](https://maukthik.github.io/doppel-try/) runs Level 4.1 for you and posts
the result as a public issue anyone can open.
