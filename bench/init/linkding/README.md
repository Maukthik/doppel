## What `doppel init` produced (8 Oct 2026, Nebius, no hand edits)

`generated/` holds the files `doppel init --backend nebius --trace` wrote for linkding at `27b7303`:
5 invented users replayed, 64% of their requests served, and calibration found exactly the three
noise keys found by hand (`date_added`, `date_modified`, `web_archive_snapshot_url`). The login matches
the answer key's approach: a fixed `Token` header for a seeded ApiToken.

It took 15 attempts over four runs. Between runs, the traces showed four gaps in what init told the
model, each fixed in Doppel itself: UNIQUE failures now show the model class behind the table; long
tracebacks keep the seed's own failing line; NOT NULL failures are explained with the model's
definition; and a login the app never read (HTTP Basic against a token-only API) is no longer blamed
on the password. The final run needed 2 attempts.