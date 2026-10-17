# Judge-only rerun 3 of 3

The recordings of [../guided-rerun/](../guided-rerun/) judged again with `bench/rejudge.py --results`:
nothing else was run. All three reruns gave identical results, so the judge is steady on the same evidence:

| Set | Regressions caught | False alarms on safe PRs |
|---|---|---|
| main (30) | 16/20 | 3/10 (s07, s09, s10) |
| held-out (10) | 7/7 | 0/3 |
| consequence (8) | 2/3 | 0/5 |

The live run's other false alarms (h10, c-s1, c-s3, c-s5) didn't come back in any rerun. The result the main
README quotes is [../guided-rerun-fixed/](../guided-rerun-fixed/).
