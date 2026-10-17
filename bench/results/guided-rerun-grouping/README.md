# The 17 Oct run, judged with the per-route grouping

The same recordings as [../guided-rerun/](../guided-rerun/): nothing was run again. `bench/rejudge.py --results`
ran only the judge, three times, on copies of [../guided-rerun-fixed/](../guided-rerun-fixed/), with today's
judge: the new-endpoint rule from that folder, plus the grouping fix from 17 Oct (a status change, a crash or a
changed request is judged once per route, not once for every route it appears on). **These are the numbers the
main README quotes.**

| Set | Regressions caught | False alarms on safe PRs |
|---|---|---|
| main (30) | 17/20 (missed r14, r18, r20) | 2/10 (s07, s09) |
| held-out (10) | 7/7 | 0/3 |
| consequence (8) | 2/3 | 0/5 |

The three runs ([this folder](.), [../guided-rerun-grouping-2/](../guided-rerun-grouping-2/),
[../guided-rerun-grouping-3/](../guided-rerun-grouping-3/)) gave the same result on all 48 PRs.

## The one result that changed: r11

r11 raises the email length limit to 254 characters and also drops the email-format check, so `not-an-email`
is accepted. Against [../guided-rerun-fixed/](../guided-rerun-fixed/), only r11 changed, from missed to caught:

- **Before:** the judge labelled `POST /api/users 400 -> 201` and `PUT /api/me 400 -> 200` intended, quoting the
  description's "RFC 5321 allows addresses up to 254 characters", while its own reason said the `Email()`
  validator was removed and `not-an-email` now passes. Two knock-on changes (`POST /api/tokens` and
  `GET /api/me`, 401 -> 200) were labelled regressions, but they aren't on the route the bug is on, so they
  don't count as the catch.
- **After:** the same three findings are labelled regressions: "invalid email strings like 'not-an-email' now
  pass validation and return 201 instead of 400, which the description does not announce."

The grouping fix didn't regroup the findings that decide r11. It split the two 401 -> 200 changes, which used
to be one group across two routes, into two. The judge saw a differently grouped prompt and read it
differently. The new verdict is the right one and held in all three runs, but it is still the model's reading,
so count it as one sample like every other number here.

## Files

- `*.json`: one per PR. Each finding keeps the live run's label as `label_v1`.
- `rejudge.log`: the per-PR result. This run's console output wasn't saved, so this one was rebuilt from the
  JSON files here; it matches the logs saved for runs 2 and 3.
- `stage5.log`: the live run's log, copied from [../guided-rerun/](../guided-rerun/).
