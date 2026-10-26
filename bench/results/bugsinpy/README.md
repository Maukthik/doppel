# Real bugs: Doppel test mode on BugsInPy's web-framework bugs

Each bug is a PR that brings a real, fixed bug back (base = the fix, head = the buggy commit), with the test written after the bug was found hidden. Python and libraries are the bug's own (BugsInPy).

**2 caught · 13 invisible (the suite never sent a request through the bug) · 22 not run** — of 37: 5 Sanic installs failed on the bug's own Python, and 17 runs broke before Doppel started (12 checkouts in this benchmark's harness, 5 sandbox connection errors). Only the 15 that ran are counted in the README.

Unconfirmed = differences seen once but not when their tests were rerun (dropped as noise by safeguard S5, kept in each bug's JSON under stats.confirmation.unconfirmed for audit).

| Bug | Python | Result | Tests run | Requests | Unconfirmed | Detail |
|---|---|---|---|---|---|---|
| fastapi #1 | 3.8 | caught | 787 | 828 | 6 | (test): this test no longer exists in head |
| fastapi #2 | 3.8 | invisible | 775 | 816 |  |  |
| fastapi #3 | 3.8 | invisible | 767 | 818 | 12 |  |
| fastapi #4 | 3.8 | invisible | 746 | 781 |  |  |
| fastapi #5 | 3.8 | invisible | 738 | 776 |  |  |
| fastapi #6 | 3.8 | invisible | 734 | 772 | 6 |  |
| fastapi #7 | 3.8 | not runnable |  |  |  | ContreeTransportError: ContreeTransportError(request=RequestInfo(url='https://api.tokenfac |
| fastapi #8 | 3.8 | not runnable |  |  |  | ContreeTransportError: ContreeTransportError(request=RequestInfo(url='https://api.tokenfac |
| fastapi #9 | 3.8 | not runnable |  |  |  | ContreeTransportError: ContreeTransportError(request=RequestInfo(url='https://api.tokenfac |
| fastapi #10 | 3.8 | not runnable |  |  |  | ContreeTransportError: ContreeTransportError(request=RequestInfo(url='https://api.tokenfac |
| fastapi #11 | 3.8 | not runnable |  |  |  | ContreeTransportError: ContreeTransportError(request=RequestInfo(url='https://api.tokenfac |
| fastapi #12 | 3.8 | invisible | 509 | 527 |  |  |
| fastapi #13 | 3.8 | invisible | 384 | 405 |  |  |
| fastapi #14 | 3.8 | invisible | 321 | 338 |  |  |
| fastapi #15 | 3.8 | invisible | 309 | 327 |  |  |
| fastapi #16 | 3.8 | invisible | 284 | 303 |  |  |
| tornado #1 | 3.7 | invisible | 983 | 790 |  |  |
| tornado #2 | 3.7 | caught | 978 | 668 |  | PUT /put: status 200 -> 0 (the base branch's test fails on this) |
| tornado #3 | 3.7 | invisible | 967 | 659 | 1 |  |
| tornado #4 | 3.7 | invisible | 965 | 651 |  |  |
| tornado #5 | 3.7 | not runnable |  |  |  | CalledProcessError: Command '['tar', '-x', '-C', '<repo>\\bench\\.cache\ |
| tornado #6 | 3.7 | not runnable |  |  |  | CalledProcessError: Command '['tar', '-x', '-C', '<repo>\\bench\\.cache\ |
| tornado #7 | 3.7 | not runnable |  |  |  | CalledProcessError: Command '['tar', '-x', '-C', '<repo>\\bench\\.cache\ |
| tornado #8 | 3.7 | not runnable |  |  |  | CalledProcessError: Command '['tar', '-x', '-C', '<repo>\\bench\\.cache\ |
| tornado #9 | 3.7 | not runnable |  |  |  | CalledProcessError: Command '['tar', '-x', '-C', '<repo>\\bench\\.cache\ |
| tornado #10 | 3.7 | not runnable |  |  |  | CalledProcessError: Command '['tar', '-x', '-C', '<repo>\\bench\\.cache\ |
| tornado #11 | 3.7 | not runnable |  |  |  | CalledProcessError: Command '['tar', '-x', '-C', '<repo>\\bench\\.cache\ |
| tornado #12 | 3.7 | not runnable |  |  |  | CalledProcessError: Command '['tar', '-x', '-C', '<repo>\\bench\\.cache\ |
| tornado #13 | 3.7 | not runnable |  |  |  | CalledProcessError: Command '['tar', '-x', '-C', '<repo>\\bench\\.cache\ |
| tornado #14 | 3.7 | not runnable |  |  |  | CalledProcessError: Command '['tar', '-x', '-C', '<repo>\\bench\\.cache\ |
| tornado #15 | 3.7 | not runnable |  |  |  | CalledProcessError: Command '['tar', '-x', '-C', '<repo>\\bench\\.cache\ |
| tornado #16 | 3.7 | not runnable |  |  |  | CalledProcessError: Command '['tar', '-x', '-C', '<repo>\\bench\\.cache\ |
| sanic #1 | 3.8 | not runnable |  |  |  | the install command failed: |
| sanic #2 | 3.8 | not runnable |  |  |  | the install command failed: |
| sanic #3 | 3.8 | not runnable |  |  |  | the install command failed: |
| sanic #4 | 3.8 | not runnable |  |  |  | the install command failed: |
| sanic #5 | 3.8 | not runnable |  |  |  | the install command failed: |
