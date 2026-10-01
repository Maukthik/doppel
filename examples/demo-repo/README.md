# Demo repo kit: see Doppel comment on a real pull request

1. Create a new public GitHub repo, e.g. `doppel-shop-demo`.
2. Copy everything in `main/` into it. Move `doppel.yml` to `.github/workflows/doppel.yml`.
   Commit to `main` and push.
3. In the demo repo: Settings → Secrets and variables → Actions → add `NEBIUS_API_KEY` and
   `NEBIUS_PROJECT_ID`.
4. Create a branch `show-stock`, replace `app.py` with `pr/app.py`, push, and open a pull request titled
   "Show stock status on products" with this description:
   > Adds an `in_stock` flag to every product so the storefront can grey out sold-out items.
   > Also tidies up order total and validation code. No other behavior changes intended.
5. Within a couple of minutes the Doppel check runs and comments: 2 regressions the PR doesn't mention
   (coupon totals, quantity 0 accepted), `in_stock` folded away as intended. The check fails.
6. Fix the two bugs in the branch and push: the same comment updates and the check goes green.

Delete `doppel-personas.json` to let Nemotron invent the users instead.
