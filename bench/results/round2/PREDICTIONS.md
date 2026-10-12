# Round two: predictions and rules, written before the run

Committed after patch 0048 and before the round-two apps were run. From this commit, no code changes until the
round-two run is done and reported.

## Where things stand

| Set | Works | Notes |
|---|---|---|
| Dev, before the rebuild (11 Oct) | 1 of 10 | |
| Dev, after patches 0037-0045 | 8 of 10 | development numbers: fixed against these apps |
| Held-out round one (12 Oct, run once) | 3 of 10 | 0 false differences; 2 stops need services or a build step, 5 were Doppel gaps |

Patch 0048 fixed round one's five gaps (CI job choice twice, git at run time, Pipfile, requirements in a
subfolder), checked against those apps only. Round one's numbers stand as reported; they are not rerun.

## The round-two apps

Ten apps chosen on 13 Oct 2026 by the rule in `bench/fresh_apps.py` (ROUND2_RULE), before 0048, pinned to a
commit, run from the repo root with nothing hand-given. Not opened for their setup; these predictions come
from general knowledge of each project only.

| App | Framework | Prediction | Why |
|---|---|---|---|
| fava | Flask | likely works | no database |
| isso | Flask | likely works | small Werkzeug/Flask app; tests use its test client |
| privacyidea | Flask | uncertain | large app; SQLite tests; may need system libraries |
| prefect | FastAPI | uncertain | SQLite by default; very large suite |
| langflow | FastAPI | uncertain | backend in a subfolder; heavy dependencies |
| kiwi | Django | uncertain | its CI tests against real databases |
| litellm | FastAPI | uncertain | huge suite; many tests expect provider API keys |
| weblate | Django | likely stops | expects PostgreSQL |
| netbox | Django | likely stops | needs PostgreSQL and Redis |
| defectdojo | Django | likely stops | tests run through Docker Compose with Postgres |

**Overall: 3 to 6 of 10 work; every stop has a readable reason; 0 false differences among the apps that work.**

## Rules for the run

- One run of all ten, on a GitHub runner (`bench-fresh` workflow, set `round2`), at the pinned commits.
- No fixes for these apps, before or after. Gaps they reveal are reported, and fixed later only for future runs.
- An app that ends with an infrastructure error (the sandbox service, not the app: "Nothing was learned about
  the app; rerun") may be rerun once, unchanged. Nothing else is rerun.
- "Works" means the harness's verdict: tests ran on base, requests were recorded, and base vs base again vs
  head (the same code) shows no difference. A stop counts against the prediction whatever its reason.
- Every result is reported, matching the prediction or not, next to dev before, dev after and round one.
