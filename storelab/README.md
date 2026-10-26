# storelab/

Made-up stores for the demo and the tests. Every trick on these pages is deliberate; never imitate a real brand.
The backend serves them at `http://localhost:8787/storelab/…`. Only here may the hidden browser click through a checkout,
and it still stops before payment.

| Store | What it shows | Owner |
|---|---|---|
| `audiodealz/` | Hero demo: fake 40% off, resetting timer, pre-ticked $4.99 add-on, $37.01 of fees at checkout step 4 (real total $191.00). `cart.html` + `checkout-1..4.html` are the made-up checkout | Shriganesh |
| `store-a/` | Pinecrest Sound Co.: $239 + $13 shipping = $252 | Josh |
| `store-b/` | Second Spin Refurbs: $199 **refurbished** (not the same item) | Josh |
| `store-c/` | The honest store: $255 − $26 card offer = $229 (Day 9: $235 − $26 = $209) | Josh |
| `store-d/` | Ultra Bargain Zone 24: $189 scam store (skipped by the Price Agent) | Josh |
| `store-e/` | Maplehollow Hi-Fi: outage banner (skipped: site down) | Josh |
| `store-f/` | Markdown Mountain: $259 "was $399", a fake discount ($399 on 3 of 90 days) | Josh |
| `store-g/` | Kestrelwick Electronics: $219 shown, $29 of service + handling fees → $248 | Josh |
| `store-h/` | Tickdown Deals: countdown that restarts on every load + "Only 2 left", $245 + $9.99 shipping | Josh |
| `store-i/` | Quietlane Audio: clean store, $249 free shipping (also lists the older WH-1000XM4: a different model) | Josh |
| `generic-shop/` | A page shaped like a real checkout, used to test the extension's page reading | Shriganesh |

Each page's JSON-LD Product (model, SKU, price, condition) matches the seeded data in `backend/data/catalog.py`, which
`scripts/seed.py` writes to the Pricise Postgres that Zetaris queries. All stores are made up; all histories are synthetic.

**Test it:** start the backend (`scripts/run.sh`) and open the pages in Chrome with the extension loaded;
`scripts/test.sh` also runs the Deal Agent against `audiodealz/` and `store-c/` in a headless browser.
