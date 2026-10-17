# Try Doppel

Run [Doppel](https://github.com/Maukthik/doppel) on any public pull request, without installing anything.

**[Open the Try page](https://maukthik.github.io/doppel-try/)**, or
**[open an issue here](https://github.com/Maukthik/doppel-try/issues/new?template=try.yml)** and paste the pull
request's link. A workflow in this repo checks out the commit the PR started from and its latest commit, runs
`doppel check` on both in Nebius sandboxes, and replies on the issue with what changed that the PR's description
doesn't mention. The reply links a check page with the evidence: the request, the old answer and the new one.

| Example | What it shows |
|---|---|
| [doppel-demo#1](https://github.com/Maukthik/doppel-demo/pull/1) "Describe has_password in the API schema" | All 44 tests pass; every user object now carries its password hash |
| [doppel-demo#2](https://github.com/Maukthik/doppel-demo/pull/2) "Small cleanup in auth error handlers" | 401 and 403 answers rename a field the description doesn't mention |
| [doppel-demo#3](https://github.com/Maukthik/doppel-demo/pull/3) "Better endpoint descriptions" | Only docstrings change, as the PR says: nothing flagged |

## What it runs

- [`.github/workflows/try.yml`](.github/workflows/try.yml): one job per new issue.
- [`scripts/try_pr.py`](scripts/try_pr.py): reads the link, checks the daily limit, checks out the PR, replies
  and labels. What the issue's author wrote is passed through environment variables and never pasted into a
  shell command.
- The PR's code only runs inside Nebius sandboxes. This repo's workflow parses its files to find the test
  command and never executes them.

## Limits

- Public repos only, up to 500 MB, and a daily cap on runs (repository variable `DAILY_LIMIT`, default 25):
  each run uses real sandboxes.
- Test mode only: the app's own tests, before and after. The guided twin, where Nemotron writes users that probe
  the app, needs a `doppel.toml` in the repo. See
  [Setting up the twin](https://github.com/Maukthik/doppel#setting-up-the-twin).
- Python web apps (Flask, Django, FastAPI and similar) whose tests run from `tox.ini`, a CI workflow, a Makefile
  or a noxfile.

To check every pull request of your own repo, add
[one workflow file](https://github.com/Maukthik/doppel#put-it-on-your-pull-requests).

This repo is built from [`try/`](https://github.com/Maukthik/doppel/tree/main/try) in the Doppel repo by
`demo/setup-try-repo.ps1`.
