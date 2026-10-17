# Live demo: a fresh repo, zero setup

`demo/setup-demo-repo.ps1` builds a public repo with microblog-api (MIT, commit `e250d71`), its own tests as a
`tests` check, and one workflow that runs `Maukthik/doppel@main` with no `doppel.toml` (test mode only). The pull
request *"Describe has_password in the API schema"* (r17 from the benchmark) says it only documents a field. It
actually puts every user's password hash into the API's responses.

| Check | Result |
|---|---|
| `tests` (all 44 of microblog-api's own tests) | ✅ pass |
| `doppel` | ❌ **1 regression the PR doesn't mention, seen in 38 requests**: `password_hash` added on 14 endpoints, with real `scrypt:32768:…` values on head |

Run 17 Oct 2026, on Nebius with the Nemotron judge, about one minute per run. `run1/` and `run2/` are the same PR
run twice (the second after GitHub Pages was turned on). Both flagged the same 38 requests, and every difference
was re-checked by rerunning its 25 tests on base and head.

The first attempt failed at install: Doppel ran `pip install -e .` although microblog-api's `tox.ini` says
`skipsdist=True`. Fixed in `setup_plan.py` the same day. That bug is how the run above came to exist.
