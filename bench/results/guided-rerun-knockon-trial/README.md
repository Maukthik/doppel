# A judge change that was tried and dropped (17 Oct)

The same recordings as [../guided-rerun/](../guided-rerun/), judged with the new-endpoint rule plus one more
change: wording that told the judge a knock-on effect of an announced change counts as announced (a username
the description now allows passes its check, and the request then fails a later, unchanged one).

| Set | Regressions caught | False alarms on safe PRs |
|---|---|---|
| main (30) | 17/20 | 1/10 (s07) |
| held-out (10) | 7/7 | 0/3 |
| consequence (8) | **1/3** | 0/5 |

The main set looked better, but c-r3-bio-lost went from caught to missed: the judge excused a real regression
(a client sending `about_me` now gets 400) as part of the announced rename. The rule was that a change goes if
it costs a catch, so the wording was removed before anything else ran. The kept result is
[../guided-rerun-fixed/](../guided-rerun-fixed/).
