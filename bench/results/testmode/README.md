# Test mode on microblog-api: raw differences, no judge (local run, 8 Oct 2026)

`doppel tests` with **no setup**: the test command (`pytest -p no:logging`) and install come from the repo's
own `tox.ini`. Every PR ran the base branch's 44 tests in three worlds (base, base again, head). This run
is **local and unjudged**: a row counts any difference, so safe PRs that change behavior on purpose show
differences here that the Nemotron judge is meant to clear. The judged run on Nebius is the result to quote.

| PR | Kind | Differences | Tests failing | Traffic only | Stage 5: tests | review | guided twin |
|---|---|---|---|---|---|---|---|
| r01-email-lookup | regression | 3 | 1 |  | ✅ | ✅ | ✅ |
| r02-revoke-auth | regression | 0 | 0 |  | · | · | ✅ |
| r03-auth-error-field | regression | 13 | 0 | yes | · | ✅ | ✅ |
| r04-pagination-count | regression | 13 | 7 |  | ✅ | ✅ | ✅ |
| r05-feed-own-posts | regression | 3 | 2 |  | ✅ | ✅ | ✅ |
| r06-password-change | regression | 6 | 1 |  | ✅ | ✅ | ✅ |
| r07-edit-own-post | regression | 3 | 1 |  | ✅ | ✅ | ✅ |
| r08-login-by-email | regression | 7 | 2 |  | ✅ | ✅ | · |
| r09-post-length | regression | 0 | 0 |  | · | ✅ | ✅ |
| r10-post-201 | regression | 14 | 3 |  | ✅ | ✅ | ✅ |
| r11-email-length | regression | 0 | 0 |  | · | ✅ | ✅ |
| r12-posts-url | regression | 38 | 0 | yes | · | ✅ | ✅ |
| r13-unfollow-all | regression | 0 | 0 |  | · | ✅ | ✅ |
| r14-is-followed | regression | 12 | 1 |  | ✅ | ✅ | · |
| r15-followers-swapped | regression | 7 | 2 |  | ✅ | ✅ | ✅ |
| r16-user-posts-404 | regression | 2 | 1 |  | ✅ | ✅ | ✅ |
| r17-hash-leak | regression | 38 | 0 | yes | · | · | ✅ |
| r18-after-cursor | regression | 1 | 1 |  | ✅ | ✅ | · |
| r19-delete-missing | regression | 0 | 0 |  | · | ✅ | ✅ |
| r20-page-size | regression | 2 | 1 |  | ✅ | · | · |
| s01-rename-vars | safe | 0 | 0 |  | · | · | · |
| s02-docstrings | safe | 0 | 0 |  | · | · | · |
| s03-hide-others-posts | safe | 4 | 1 |  | · | · | · |
| s04-no-self-follow | safe | 0 | 0 |  | · | · | · |
| s05-posts-count | safe | 38 | 0 | yes | · | · | · |
| s06-longer-posts | safe | 0 | 0 |  | · | · | · |
| s07-username-digits | safe | 3 | 1 |  | · | · | · |
| s08-pagination-helper | safe | 0 | 0 |  | · | ✅ | · |
| s09-location | safe | 38 | 0 | yes | · | · | · |
| s10-user-stats | safe | 0 | 0 |  | · | · | · |
| h01-lookup-by-email | regression | 2 | 1 |  | ✅ | ✅ | ✅ |
| h02-feed-order | regression | 2 | 1 |  | ✅ | · | ✅ |
| h03-edit-not-saved | regression | 0 | 0 |  | · | · | · |
| h04-follow-twice | regression | 11 | 1 |  | ✅ | ✅ | ✅ |
| h05-about-me-required | regression | 25 | 3 |  | ✅ | ✅ | ✅ |
| h06-token-lifetime | regression | 26 | 5 |  | ✅ | ✅ | ✅ |
| h07-delete-check | regression | 6 | 2 |  | ✅ | ✅ | ✅ |
| h08-rename-token-helper | safe | 0 | 0 |  | · | · | · |
| h09-feed-docs | safe | 0 | 0 |  | · | · | · |
| h10-shorter-usernames | safe | 1 | 0 | yes | · | · | · |

- Regressions with a difference: **21/27**, of which **3** with every test still passing (r03, r12, r17: the tests' assertions never looked).
- No difference: r02-revoke-auth, r09-post-length, r11-email-length, r13-unfollow-all, r19-delete-missing, h03-edit-not-saved — the suite never sends the requests these PRs changed; the guided HTTP twin caught 5 of these 6 in Stage 5.
- Safe PRs with a difference: 5/13 (s03-hide-others-posts, s05-posts-count, s07-username-digits, s09-location, h10-shorter-usernames), all intended behavior changes the judge must clear.
- A/A (base against itself): 0 differences; noise found and ignored: `access_token`, `refresh_token`, `timestamp`, `first_seen`, `last_seen`.
