> **Rerun on 14 Oct 2026 with the current code** (bench-fresh, same pinned commits). These apps are not new to
> Doppel: their first run is in ../heldout/ (3 work · 0 noisy · 7 stopped), and what stopped there shaped later fixes.
> Only round2 (../round2/) is a first-sight result.

# Ten held-out apps, chosen before the setup layer was rebuilt

5 work · 0 noisy · 5 stopped

| App | Framework | Result | Tests | Requests | Reason |
|---|---|---|---|---|---|
| ihatemoney | Flask | works | 151 | 728 |  |
| searxng | Flask | works | 342 | 34 |  |
| changedetection | Flask | stopped |  |  | install |
| babybuddy | Django | stopped |  |  | tests did not run |
| pretalx | Django | works | 10214 | 3011 |  |
| tandoor | Django | works | 1277 | 1207 |  |
| inventree | Django | stopped |  |  | tests did not run |
| lnbits | FastAPI | works | 450 | 1149 |  |
| polar | FastAPI | stopped |  |  | needs a service |
| fastapi-best-architecture | FastAPI | stopped |  |  | needs a service |
