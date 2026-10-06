# Answer key: linkding

A hand-written twin spec for [sissbruecker/linkding](https://github.com/sissbruecker/linkding) at
commit `27b7303` (13 Sep 2026), used to judge what `doppel init` produces for the same repo.

Checked with the local backend (`bootstrap.try_files` with `users.json` as the invented users):
the smoke login passed and calibration found exactly `date_added`, `date_modified` and
`web_archive_snapshot_url` as noise, the three keys found by hand.
