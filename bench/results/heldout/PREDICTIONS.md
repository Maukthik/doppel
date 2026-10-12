# Held-out apps: predictions and rules, written before the run

Committed after patch 0045 and before the held-out apps were run. From this commit, no code changes until the
held-out run is done and reported.

## Where things stand (dev set, on a GitHub runner)

The ten dev apps found the gaps the setup layer was rebuilt for, so these are development numbers:

| | Works | Notes |
|---|---|---|
| Before the rebuild (11 Oct screening) | 1 of 10 | bakerydemo only |
| After (patches 0037-0045) | 8 of 10 | 14,505 tests, 17,079 requests, 0 false differences |

The two dev stops: fastapi-template (needs its built frontend, then Postgres) and redash (its HTTP tests need
Postgres and Redis). Both are stops Doppel explains; neither is a false result.

## The held-out apps

Ten apps chosen on 11 Oct 2026 by the rule in `bench/fresh_apps.py` (HELDOUT_RULE), pinned to a commit, run
from the repo root with nothing hand-given. Not opened for their setup during development; the predictions
below come from general knowledge of each project only.

| App | Framework | Prediction | Why |
|---|---|---|---|
| ihatemoney | Flask | likely works | SQLite tests |
| babybuddy | Django | likely works | `manage.py test`, SQLite |
| pretalx | Django | likely works | pytest; may need system libraries |
| tandoor | Django | uncertain | database setup unknown |
| inventree | Django | uncertain | the app sits in a subfolder; tests run through `invoke` |
| lnbits | FastAPI | uncertain | SQLite by default; some tests may expect a Lightning backend |
| searxng | Flask | uncertain | tests run through its own `manage` script; few HTTP requests |
| changedetection | Flask | uncertain | tests use a live server |
| polar | FastAPI | likely stops | needs Postgres and Redis |
| fastapi-best-architecture | FastAPI | likely stops | needs a SQL database and Redis |

**Overall: 4 to 7 of 10 work; every stop has a readable reason; 0 false differences among the apps that work.**

## Rules for the run

- One run of all ten, on a GitHub runner (`bench-fresh` workflow, set `heldout`), at the pinned commits.
- No fixes for these apps, before or after. Gaps they reveal are reported, and fixed later only for future runs.
- An app that ends with an infrastructure error (the sandbox service, not the app: "Nothing was learned about
  the app; rerun") may be rerun once, unchanged. Nothing else is rerun.
- "Works" means the harness's verdict: tests ran on base, requests were recorded, and base vs base again vs
  head (the same code) shows no difference. A stop counts against the prediction whatever its reason.
- Every result is reported, matching the prediction or not, next to the dev before and after.
