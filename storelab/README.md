# storelab/

Made-up stores for the demo and the tests. Every trick on these pages is deliberate; never imitate a real brand.
The backend serves them at `http://localhost:8787/storelab/…`. Only here may the hidden browser click through a checkout,
and it still stops before payment.

| Store | What it shows | Owner |
|---|---|---|
| `audiodealz/` | Hero demo: fake 40% off, resetting timer, pre-ticked $4.99 add-on, $37.01 of fees at checkout step 4 (real total $191.00). `cart.html` + `checkout-1..4.html` are the made-up checkout | Shriganesh |
| `store-c/` | The honest store: $255 − $26 card offer = $229 | Josh |
| `generic-shop/` | A page shaped like a real checkout, used to test the extension's page reading | Shriganesh |
| Stores A–I | Planned (Store Lab with switchable tricks) | Josh |

**Test it:** start the backend (`scripts/run.sh`) and open the pages in Chrome with the extension loaded;
`scripts/test.sh` also runs the Deal Agent against `audiodealz/` and `store-c/` in a headless browser.
