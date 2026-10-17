# The 17 Oct run, judged with the new-endpoint rule

The same recordings as [../guided-rerun/](../guided-rerun/): nothing was run again. `bench/rejudge.py --results`
ran only the judge, with one rule added after that run: a status change from 404 or 405 on a route the PR
adds goes to a person, never into the regressions, since there was no endpoint before and so no behavior to
break. **These are the numbers the main README quotes.**

| Set | Regressions caught | False alarms on safe PRs |
|---|---|---|
| main (30) | 16/20 | 2/10 (s07, s09) |
| held-out (10) | 7/7 | 0/3 |
| consequence (8) | 2/3 | 0/5 |

Against the same recordings judged without the rule ([../guided-rerun-j1/](../guided-rerun-j1/)), one result
changed: s10 (a new stats endpoint) went from flagged to cleared. No regression catch changed.

[rejudge.log](rejudge.log) is the run. In the JSON files, each finding keeps the live run's label as `label_v1`.
