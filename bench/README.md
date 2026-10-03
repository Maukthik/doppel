# Stage 5 benchmark: a real app, 30 pull requests

**Question:** on a real, well-tested open-source API, does Doppel catch regressions that the app's
own test suite misses, without crying wolf on safe changes? And does it beat simply asking an AI
to review the diff?

Results: [results/results.md](results/results.md)

## The app

[miguelgrinberg/microblog-api](https://github.com/miguelgrinberg/microblog-api) at commit `e250d71`:
a Flask + SQLAlchemy social API (users, login tokens, posts, follows, feed, pagination), written as
a reference project by the author of the Flask Mega-Tutorial. It has **44 tests**, and they all pass on
the base code. We did not change the app or its tests; Doppel only adds two files a user would add:

- `microblog/twin/doppel.toml`: how to install, seed, migrate and start the app
- `microblog/twin/doppel_seed.py`: a small social network (6 accounts with a known password,
  24 imported users, 150 posts, a follow graph). It runs once, so base and head start from the same data.

## The 30 pull requests (`microblog/prs/`)

Each PR is a patch plus a title and description (`prs/index.json`), written the way PR authors write:
the description says what the author *meant* to do.

- **20 regressions.** 3 are real upstream bug fixes, reverted (`5d704d1`, `4c5d5aa`, `beed1d9`). The other
  17 are planted, each inside a plausible change ("simplify the feed query", "cheaper count for
  pagination", "allow emails up to 254 characters"). They cover lost validation, wrong data,
  authorization slips, crashes, removed fields, a leaked password hash, and broken pagination.
- **10 safe PRs.** Pure refactors, docs, and behavior changes the description announces (new field,
  new endpoint, new migration, 403 to 404). If such a PR changes what an existing test expects,
  the PR updates that test, as a real author would.

`index.json` stores what each regression really does (`bug`) and the route where it shows
(`expect`, a regex). Neither is shown to Doppel or to the AI reviewer.

**Held-out set (10 PRs, `h01`–`h10`, `"set": "heldout"`).** Written on 3 Oct, after the results
below were in and after Doppel was tuned on them, and before the tuned version ran on them: 7 new
regressions of new kinds (lookup broken, feed order flipped, edits not saved, duplicate follow
accepted, a newly required field, tokens expiring at once, an inverted delete check) and 3 safe PRs.
Doppel improvements are judged on this set, so tuning to the main 30 can't inflate the result.
Run it with `--set heldout`.

## The four arms

| Arm | What it sees | Caught means |
|---|---|---|
| **Tests** | the PR's code, running the app's own 44 tests | any test fails |
| **AI review** | Nemotron 3 Ultra gets the full app source, the diff and the PR description, and answers `regression` or `safe` | it answers `regression` |
| **Doppel** | Nemotron 3.5 Lightning writes 12 personas from the source and the diff; the twin replays them on base and head in Nebius sandboxes; Nemotron 3 Ultra judges each change against the PR description | a change judged `regression` on a request matching the PR's `expect` route |
| **Review-guided Doppel** | Nemotron 3 Ultra first lists the regressions it suspects (like the AI review arm, but asked for recall); a probe persona is written for each suspicion next to the usual personas; the twin replays all of them; each suspicion ends up confirmed, refuted or untested | a change judged `regression` on a request matching the PR's `expect` route |

On safe PRs, a `regression` from the AI reviewer or from Doppel (on any route) is a false alarm.
Doppel's score is the strictest of the three: it has to find the change *and* the judge has to
call it a regression *and* it has to be on the right route. The AI reviewer only has to say
"regression"; we don't check that it named the right bug.

## Results (3 Oct)

**Main 30 PRs**

| | Regressions caught | False alarms on safe PRs | Cost per PR | Time per PR |
|---|---|---|---|---|
| App's own test suite (44 tests) | 12/20 | 0/10 | $0 (CI minutes) | 11 s |
| AI code review (Nemotron 3 Ultra) | 17/20 | 1/10 | $0.03 | 3 s |
| Doppel alone | 11/20 | 1/10 | $0.13 | 81 s |
| **Review-guided Doppel** | **16/20** | **0/10** | $0.25 | 147 s |
| App's tests + review-guided Doppel | **20/20** | 0/10 | | |
| AI review + review-guided Doppel | 19/20 | 1/10 | | |

**Held-out 10 PRs** (written after the main results were in)

| | Regressions caught | False alarms on safe PRs |
|---|---|---|
| App's own test suite | 6/7 | 0/3 |
| AI code review | 5/7 | 0/3 |
| **Review-guided Doppel** | **6/7** | **0/3** |

Per-PR tables and chart: [results/results.md](results/results.md). Every catch is backed by recorded
requests: the same synthetic user got a different answer from the PR than from main (for example
r02: `DELETE /api/tokens` with an empty `Bearer` header, 401 on main, 500 on the PR).

What the numbers say:
- **Doppel and the app's tests are complementary: together they catch all 20.** Every regression
  the 44 tests miss, review-guided Doppel caught.
- **The AI reviewer and the twin catch different things.** The reviewer missed r02 (a 500 crash)
  and r17 (every user object leaks its password hash; it called the PR "safe"); review-guided
  Doppel caught both. On the held-out set the reviewer missed h02 (feed order flipped); Doppel caught it.
- **Review-guided Doppel made no false alarms on 13 safe PRs; the reviewer made one** (s08: it
  claimed a refactor broke `limit=0`; the twin's probes sent `limit=0`, `limit=abc`, `limit=30` and
  no limit to both versions and got identical answers, so its suspicions were refuted).
- **Misses:** r08 (login by email), r14 (`is_following` flag) and r18 (`after` cursor) were never
  triggered by a synthetic user in the recorded run. r20 (page cap removed) was reached, but only with
  `limit=50` and `limit=100`, which the PR description allows; no persona asked for more than 100.
  h03 (edits answer 200 but aren't saved) was not observed in the recorded run.

### How these numbers were produced (read this before quoting them)

- **Runs vary.** Personas are sampled, so individual PRs flip between runs. Complete runs of the
  review-guided arm on the main 30 scored 15, 17, 16 and 15 of 20. Each PR's result file keeps the
  run it came from; a few (r11, h04) come from an earlier run because their latest rerun failed on a
  Token Factory or sandbox timeout, and the file records that error.
- **The judge was changed after the held-out set had run, and re-applied to the same recordings.**
  The first held-out run showed the judge calling unannounced changes "intended" (h04: following
  twice now answers 204). Our first fix (the judge must quote the PR description) backfired on the
  main set (12/20): the judge quoted sentences that describe the code edit ("removes the OR
  condition") as if they announced the effect. The judge now asks a second, narrower question with
  **only the PR description and the observed before/after, no diff**: would a reader of this
  description expect exactly this change? A change is "intended" only if that answer is yes and the
  quoted sentence is really in the description. `bench/rejudge.py` re-labelled every recorded
  finding with this judge, without rerunning the twin; each finding keeps its old label in `label_v1`.
- So the held-out set is **no longer a clean held-out test of the judge**: h04 moved from missed to
  caught by that change. It is still held out for the personas, the twin and the rehearsal, which
  were not changed after it ran. Without h04, review-guided Doppel is 5/6 on the held-out regressions.
- The re-judge turned "intended" into "regression" on 5 regressions (r01, r05, r09, r11, h04),
  removed the one false alarm (s09: the description does announce `location`), and created
  no new false alarm on the 13 safe PRs. It moved no PR from caught to missed.

## How we got here: what each failed run taught us (2 Oct)

The first full run scored Doppel **0 of 20**: it found no behavior change in any PR, not even the
safe ones that add a field. The personas were the problem, not the diff. Nemotron 3.5 Lightning
wrote logins as a JSON body, but this API wants HTTP Basic auth, so almost every request got a 401
in both worlds (on the PR that adds a `location` field, the app served 2 of 54 requests). Identical
401s look like "nothing changed".

What changed in Doppel because of it:
- **Rehearsal**: personas are replayed once on the base world first. Those stuck at 401/403/404/405
  go back to Nemotron with the app's actual answers, and come back fixed. Replaying the failed run's
  own personas locally, rehearsal fixed 9 of 9 stuck personas and the app served 27 of 54 requests
  instead of 2.
- **Coverage in every report**: "the app served X of Y requests". Under 30%, the PR comment says
  "couldn't test this PR" instead of "no behavior changes".
- **`[personas] guide`** in `doppel.toml`: a few lines from the app's owner on how to log in and which
  seeded accounts exist. For microblog it is written once and says nothing about any PR (see
  `microblog/twin/doppel.toml`).
- A persona that crashes or times out no longer sinks the whole run, and network timeouts are retried.

The second run (with rehearsal) still found nothing, and this time the twin wasn't the problem.
Replaying that run's own personas locally found 17 changes on the `location` PR; on Nebius, 0. The cause
was the benchmark harness: it applied each PR with `git apply` inside a folder of the doppel repo, and
inside another git repository `git apply` resolves paths from that repository's root, skips them, and
still exits 0. Every "head" was a copy of base, so the twin was correctly reporting that nothing
changed, and the AI reviewer was shown empty diffs. The harness now applies each patch in its own
repository and refuses to run a PR whose head is identical to base. Both runs' Doppel and AI-review
numbers were discarded; only the test-suite arm (run where patches applied correctly) carried over.

### The first valid run: standalone Doppel loses to an AI reviewer on recall

With the harness fixed, the first valid full run scored the app's tests 12/20, the AI reviewer
(Nemotron 3 Ultra reading the diff and the full source) **17/20 with 1 false alarm**, and standalone
Doppel **11/20 with 0 false alarms**. Doppel caught 5 of the 8 regressions the tests miss, including
the leaked password hash that the AI reviewer called "safe". Every Doppel miss was a coverage miss:
no persona sent the input that triggers the bug (revoking without a header, changing a password
without the old one, logging in by email, a placeholder like `{post_id}` left unfilled).

So the reviewer is good at *suspecting* and Doppel is good at *proving*. That led to the
review-guided arm: the reviewer lists what it suspects, Doppel writes a probe persona for each
suspicion, and the twin confirms or refutes it with recorded requests. The standalone numbers stay
in `results/` as the `doppel` arm.

## Honest limits

- We wrote the planted regressions. To avoid tuning them against either side, the full list of
  30 PRs was fixed (`prs/index.json`) before the app's tests or Doppel ran on any of them, and none was
  changed afterwards except to make two safe PRs update the tests their announced change affects
  and to fix the file format of one safe PR's migration.
- One app, one language, 30 PRs: this is evidence, not proof. The harness takes any app with a
  `doppel.toml` and a folder of patches, so it can be rerun on others.
- Models are non-deterministic. Each result file keeps the personas, every finding and every
  verdict, so any number in the table can be checked by hand.
- Doppel only sees what its personas do. A regression on a route no persona visits is missed;
  the results count it as missed.

## Run it

```bash
pip install -e ".[nebius,dev]"            # from the repo root; .env holds NEBIUS_API_KEY and NEBIUS_PROJECT_ID
python bench/run_bench.py --arms doppel,review   # all 30 PRs, about $2-3 of credits
python bench/rejudge.py                    # optional: re-label recorded findings with the current judge
python bench/summarize.py                  # -> results/results.md and results/chart.svg
```

The `tests` arm needs the app's requirements (`pip install -r bench/.cache/base/requirements.txt`)
and is already recorded in `results/`. Every arm saves after each PR and is skipped on rerun, so
an interrupted run resumes where it stopped (`--force` redoes it, `--only r05-feed-own-posts` runs one).
