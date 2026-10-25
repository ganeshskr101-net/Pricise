# storelab/

Made-up stores for the demo and the tests. Every trick on these pages is deliberate; never imitate a real brand.
The backend serves them at `http://localhost:8787/storelab/…`. Only here may the hidden browser click through a checkout,
and it still stops before payment.

| Store | What it shows | Owner |
|---|---|---|
| `audiodealz/` | Hero demo: fake 40% off, resetting timer, pre-ticked $4.99 add-on, $37.01 of fees at checkout step 4 (real total $191.00). `cart.html` + `checkout-1..4.html` are the made-up checkout | Shriganesh |
| `store-c/` | The honest store: $255 − $26 card offer = $229 | Josh |
| `generic-shop/` | A page shaped like a real checkout, used to test the extension's page reading | Shriganesh |
| `store-d/` | The scam store ($189) the Price Agent proposes and the Trust Agent blocks. Minimal page; Josh can restyle it | Josh (data: Raj) |
| `_official/` | The manufacturer photo every honest store may reuse; the Trust Agent never counts it as copied | Raj |
| Stores A–I | Planned (Store Lab with switchable tricks) | Josh |

**Test it:** start the backend (`scripts/run.sh`) and open the pages in Chrome with the extension loaded;
`scripts/test.sh` also runs the Deal Agent against `audiodealz/` and `store-c/` in a headless browser.

**Trust data:** every store the Trust Agent checks has `about.json` (`name, domain, domain_created, as_of, address, company_number`),
`returns.html`, `listings.json` (`image_url` of each product photo) and `reviews.json` next to its `index.html`
(`audiodealz/`, `store-c/`, `store-d/` today). Give honest stores their own photo; scam stores reuse another store's.
`python bench/trust/make_fixtures.py` regenerates these files without touching any `index.html` except Store D's.
