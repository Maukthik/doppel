# Real fixes, with the guided twin

The same PR as in [../README.md](../README.md): isso's stored-XSS fix (`2e58889`), replayed backwards
(base = the fix, head = the commit before it, the fix's own tests put back). This time it ran through
`doppel check` with both modes: the app's own tests, and Nemotron's invented users guided by an AI
reviewer's suspicions. Run on Nebius, 14 Oct 2026. Time-boxed to one day, at most two runs; this is the run.

## Setup

`doppel init` could not set isso up on its own, in two tries:
- First the sandbox lacked `pkg_resources` (isso of Feb 2026 imports it; Python 3.12 images ship no setuptools).
  Fixed in Doppel for both modes: the install adds `setuptools<81` when the app's code imports it.
- Then Nemotron's drafts got isso's database setup wrong, and the setup agent spent its steps on the compiled
  Markdown library (misaka).

So the twin's setup was written by hand, the way an app's maintainer writes it once, in
[bench/twin-setups/isso/](../../../twin-setups/isso/): a config file, a start script that serves isso on
`$PORT`, two threads and a reply seeded through isso's own API, and a guide for the invented users that
describes the API. It says nothing about the bug: no quotes, escaping or XSS. The same files went to base and
head. Before the run, the setup was checked by hand: a website containing a quote comes back escaped from
base and raw from head, on both `POST /new` and `PUT /id/<id>`, so the bug is reachable over HTTP.

## Result

| | |
|---|---|
| Test mode | 110 tests, 601 requests, **0 differences**: the suite never sends a quote in a website |
| The twin | 19 invented users, 44 requests, 32 served; **0 differences** |
| The reviewer (Nemotron Ultra, reading the diff) | **named the bug**: s1 "website field is now escaped with quote=False instead of quote=True", s4 "edit action no longer escapes author, email, website" |
| The twin's probes | **missed the trigger**: s1's probe sent `&` (escaped the same either way; only a quote differs); every edit probe used `PUT /id/<id>/edit/<hash>`, a route that answers 405, and no user reached the edit code (API.edit not reached) |
| Doppel's verdict | all 6 suspicions **untested**, none refuted, each with the reason |

So: the reviewer guessed right, and the twin could not prove it. The reviewer reads a diff that removes
`escape()` calls, so a suspicion here is no surprise; the twin's job was the proof, a recorded request, and it
didn't get one.

**What changed in Doppel because of it.** The report used to open with "Doppel: no regressions" even with
every suspicion untested, a pass it hadn't earned. It now reads "No regressions found, but 6 of the reviewer's
6 suspicions couldn't be tested", on a grey banner instead of a green one. The check page here is re-rendered
with that change; `report.md` is as the run wrote it.

**What would close the gap (not done before the deadline).** Probes are not rehearsed the way personas are:
a persona stuck at 401/403/404/405 on base goes back to Nemotron with the app's real answers and comes back
fixed, and a probe stuck at 405 should too. And a probe should send every input its trigger names (s1's
trigger named quotes; its probe sent only `&`). Both change the guided twin, so the Stage 5 benchmark would
have to be rerun to keep its numbers honest.

## Rerun with probe rehearsal (16 Oct 2026)

Run folder: [isso-stored-xss-rerun/](isso-stored-xss-rerun/doppel-check.html). Same PR and same hand-written
setup as above, with today's guided twin (probes stuck at 405 are rehearsed; probes are asked to send every value a
trigger names).

| | First run | Rerun |
|---|---|---|
| Probes reaching the edit code | none (all used `PUT /id/<id>/edit/<hash>`, 405) | yes: `PUT /id/<id>`, the real route |
| Probes sending a quote | none | none (the website probe sent `&`, the edit probes sent plain text) |
| Suspicions | 6 untested | 3 untested, **3 refuted** |
| Result | invisible | invisible |

Rehearsal fixed the route, so it does what it was built for. The input is still wrong: no probe sent a `'` or a
`<`, so the bug never showed. And this run is worse in one way. s1 ("quote=False instead of quote=True") was marked
**refuted** on a probe that sent only `&`, which escapes the same way in both versions, so it proves nothing about
quotes. A refutation should need a probe that actually sends the trigger. Doppel doesn't check that yet, and that's
a known limit.

## Runs 3 and 4 (17 Oct 2026): caught

Between run 2 and these runs, four changes went into Doppel. All are generic and none mentions isso:
- the reviewer has to name the exact inputs that the code before and after treats differently
  (quote=False vs quote=True differs on `'` and `"` only), and those values go to the probe writer;
- a "refuted" verdict has to name the triggering value, and a probe has to have really sent it;
- a probe answered with status 0 (its URL couldn't even be sent) counts as stuck and is rehearsed;
- (test mode) a failed package install is no longer hidden. This one fixes run 3, below.

**Run 3** ([isso-stored-xss-run3/](isso-stored-xss-run3/doppel-check.html)) doesn't count. Its test mode broke
because of a change made earlier that day (a failed `pip install -e .` was let through, so werkzeug was never
installed), and that change was reverted. Its twin was better than run 2's: the reviewer named double quotes,
but one probe's `"` made an invalid URL (400 in both versions) and another sent a name where an id belongs
(status 0), so 2 of 3 suspicions stayed untested.

**Run 4** ([isso-stored-xss-run4/](isso-stored-xss-run4/doppel-check.html)): **caught by invented users**.

| | |
|---|---|
| Test mode | 110 tests, 601 requests, 0 differences (same as every run: the suite never sends these inputs) |
| The twin | 18 users, 25 of 47 requests served; rehearsal repaired all 5 probes before the replay |
| Regression 1 | `POST /new` with website `https://example.com/"quoted"`: base stores `&quot;quoted&quot;`, head stores `"quoted"` raw |
| Regression 2 | `PUT /id/4` with author `Test <script>`: base returns `Test &lt;script&gt;`, head returns `Test <script>`, and `GET /id/4` shows it stored that way (stored XSS) |
| Suspicions | 2 confirmed (the two above), 3 refuted, each refutation naming the value its probe sent |

Disclosure: this PR has now been run four times, and Doppel changed between runs because of what the earlier
runs showed. Each change has its own tests and also applies to every other app, but the catch came on the
fourth try at this PR, after it had been seen. One of the three refutations is weak: the email refutation is
correct that responses are identical, but isso never returns email addresses, so no request could have shown
an email difference. And as before, the reviewer reads a diff that removes `escape()` calls, so its suspicion
is no surprise; what the twin adds is the recorded proof.

The 14 test-mode results in [../README.md](../README.md) are unchanged: test mode caught 0 of 14.
