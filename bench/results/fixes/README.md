# Real fixes: would Doppel have flagged the change that let each bug in?

Each fix becomes the PR that brings its bug back (base = the fix, head = the commit before it). The tests the fix added or changed are put back as they were, so both worlds run the suite as it stood before anyone knew about the bug. Fixes and their routes were chosen before any ran.

**0 caught · 0 flagged elsewhere · 14 invisible · 0 not runnable** — of 14 run.

| Fix | App | Fixed on | Bug | Result | Tests | Requests | Detail |
|---|---|---|---|---|---|---|---|
| [fava-account-order](fava-account-order/doppel-check.html) | fava | 2025-10-13 | The account journal's entries are numbered and ordered wrongly when the report is paged. | invisible | 453 | 126 |  |
| [fava-int-lineno-meta](fava-int-lineno-meta/doppel-check.html) | fava | 2026-09-11 | Entries whose metadata holds an integer (lineno) can't be deserialised when saved through the API. | invisible | 543 | 143 |  |
| [fava-posting-documents](fava-posting-documents/doppel-check.html) | fava | 2026-05-17 | Document links attached to a posting miss entry_hash, and the backend can't resolve them. | invisible | 537 | 143 |  |
| [fava-import-dirs-spaces](fava-import-dirs-spaces/doppel-check.html) | fava | 2026-01-10 | An import directory option with spaces in its path is split into several directories. | invisible | 452 | 128 |  |
| [isso-stored-xss](isso-stored-xss/doppel-check.html) | isso | 2026-02-19 | Single quotes in website aren't escaped, and edits don't escape website or author at all (stored XSS). | invisible | 110 | 601 |  |
| [isso-no-cache](isso-no-cache/doppel-check.html) | isso | 2026-04-22 | Comment listings are sent without no-cache headers, so browsers and proxies show stale comments. | invisible | 116 | 629 |  |
| [privacyidea-userlist-scope](privacyidea-userlist-scope/doppel-check.html) | privacyidea | 2026-09-22 | An admin allowed to list one user or resolver can enumerate every user of the installation. | invisible | 4856 | 21 |  |
| [privacyidea-every-realm](privacyidea-every-realm/doppel-check.html) | privacyidea | 2026-09-21 | A policy for every realm ('*') resolves to no realm: bulk revoke answers 200 and revokes nothing. | invisible | 4857 | 21 |  |
| [prefect-zero-limit-pages](prefect-zero-limit-pages/doppel-check.html) | prefect | 2026-07-21 | Paginating flows, flow runs, task runs or deployments with limit=0 divides by zero. | invisible | 3122 | 1982 |  |
| [prefect-content-type](prefect-content-type/doppel-check.html) | prefect | 2026-03-26 | Requests from old clients without a Content-Type header are rejected instead of read as JSON. | invisible | 2906 | 1916 |  |
| [prefect-v2-trailing-slash](prefect-v2-trailing-slash/doppel-check.html) | prefect | 2026-05-12 | The redirect to the V2 UI drops the trailing slash and lands on the V1 app. | invisible | 3003 | 1974 |  |
| [prefect-redirect-host](prefect-redirect-host/doppel-check.html) | prefect | 2026-06-29 | The UI redirect's Location is absolute and names the internal host behind a reverse proxy. | invisible | 3108 | 1973 |  |
| [prefect-pending-graph](prefect-pending-graph/doppel-check.html) | prefect | 2026-06-15 | The flow run graph leaves out task runs that are still pending. | invisible | 3072 | 1969 |  |
| [prefect-state-data-get](prefect-state-data-get/doppel-check.html) | prefect | 2026-05-15 | Setting a state whose data isn't a dict crashes the orchestration rules (500). | invisible | 3016 | 1993 |  |

**History, 14 Oct 2026.** The first full run (bench-fixes #2) reported prefect-content-type as *caught* and three
other prefect fixes as *flagged elsewhere*. All four were list-order noise on prefect's filter endpoints (items with
equal sort keys coming back in another order), and the judge explained the one "catch" with a cause the change
can't have. Test mode now counts a reordering whose test passed on both sides instead of flagging it (a reordering a
test notices is still flagged: benchmark PR h02). The prefect fixes were rerun after that change (bench-fixes #3);
the other eight results are from #2. The benchmark's test-mode recall was rechecked after the change: unchanged
(bench/results/recheck-order).
