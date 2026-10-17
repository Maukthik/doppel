# Results, folder by folder

Every number in the main README comes from one of these folders. Each folder's README (or `results.md`) has
the per-item table, and the JSON files hold the raw recordings. How the benchmark PRs were made and scored:
[../README.md](../README.md).

## The benchmark: pull requests to microblog-api

| Folder | What | When | Headline |
|---|---|---|---|
| [`results.md`](results.md) and the 48 `*.json` here | The first full run: the app's tests, AI review, Doppel and review-guided Doppel on 30 PRs (20 regressions, 10 safe), the 10 held-out PRs and the 8 consequence PRs | 3–11 Oct | guided: 16/20, 0/10 false alarms; held-out 6/7, 0/3 |
| [`consequence-new-judge/`](consequence-new-judge/) | The 8 consequence PRs (an announced change with side effects, or a regression hiding behind one) through `doppel check` | 11 Oct | 2/3 caught, 2/5 safe PRs flagged |
| [`guided-rerun/`](guided-rerun/) | Review-guided Doppel run again on all 48, with probe rehearsal | 16–17 Oct | 17/20, 3/10; held-out 7/7, 1/3; consequence 2/3, 3/5 |
| `guided-rerun-j1/`, `-j2/`, `-j3/` | The same recordings judged three more times, nothing else run | 17 Oct | identical each time: 16/20, 3/10; 7/7, 0/3; 2/3, 0/5 |
| [`guided-rerun-knockon-trial/`](guided-rerun-knockon-trial/) | A judge change tried on those recordings and dropped, because it lost a real catch | 17 Oct | 17/20, 1/10, but consequence fell to 1/3 |
| [`guided-rerun-fixed/`](guided-rerun-fixed/) | Those recordings judged with the one rule that was kept. **The numbers the main README quotes** | 17 Oct | 16/20, 2/10; 7/7, 0/3; 2/3, 0/5 |
| [`testmode/`](testmode/) | Test mode alone on the benchmark, unjudged, on a laptop | 8 Oct | 21 of 27 regressions changed a response the suite already sends |
| `recheck-1014/`, `recheck-order/` | Test mode checked again after later changes (the second after the rule for lists returned in a new order) | 14 Oct | unchanged: 21/27 |

## Zero setup on real apps (test mode)

| Folder | What | When | Headline |
|---|---|---|---|
| [`fresh-before/`](fresh-before/), [`fresh/`](fresh/) | Dev set, 10 apps: the first runs | 11–12 Oct | 1/10 work |
| [`fresh-rerun/`](fresh-rerun/) | Dev set again, after the fixes those runs led to | 14 Oct | 8/10 work, 0 false differences |
| [`heldout/`](heldout/) | Held-out set, 10 apps: first run | 12 Oct | 3/10 work |
| [`heldout-rerun/`](heldout-rerun/) | Held-out set again | 14 Oct | 5/10 work, 0 false differences |
| [`round2/`](round2/) | 10 more apps, first sight, with predictions committed before the run | 14 Oct | 4/10 work, 0 false differences |

## Real bugs

| Folder | What | When | Headline |
|---|---|---|---|
| [`bugsinpy/`](bugsinpy/) | 37 BugsInPy bugs in FastAPI, Tornado and Sanic, each replayed as the PR that brings it back | 10 Oct | 2 caught, 13 invisible to the suite, 22 not runnable |
| [`fixes/`](fixes/) | 14 recent real fixes in fava, isso, privacyidea and prefect, test mode | 14 Oct | 0/14 caught, 0 false alarms |
| [`fixes/twin/`](fixes/twin/) | isso's stored-XSS fix through the guided twin, four runs | 14–17 Oct | caught on the fourth run (the write-up says what changed between runs) |

## Other

| Folder | What | When | Headline |
|---|---|---|---|
| [`demo-pr/`](demo-pr/) | The live demo pull request on a fresh repo | 17 Oct | password-hash leak caught on both runs; all 44 tests pass |
| [`generality/`](generality/) | Two apps Doppel was never tuned for, set up by `doppel init` alone | 8 Oct | flaskr 3/3 caught, 0/1 false alarms; drf-tutorial's setup failed |
