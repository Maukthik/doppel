# Changelog

## Unreleased

Seven gaps found while writing the architecture guide, each pinned by a test in `tests/test_hardening.py`:
- Test mode expands tox's own substitutions (`{posargs:tests}`, `{posargs}`, `{toxinidir}`, `{envpython}`),
  which pytest used to take for paths.
- A project's `PYTEST_ADDOPTS` is added to Doppel's options instead of replacing them, and
  `PYTHONHASHSEED=random` from a project is ignored, so random order and hash randomization stay off.
- The judge groups a status change, a crash or a changed request per route: a 500 on two unrelated routes is two
  changes with two verdicts. A field change still counts once wherever it appears.
- `--tests-out` is written after the reviewer's confirmed suspicions are applied, so an escalated regression
  gets its test; a field the base didn't have is asserted as missing.
- `doppel init` reports a `<repo>` placeholder in the seed once, not twice.
- The init trace page shows the setup agent's run as its own row, and an init the agent finished as working.
- The setup agent's `read` reads the sandbox it works in (including files its shell commands wrote), and a
  re-read is allowed again after a shell command; `./.env` and `env` are no longer the same path.

## 1.0.0 (October 2026)

The first release meant to be installed: `uses: Maukthik/doppel@v1` in a workflow, or
`pip install "doppel-check[nebius] @ git+https://github.com/Maukthik/doppel@v1"` for the CLI.

**On every pull request**
- Test mode with no setup: the app's own tests run on the base branch twice and on the PR once, in Nebius
  sandboxes, and every recorded response is compared. The command and install come from `tox.ini`, the CI
  workflow, a Makefile or a noxfile.
- The guided twin when the repo has a `doppel.toml`: Nemotron Ultra lists what it suspects the PR breaks,
  Nemotron writes users and probes, and each suspicion ends up confirmed, refuted or untested.
- One PR comment, updated on each push; a check page on GitHub Pages; the check fails on a regression.

**New in 1.0.0**
- A maintainer can accept Doppel's findings: add the label `doppel: accepted` and re-run the check. It passes,
  and the comment keeps the findings (input `accept-label`).
- When Doppel can't compare the two versions, the check warns instead of failing the PR, and the comment says
  why, even when Doppel stopped before writing a report (input `fail-on-error`, default `false`).
- Changes to fields that look like they hold secrets (`password_hash`, `apiKey`, `refresh_token`…) are never
  accepted as intended on the PR description's word: they always go to a person.
- A route the PR adds can't regress: a difference on it goes to a person, not into the regressions.
- A reviewer's suspicion is only refuted by a probe that really sent the triggering value; probes that got no
  answer at all are rehearsed and repaired.
- `tox.ini` with `skipsdist` no longer installs the project as a package.
- The package is named `doppel-check` (the name `doppel` on PyPI belongs to an unrelated project). The
  command is still `doppel`.

## Earlier tags

`v0.1-local-twin`, `v0.2-nebius-twin`, `v0.3-judge`, `v0.4-action`, `v0.5-evidence`, `v0.6-init`,
`v0.7-glass-box`: the build stages, described in [docs/STAGES.md](docs/STAGES.md).
