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
