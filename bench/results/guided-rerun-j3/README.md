# Stage 5 rerun: the review-guided twin with today's code

Run on Nebius with the Nemotron judge, 16–17 Oct 2026 (`bench-stage5`, set `all`). Same 48 microblog-api PRs
as the earlier run whose files are in `../` (rejudged with judge v2 on 10 Oct). Since that run, the guided twin
changed in two ways: a probe stuck at 405 or with an unfilled `{placeholder}` now goes back to Nemotron and is
rewritten from the app's real answers (probe rehearsal), and the probe prompt asks for every value a trigger
names. The judge's code is the same; Nemotron's answers are not deterministic.

| Set | Regressions caught | False alarms on safe PRs |
|---|---|---|
| main (30) | **17/20** (was 16/20) | **3/10** (was 0/10) |
| heldout (10) | **7/7** (was 6/7) | **1/3** (was 0/3) |
| consequence (8), first guided run | 2/3 | 3/5 |

**Newly caught:** r08-login-by-email and h03-edit-not-saved. **Still missed:** r14-is-followed and r18-after-cursor
(no behavior change reached), r20-page-size (reached, judged intended), c-r2-private-email-own (judged intended).

**The false alarms** are s07, s09, s10, h10, c-s1, c-s3 and c-s5. In each, the twin recorded a real difference,
and it is the change the PR announces: a username starting with a digit now gets 201, the new stats endpoint now
answers, a username over 32 characters now gets 400. The earlier run recorded the same kinds of differences and the
judge called them intended. This time, in s07, s10, h10, c-s1 and c-s5, the second judge pass (asked without the
diff, and required to quote the description word for word) didn't confirm the announcement, and a change the
judge first called intended then counts as a regression. In s09 and c-s3 the first pass itself said regression.
In s10 every finding got flipped like that, including `404 -> 200` on the
new endpoint, which is exactly what the description says.

So the twin didn't get noisier, but the judge's call on announced changes is unstable from run to run. The same
strict second pass is what catches r01, r05, r09, r11, h04 and h05, where the first pass explains the change away
from the diff, so removing it would trade false alarms for misses.
