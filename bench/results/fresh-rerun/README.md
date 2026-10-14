> **Rerun on 14 Oct 2026 with the current code** (bench-fresh, same pinned commits). These apps are not new to
> Doppel: their first run is in ../fresh/ (1 work · 0 noisy · 9 stopped), and what stopped there shaped later fixes.
> Only round2 (../round2/) is a first-sight result.

# Ten real Python web apps, never seen before

8 work · 0 noisy · 2 stopped

| App | Framework | Result | Tests | Requests | Reason |
|---|---|---|---|---|---|
| ctfd | Flask | works | 704 | 5301 |  |
| redash | Flask | stopped |  |  | no recorded requests |
| flaskbb | Flask | works | 776 | 80 |  |
| healthchecks | Django | works | 1773 | 1417 |  |
| wger | Django | works | 2359 | 3077 |  |
| paperless-ngx | Django | works | 3736 | 1466 |  |
| label-studio | Django | works | 1978 | 2720 |  |
| bakerydemo | Django (Wagtail) | works | 2 | 3 |  |
| mealie | FastAPI | works | 3245 | 3035 |  |
| fastapi-template | FastAPI | stopped |  |  | tests did not run |
