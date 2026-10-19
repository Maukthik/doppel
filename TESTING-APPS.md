# Testing Doppel on apps it has never seen

[TESTING.md](TESTING.md) proves Doppel on microblog-api, the app its benchmark was built on. This guide does
the harder test: five open-source Python web apps Doppel was never tuned for, from a 70-test Flask service
to a 2,266-test Django library. For each app you run three checks:

1. **A planted regression.** One small edit that changes what the app answers, while every one of the
   app's own tests still passes. Doppel should find it.
2. **A safe change.** A docstring edit. Doppel should say "no regressions". A tool that flags everything
   proves nothing.
3. **A real merged pull request** from the app's history, compared the way a reviewer would have seen it.

Everything here was run on 19 Oct 2026, at the commits pinned below, and the outputs shown are from those
runs. Running them also found eight gaps in Doppel, all fixed and listed [at the end](#what-these-apps-taught-doppel).

| App | Framework | Tests | Planted regression | Safe change | Real PR | Local time per check |
|---|---|---|---|---|---|---|
| [httpbin](#1-httpbin-flask) | Flask | 70 | Caught: `origin` dropped from `GET /get` (9 requests) | No regressions | #66: no regressions | ~15 s |
| [flasky](#2-flasky-flask-old-pins) | Flask, 2017 pins | 34 | Caught: `author_email` added to every post (5 requests) | No regressions | Chapter 16a: no regressions | ~15 s |
| [django-allauth](#3-django-allauth-django) | Django | 2,266 | Caught: `track_activity` flipped in the config endpoints (2 requests) | No regressions | IdP key rotation: no regressions | 3–7 min |
| [Kinto](#4-kinto-pyramid--webtest) | Pyramid + WebTest | ~2,040 | Caught: `storage_backend` exposed on the root endpoint (94 requests) | No regressions | #3793: no regressions | 6–7 min |
| [Datasette](#5-datasette-asgi) | ASGI | ~2,800 | Caught: `uvicorn` dropped from `GET /-/versions.json` (28 requests) | No regressions | #2951: no regressions | ~9 min |

---

## How it works, in one paragraph

Each check needs two copies of the app: **base** (the code before the change) and **head** (the code with
it). Doppel runs the app's own tests on base twice and on head once, records every response the tests get
back, ignores whatever differs between the two base runs (tokens, timestamps), and reports every other
difference. Without `--pr`, every difference is listed as a *behavior change*; with `--pr PR.md` and a
Nebius key, Nemotron judges each one against the description ([TESTING.md, Level 3](TESTING.md#level-3-add-the-judge)).

**Local or Nebius.** With `--backend local`, the tests run on your machine, so each app needs its own Python
environment (that's most of the setup below). With `--backend nebius` (the default for `doppel check`), the
tests run in sandboxes that install the app themselves; every app section ends with that one command.

---

## 0. Set up once

You need Doppel installed as in [TESTING.md, Level 0](TESTING.md#level-0-install-and-run-the-test-suite), and
a folder for the apps next to the repo:

```
work/
├── doppel/        the repo, with its .venv
└── apps/          everything this guide creates
```

PowerShell:

```powershell
cd work
mkdir apps; cd apps
..\doppel\.venv\Scripts\Activate.ps1
doppel --version
```

bash:

```bash
cd work
mkdir -p apps && cd apps
source ../doppel/.venv/bin/activate
doppel --version
```

Keep this terminal: every command below runs from `work/apps` with Doppel's venv active. Each app gets its
**own** venv for its own dependencies (`hb-venv`, `fl-venv`, …). You never activate those; Doppel is told
where its Python is with `--python`.

**Three rules that apply to every app:**

- **Clone with `-c core.autocrlf=false`**, so line endings stay as the repo has them on Windows too.
- **Install the app editable from the base folder** (`pip install -e`). Doppel runs `python -m pytest` inside
  each copy, and Python then imports that copy's own code first, so base runs base and head runs head. Some
  apps also read their own installed version at import time (httpbin does), which needs the install.
- **Check out the pinned commit.** The results below are for those commits; a newer one may differ.

**Making a change.** Each planted change is a one- or two-line edit: open the file in any editor, find the
line shown under "before", and change it to "after". Then `git -C <head> diff` should show exactly that.

---

## 1. httpbin (Flask)

[psf/httpbin](https://github.com/psf/httpbin): the HTTP request-and-response service. Small, fast, a good
first app. Tests: `pytest tests` from `tox.ini`.

### Get it

PowerShell:

```powershell
git clone -c core.autocrlf=false https://github.com/psf/httpbin.git hb-base
git -C hb-base checkout f7b02ae
git clone -c core.autocrlf=false hb-base hb-head
py -3.12 -m venv hb-venv
.\hb-venv\Scripts\python -m pip install -e ".\hb-base[test]"
cd hb-base; ..\hb-venv\Scripts\python -m pytest -q tests; cd ..
```

bash:

```bash
git clone -c core.autocrlf=false https://github.com/psf/httpbin.git hb-base
git -C hb-base checkout f7b02ae
git clone -c core.autocrlf=false hb-base hb-head
python3.12 -m venv hb-venv
hb-venv/bin/python -m pip install -e "./hb-base[test]"
(cd hb-base && ../hb-venv/bin/python -m pytest -q tests)
```

**Expect:** `70 passed`. (Python 3.11 to 3.14 all work.)

### Planted regression: `GET /get` stops returning `origin`

In `hb-head/httpbin/core.py`, in `view_get`:

```python
# before
    return jsonify(get_dict("url", "args", "headers", "origin"))
# after
    return jsonify(get_dict("url", "args", "headers"))
```

Clients that read the caller's IP from `/get` break; no test asserts it, so all 70 still pass. Run:

PowerShell:

```powershell
doppel check --backend local --no-twin --base hb-base --head hb-head --python hb-venv\Scripts\python.exe --strict --out hb.md --json hb.json
```

bash:

```bash
doppel check --backend local --no-twin --base hb-base --head hb-head --python hb-venv/bin/python --strict --out hb.md --json hb.json
```

**Expect** (about 15 seconds; exit code 1):

```
[tests] test command: python -m pytest tests   (from tox.ini)
[tests] 70 tests passed on base; 505 requests recorded; 0 flaky, 0 with varying requests, 0 noisy field name(s); 9 difference(s)
## Doppel: 1 behavior change, seen in 9 requests
| 1 | Responses no longer include origin | `GET /etag/abc`<br>`GET /get` | 9 requests from 9 tests | `origin: "127.0.0.1"` → `no origin` |
```

`doppel view hb.json` opens the check page.

**Worth trying:** change `view_origin` instead (`jsonify(origin=...)` → `jsonify(ip=...)`, the `GET /ip`
endpoint). Doppel reports **no regressions**, correctly for test mode: no test ever calls `/ip`, so there
is no response to compare. That's the gap the guided twin's invented users exist for
([TESTING.md, Level 4.2](TESTING.md#42-the-guided-twin)).

### Safe change

Undo the edit (`git -C hb-head checkout .`), then change only the docstring of `view_origin`:
`"""Returns the requester's IP Address.` → `"""Returns the IP address of whoever sent the request.`
Run the same command. **Expect:** `## Doppel: no regressions`, exit code 0.

### Real PR: #66 "Fix invalid escape sequence SyntaxWarnings"

httpbin merges with merge commits, so the PR's base is where its branch started (`07717168`) and its head is
the branch's last commit (`4e445e00`).

PowerShell:

```powershell
git clone -c core.autocrlf=false hb-base hb-pr-base; git -C hb-pr-base checkout 07717168
git clone -c core.autocrlf=false hb-base hb-pr-head; git -C hb-pr-head checkout 4e445e00
doppel check --backend local --no-twin --base hb-pr-base --head hb-pr-head --python hb-venv\Scripts\python.exe --strict --out hb-pr.md
```

bash:

```bash
git clone -c core.autocrlf=false hb-base hb-pr-base && git -C hb-pr-base checkout 07717168
git clone -c core.autocrlf=false hb-base hb-pr-head && git -C hb-pr-head checkout 4e445e00
doppel check --backend local --no-twin --base hb-pr-base --head hb-pr-head --python hb-venv/bin/python --strict --out hb-pr.md
```

**Expect:** `## Doppel: no regressions` (69 tests on base). The PR only fixes string escapes. Older httpbin
PRs (#65, #41, #34) need `flasgger`, which newer httpbin made optional: `hb-venv` gets it with
`pip install flasgger`. Without it, Doppel says `couldn't compare` and shows the `ModuleNotFoundError`.

### On Nebius instead

```
doppel check --no-twin --base hb-base --head hb-head --strict --out hb.md
```

---

## 2. flasky (Flask, old pins)

[miguelgrinberg/flasky](https://github.com/miguelgrinberg/flasky): the companion app of the *Flask Web
Development* book. Tests: unittest files in `tests/`, no CI or tox file, so Doppel has to find them itself.
Its dependencies are pinned for 2017 (Flask 0.12): it needs **Python 3.8**, and its config reads
`SERVER_NAME` from the environment.

### Get it

Get Python 3.8 with [uv](https://docs.astral.sh/uv/) if you don't have it (`py -3.8 --version` /
`python3.8 --version` to check).

PowerShell:

```powershell
git clone -c core.autocrlf=false https://github.com/miguelgrinberg/flasky.git fl-base
git -C fl-base checkout 3beedd6
git clone -c core.autocrlf=false fl-base fl-head
uv python install 3.8
uv venv -p 3.8 --seed fl-venv
.\fl-venv\Scripts\python -m pip install -r fl-base\requirements\dev.txt pytest
$env:SERVER_NAME = "localhost"
cd fl-base; ..\fl-venv\Scripts\python -m pytest -q; cd ..
```

bash:

```bash
git clone -c core.autocrlf=false https://github.com/miguelgrinberg/flasky.git fl-base
git -C fl-base checkout 3beedd6
git clone -c core.autocrlf=false fl-base fl-head
uv python install 3.8
uv venv -p 3.8 --seed fl-venv
fl-venv/bin/python -m pip install -r fl-base/requirements/dev.txt pytest
export SERVER_NAME=localhost
(cd fl-base && ../fl-venv/bin/python -m pytest -q)
```

**Expect:** `34 passed, 1 skipped` (the skipped one is Selenium). flasky has no `setup.py` or
`pyproject.toml`, so there is nothing to install editable here: the requirements are enough. Without
`SERVER_NAME` every test fails to load with `KeyError: 'SERVER_NAME'`.

### Planted regression: every post now carries its author's email

In `fl-head/app/models.py`, in `Post.to_json` (not `Comment.to_json`, which has a similar line):

```python
# before
            'author_url': url_for('api.get_user', id=self.author_id),
            'comments_url': url_for('api.get_post_comments', id=self.id),
# after
            'author_url': url_for('api.get_user', id=self.author_id),
            'author_email': self.author.email,
            'comments_url': url_for('api.get_post_comments', id=self.id),
```

A privacy leak the tests don't notice: 34 still pass. Run (the `SERVER_NAME=localhost` prefix inside
`--command` works on every OS and backend; Doppel sets it for the tests):

PowerShell:

```powershell
doppel check --backend local --no-twin --base fl-base --head fl-head --python fl-venv\Scripts\python.exe --command "SERVER_NAME=localhost python -m pytest" --strict --out fl.md --json fl.json
```

bash:

```bash
doppel check --backend local --no-twin --base fl-base --head fl-head --python fl-venv/bin/python --command "SERVER_NAME=localhost python -m pytest" --strict --out fl.md --json fl.json
```

**Expect** (about 15 seconds; exit code 1):

```
## Doppel: 1 behavior change, seen in 5 requests
| 1 | Responses now include author_email | `POST /api/v1/posts/`<br>`GET /api/v1/posts/{id}`<br>`GET /api/v1/users/{id}/posts/`<br>+2 more | 5 requests from 1 test | `no author_email` → `author_email: "john@example.com"` |
```

**Worth trying:** add `'email': self.email,` to `User.to_json` instead. That one **is** caught by flasky's
own tests (`test_user_model.py` checks the exact keys), and Doppel reports it as
`A test that passed now fails: AssertionError: Lists differ…`. Both tools agree there.

### Safe change

`git -C fl-head checkout .`, then in `fl-head/flasky.py` change `"""Run the unit tests."""` to
`"""Run the unit tests (optionally with coverage)."""`. **Expect:** `## Doppel: no regressions`.

### Real "PR": a chapter commit

flasky has no merged pull requests: its history is one commit per book chapter. Use a chapter as the change,
with its parent as base:

PowerShell:

```powershell
git clone -c core.autocrlf=false fl-base fl-pr-base; git -C fl-pr-base checkout 4770c59~1
git clone -c core.autocrlf=false fl-base fl-pr-head; git -C fl-pr-head checkout 4770c59
doppel check --backend local --no-twin --base fl-pr-base --head fl-pr-head --python fl-venv\Scripts\python.exe --command "SERVER_NAME=localhost python -m pytest" --strict --out fl-pr.md
```

bash:

```bash
git clone -c core.autocrlf=false fl-base fl-pr-base && git -C fl-pr-base checkout 4770c59~1
git clone -c core.autocrlf=false fl-base fl-pr-head && git -C fl-pr-head checkout 4770c59
doppel check --backend local --no-twin --base fl-pr-base --head fl-pr-head --python fl-venv/bin/python --command "SERVER_NAME=localhost python -m pytest" --strict --out fl-pr.md
```

`4770c59` is "Chapter 16: Logging of slow database queries (16a)". **Expect:** `## Doppel: no regressions`:
it adds logging, not behavior.

### On Nebius instead

`--python 3.8` makes the sandbox install that Python:

```
doppel check --no-twin --base fl-base --head fl-head --python 3.8 --command "SERVER_NAME=localhost python -m pytest" --strict --out fl.md
```

---

## 3. django-allauth (Django)

[pennersr/django-allauth](https://github.com/pennersr/django-allauth): authentication for Django, with a
large test suite (2,266 tests). Its tests live in `tests.py` and `*_tests.py` files as well as `test_*.py`
(set in its `pytest.ini`), and its CI runs them through nox.

### Get it

PowerShell:

```powershell
git clone -c core.autocrlf=false https://github.com/pennersr/django-allauth.git aa-base
git -C aa-base checkout 5ea117e
git clone -c core.autocrlf=false aa-base aa-head
py -3.13 -m venv aa-venv
.\aa-venv\Scripts\python -m pip install -e ".\aa-base[headless,socialaccount,mfa,idp-oidc,openid,steam]" "pytest>=8.3.5,<9" "pytest-asyncio==1.3.0" "pytest-django>=4.11,<4.13" "Pillow>=9.0" "djangorestframework>=3.15.2,<4" "django-ninja>=1.3.0,<2" "pyyaml>=6.0.2,<7" "psycopg[binary]>=3.2.10" "oauthlib>=4.0.0,<5"
cd aa-base; ..\aa-venv\Scripts\python -m pytest -q; cd ..
```

bash:

```bash
git clone -c core.autocrlf=false https://github.com/pennersr/django-allauth.git aa-base
git -C aa-base checkout 5ea117e
git clone -c core.autocrlf=false aa-base aa-head
python3.13 -m venv aa-venv
aa-venv/bin/python -m pip install -e "./aa-base[headless,socialaccount,mfa,idp-oidc,openid,steam]" "pytest>=8.3.5,<9" "pytest-asyncio==1.3.0" "pytest-django>=4.11,<4.13" "Pillow>=9.0" "djangorestframework>=3.15.2,<4" "django-ninja>=1.3.0,<2" "pyyaml>=6.0.2,<7" "psycopg[binary]>=3.2.10" "oauthlib>=4.0.0,<5"
(cd aa-base && ../aa-venv/bin/python -m pytest -q)
```

**Expect:** `2266 passed` in about a minute. The test dependencies are the ones its noxfile installs. The
`saml` extra is left out: it needs `xmlsec`, which often has to be compiled on Windows, and allauth's own CI
leaves SAML out too.

### Planted regression: the config endpoint reports the opposite setting

In `aa-head/allauth/headless/usersessions/response.py`:

```python
# before
    data = {"usersessions": {"track_activity": app_settings.TRACK_ACTIVITY}}
# after
    data = {"usersessions": {"track_activity": not app_settings.TRACK_ACTIVITY}}
```

Front ends that read this config to decide whether to show session activity now get the wrong answer.
`test_config` only checks the keys, so all 2,266 tests still pass. Run:

PowerShell:

```powershell
doppel check --backend local --no-twin --base aa-base --head aa-head --python aa-venv\Scripts\python.exe --strict --out aa.md --json aa.json
```

bash:

```bash
doppel check --backend local --no-twin --base aa-base --head aa-head --python aa-venv/bin/python --strict --out aa.md --json aa.json
```

**Expect** (about 3 minutes; exit code 1):

```
[tests] test command: python -m pytest   (from tests found in the repo)
[tests] 222 test files: split across 8 sandboxes per world
[tests] 2266 tests passed on base; 3569 requests recorded; 0 flaky, 696 with varying requests, 27 noisy field name(s); 2 difference(s)
## Doppel: 1 behavior change, seen in 2 requests
| 1 | Values changed: track_activity | `GET /_allauth/app/v1/config`<br>`GET /_allauth/browser/v1/config` | 2 requests from 2 tests | `track_activity: false` → `track_activity: true` |
```

"696 with varying requests" means about 700 tests send different requests from one run to the next on the same
code (random emails, fresh codes). Doppel still checks that those tests pass, but doesn't compare their
responses; the check page lists that as "left out".

**Worth trying:** add a field to `serialize_user` in `allauth/headless/adapter.py`. allauth's own tests
catch that one (4 fail), and Doppel reports the failing tests.

### Safe change

`git -C aa-head checkout .`, then in `aa-head/allauth/headless/adapter.py` change
`Returns the basic user data` to `Return the basic user data`. **Expect:** `## Doppel: no regressions`.

### Real PR: "Add cache-control header to JWKs response and key rotation support"

allauth merges on Codeberg with merge commits (`98de794a`). The PR's base and head:

PowerShell:

```powershell
git clone -c core.autocrlf=false aa-base aa-pr-base; git -C aa-pr-base checkout 09eea91a
git clone -c core.autocrlf=false aa-base aa-pr-head; git -C aa-pr-head checkout 1565ad65
doppel check --backend local --no-twin --base aa-pr-base --head aa-pr-head --python aa-venv\Scripts\python.exe --strict --out aa-pr.md
```

bash:

```bash
git clone -c core.autocrlf=false aa-base aa-pr-base && git -C aa-pr-base checkout 09eea91a
git clone -c core.autocrlf=false aa-base aa-pr-head && git -C aa-pr-head checkout 1565ad65
doppel check --backend local --no-twin --base aa-pr-base --head aa-pr-head --python aa-venv/bin/python --strict --out aa-pr.md
```

**Expect:** `## Doppel: no regressions` (13 files, +478/−13; 2,104 tests on base). The PR's visible change is
a new `Cache-Control` response header, and test mode compares response bodies and status codes, not
headers: a known limit, listed in the README's [Limits](README.md#limits).

### On Nebius instead

```
doppel check --no-twin --base aa-base --head aa-head --strict --out aa.md
```

---

## 4. Kinto (Pyramid + WebTest)

[Kinto/kinto](https://github.com/Kinto/kinto): a JSON storage service. Pyramid, tested through WebTest, run
with `pytest -n auto` (set in its `pyproject.toml`). Its CI adds PostgreSQL; without it, the PostgreSQL tests
skip themselves and the rest run on the in-memory backend, which is all this guide needs.

### Get it

PowerShell:

```powershell
git clone -c core.autocrlf=false https://github.com/Kinto/kinto.git kinto-base
git -C kinto-base checkout 7c8fb41
git clone -c core.autocrlf=false kinto-base kinto-head
py -3.12 -m venv kv
.\kv\Scripts\python -m pip install -e ".\kinto-base[test]" pytest-xdist
cd kinto-base; ..\kv\Scripts\python -m pytest -q -n 4; cd ..
```

bash:

```bash
git clone -c core.autocrlf=false https://github.com/Kinto/kinto.git kinto-base
git -C kinto-base checkout 7c8fb41
git clone -c core.autocrlf=false kinto-base kinto-head
python3.12 -m venv kv
kv/bin/python -m pip install -e "./kinto-base[test]" pytest-xdist
(cd kinto-base && ../kv/bin/python -m pytest -q -n 4)
```

**Expect:** about 2,035 passed and 313 skipped, in 3 to 5 minutes. A handful may fail on your machine
(here: 6 failed, 1 error, all in parts that need services or settings this setup doesn't provide). That's
fine: Doppel finds them by running base twice, leaves them out of the comparison and says so.
`pytest-xdist` is needed because Kinto's own settings pass `-n auto`.

### Planted regression: the root endpoint exposes the storage backend

In `kinto-head/kinto/core/views/hello.py`, in `get_hello`, after the loop that copies the public settings:

```python
# before
    for setting in list(public_settings):
        data["settings"][setting] = settings[setting]

# after
    for setting in list(public_settings):
        data["settings"][setting] = settings[setting]
    data["storage_backend"] = settings["storage_backend"]

```

An internal setting, not in `public_settings`, now shown to anyone on `GET /v1/`. Run:

PowerShell:

```powershell
doppel check --backend local --no-twin --base kinto-base --head kinto-head --python kv\Scripts\python.exe --strict --out kinto.md --json kinto.json
```

bash:

```bash
doppel check --backend local --no-twin --base kinto-base --head kinto-head --python kv/bin/python --strict --out kinto.md --json kinto.json
```

**Expect** (6 to 7 minutes; exit code 1):

```
[tests] 96 test files: split across 4 sandboxes per world
[tests] 2038 tests passed on base; 3871 requests recorded; 0 flaky, 103 with varying requests, 17 noisy field name(s); 94 difference(s)
## Doppel: 1 behavior change, seen in 94 requests
| 1 | Responses now include storage_backend | `GET /v0/`<br>`POST /v0/batch`<br>`GET /v1/` | 94 requests from 79 tests | `no storage_backend` → `storage_backend: "kinto.core.storage.memory"` |
```

94 requests, one change: the root answer, plus the same answer nested inside batch responses.

### Safe change

`git -C kinto-head checkout .`, then change the docstring of `get_hello` from
`"""Return information regarding the current instance."""` to
`"""Return what this instance is: its name, version, settings and capabilities."""`.
**Expect:** `## Doppel: no regressions`.

### Real PR: #3793 "Fix KeyError in memory storage purge_deleted when resource is missing"

Kinto squash-merges, so a PR is one commit and its base is that commit's parent:

PowerShell:

```powershell
git clone -c core.autocrlf=false kinto-base kinto-pr-base; git -C kinto-pr-base checkout 91163c77~1
git clone -c core.autocrlf=false kinto-base kinto-pr-head; git -C kinto-pr-head checkout 91163c77
doppel check --backend local --no-twin --base kinto-pr-base --head kinto-pr-head --python kv\Scripts\python.exe --strict --out kinto-pr.md
```

bash:

```bash
git clone -c core.autocrlf=false kinto-base kinto-pr-base && git -C kinto-pr-base checkout 91163c77~1
git clone -c core.autocrlf=false kinto-base kinto-pr-head && git -C kinto-pr-head checkout 91163c77
doppel check --backend local --no-twin --base kinto-pr-base --head kinto-pr-head --python kv/bin/python --strict --out kinto-pr.md
```

**Expect:** `## Doppel: no regressions` (2,014 tests on base, about 7 minutes). The fix touches a code path
the existing tests don't reach with the bug's input.

### On Nebius instead

```
doppel check --no-twin --base kinto-base --head kinto-head --strict --out kinto.md
```

---

## 5. Datasette (ASGI)

[simonw/datasette](https://github.com/simonw/datasette): a tool for publishing SQLite databases, with a very
large suite (about 2,800 tests). Its test client is built on `httpx2`, a fork of httpx.

### Get it

Its development dependencies are a `dev` dependency group, which needs pip 25.1 or newer:

PowerShell:

```powershell
git clone -c core.autocrlf=false https://github.com/simonw/datasette.git ds-base
git -C ds-base checkout c435a80
git clone -c core.autocrlf=false ds-base ds-head
py -3.12 -m venv ds-venv
.\ds-venv\Scripts\python -m pip install -U pip
.\ds-venv\Scripts\python -m pip install -e .\ds-base --group .\ds-base\pyproject.toml:dev
```

bash:

```bash
git clone -c core.autocrlf=false https://github.com/simonw/datasette.git ds-base
git -C ds-base checkout c435a80
git clone -c core.autocrlf=false ds-base ds-head
python3.12 -m venv ds-venv
ds-venv/bin/python -m pip install -U pip
ds-venv/bin/python -m pip install -e ./ds-base --group ./ds-base/pyproject.toml:dev
```

Doppel picks its test command from the CI workflow: `python -m pytest -m 'not serial'`.

### Planted regression: `/-/versions` stops reporting the server

In `ds-head/datasette/app.py`, in the versions data, remove the line:

```python
            "uvicorn": uvicorn_version,
```

PowerShell:

```powershell
doppel check --backend local --no-twin --base ds-base --head ds-head --python ds-venv\Scripts\python.exe --strict --out ds.md --json ds.json
```

bash:

```bash
doppel check --backend local --no-twin --base ds-base --head ds-head --python ds-venv/bin/python --strict --out ds.md --json ds.json
```

**Expect** (about 9 minutes; exit code 1):

```
[tests] test command: python -m pytest -m 'not serial'   (from CI workflow (test-sqlite-support.yml, job test))
[tests] 84 test files: split across 4 sandboxes per world
[tests] 2808 tests passed on base; 2596 requests recorded; 2 flaky, 1 with varying requests, 19 noisy field name(s); 28 difference(s)
## Doppel: 1 behavior change, seen in 28 requests
| 1 | Responses no longer include uvicorn | `GET /-/versions.json` | 28 requests from 9 tests | `uvicorn: "0.54.0"` → `no uvicorn` |
```

Datasette's own tests pass on it: they check the versions page's other keys. The `serial` tests (the ones
its CI runs separately, one at a time) are left out, as in its CI's first step.

### Safe change

`git -C ds-head checkout .`, then in `ds-head/datasette/app.py` change the docstring
`"""Context manager to mark code as executing within a datasette.client request."""` to
`"""Context manager that marks code as running inside a datasette.client request."""`.
**Expect:** `## Doppel: no regressions` (about 9 minutes).

### Real PR: #2951 "Fix missing space before the view count on the index page"

Datasette squash-merges, so the base is the commit's parent:

PowerShell:

```powershell
git clone -c core.autocrlf=false ds-base ds-pr-base; git -C ds-pr-base checkout e4e6f919~1
git clone -c core.autocrlf=false ds-base ds-pr-head; git -C ds-pr-head checkout e4e6f919
doppel check --backend local --no-twin --base ds-pr-base --head ds-pr-head --python ds-venv\Scripts\python.exe --strict --out ds-pr.md
```

bash:

```bash
git clone -c core.autocrlf=false ds-base ds-pr-base && git -C ds-pr-base checkout e4e6f919~1
git clone -c core.autocrlf=false ds-base ds-pr-head && git -C ds-pr-head checkout e4e6f919
doppel check --backend local --no-twin --base ds-pr-base --head ds-pr-head --python ds-venv/bin/python --strict --out ds-pr.md
```

**Expect:** `## Doppel: no regressions`. The PR only moves whitespace around a comma in the index page's
HTML, and no response the base's tests recorded changed. The test the PR adds isn't part of the comparison:
only tests that exist on base run in both worlds, so a PR can't add (or edit) the test that judges it.

### On Nebius instead

```
doppel check --no-twin --base ds-base --head ds-head --strict --out ds.md
```

---

## Any other app or pull request

The same steps work on any Python web app whose tests run with pytest, Django's or unittest's runner:

1. **Base and head.** Clone twice. For a real PR, find where its branch started:
   - **Squash-merged** (one commit with `(#123)` in its message): base = that commit's parent (`<sha>~1`), head = the commit.
   - **Merge commit** (`Merge pull request #123`): base = `git merge-base <merge>^1 <merge>^2`, head = `<merge>^2`.
   - **Open PR**: `git -C <head> fetch origin pull/123/head` and `git -C <head> checkout FETCH_HEAD`; base =
     `git -C <head> merge-base FETCH_HEAD origin/main` (use the repo's default branch).
   - Or let [Try Doppel](https://maukthik.github.io/doppel-try/) do all of it: paste the PR's link.
2. **The app's Python.** A venv with the app installed editable from base, plus its test dependencies (look
   in `tox.ini`, the CI workflow, `noxfile.py`, `requirements-dev.txt` or the `test`/`dev` extras). Check the
   suite passes on base before involving Doppel. Or skip this and use `--backend nebius`.
3. **Run** `doppel check --backend local --no-twin --base <base> --head <head> --python <venv python> --strict`.
4. **If the test command is wrong**, override it with `--command "..."`; an environment variable the tests
   need goes in front: `--command "SERVER_NAME=localhost python -m pytest"`.

### Reading the result

| You see | It means |
|---|---|
| `N behavior changes` / `regressions the PR doesn't mention` | Responses differ between base and head, confirmed by running the tests behind them again |
| `no regressions` | Every response the tests got was the same on both (after the noise both base runs showed) |
| `The tests can't run on the PR's code: <error>` | The head doesn't even import: often a real bug, sometimes a dependency your venv lacks |
| `couldn't compare: the test command ran no test on the unchanged code` | The tests don't run on base in this environment: check the venv and the command (the output is shown) |
| `couldn't compare: the tests ran but sent no request through a test client Doppel records` | The tests don't talk to the app over HTTP through a client Doppel knows (they use a browser, websockets or mocks). Use the twin |
| `no such interpreter` | `--python` points at nothing: give the full path to the venv's python |

### Record what you find

For each app, a line like this is all a report needs (the [Share a result](TESTING.md#share-a-result)
section says what to attach):

```
app, commit | how its tests ran (command, count, time) | planted: caught? | safe: clean? | real PR: result | anything odd
```

---

## What these apps taught Doppel

Running these five apps found eight gaps. All are fixed and each is pinned by a test in
[`tests/test_new_apps.py`](tests/test_new_apps.py) or [`tests/test_presentation.py`](tests/test_presentation.py):

| App | What happened | Fixed |
|---|---|---|
| django-allauth | Doppel used the Makefile's `pytest allauth/`, which collects nothing (the tests moved to `tests/`) | A command that names folders with no test files is passed over |
| django-allauth | Only `test_*.py` files were split across sandboxes, so 1,076 tests in `tests.py` files never ran | Test files are found with the repo's own `python_files` setting; the base's copies of them are used in head, like other tests |
| django-allauth | With `--command`, the report still said the command came from the Makefile | The report says "from you" |
| Kinto | One change showed as three, because batch answers carry a `Content-Length` that follows the body | A `Content-Length` inside a body is not compared on its own |
| flasky | With Flask 0.12's test client, every request was recorded as `GET ` with no path | The recorder reads the path from what the client was given |
| Datasette | Its test client uses `httpx2`, which the recorder didn't know, so nothing was recorded | `httpx2` is recorded like `httpx` |
| httpbin | A head whose tests can't import showed as "this test no longer exists" seventy times | One change: "The tests can't run on the PR's code" with the error |
| Datasette | The safe change came back as a difference on an error page, every run: the `.egg-info` an editable install leaves in the base folder was copied into the base worlds only, so pytest loaded the app differently there | Build leftovers (`*.egg-info`) stay out of every world |
