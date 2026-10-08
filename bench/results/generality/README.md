# Generality benchmark

Python apps Doppel was never tuned for, each set up by `doppel init` with no hand edits.
Everything runs on Nebius sandboxes with NVIDIA Nemotron models via Token Factory.

| App | Framework | Login | Database | `doppel init` | Served (A/A) | False diffs (A/A) | Doppel caught | Doppel false alarms | AI review caught | Tests caught |
|---|---|---|---|---|---|---|---|---|---|---|
| flaskr | Flask | session cookie, HTML forms | SQLite | ✅ 1 try | 95% | 0 | 3/3 | 0/1 | 3/3 | 3/3 |

Served = share of the invented users' requests the app answered (not 4xx/5xx) on the A/A run.
False diffs = differences between two runs of the same code: every one is noise.
Doppel false alarms are counted on the safe PRs (a regression verdict on a safe change).
