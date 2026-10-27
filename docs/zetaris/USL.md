# Pricise on Zetaris: the `pricise_usl` Unified Semantic Layer

Built and run on 27 Oct 2026 against the team's Zetaris install. Every result below came from that install; nothing is typed in by hand.

## Use case

A shopper is on a real product page (the demo: **JBL Flip 6, Blue on amazon.com, $114.95, List Price $129.95**). Pricise
answers "Best safe price?": the same item, new, at a full price, from a credible retailer whose domain is old enough to trust.
That takes three sources that live in different places:

| Source | What it gives | How Zetaris reads it |
|---|---|---|
| (a) **Pricise Postgres** (`pricise_pg`) | prices the shopper saw (`price_observations`), cached Google Shopping offers including retailers' sellers lists (`shopping_offers`), every check's findings (`findings`), test stores (`stores`) | JDBC datasource (`scripts/register_zetaris.py`) |
| (b) **Public RDAP** (Verisign, .com) | when amazon.com, woot.com, walmart.com, bestbuy.com, target.com and jbl.com were registered | six Lightning REST tables, cached |
| (c) **SerpApi Google Shopping** | one live search for the demo product ("JBL Flip 6 Blue"): 40 offers | one Lightning REST table, cached; the SerpApi key lives only in Zetaris's table definition |

The USL joins them into one table, `safe_price`: one row per (product, retailer) offer with its price, condition, seller,
warranty note, domain age from RDAP, the prices Pricise has seen, the credible stores' median, and `is_safe`. The Price Agent
reads it (`backend/data/repo.py: usl_offers`) and falls back to its own search when Zetaris can't answer; its trace lines name
`pricise_usl.safe_price`.

Source (c) is a cached snapshot, not a live search, so every place that shows its prices (the evidence page's Price
comparison, the side panel's best-price card, the Deal Agent's discount line) says when it was fetched: the search's own
`search_metadata.created_at`, read with `SELECT search_metadata.created_at AS created FROM pricise_rest.serp_jbl_flip6`
(a read of the cached table, no new SerpApi search; remembered for 10 minutes). Rows from Pricise's cache use the newest
`shopping_offers.fetched_at`.

What the USL does **not** decide: whether an offer is the *same item* (edition, colour, model name, bundles) and whether an
unusually low offer is approved. Those stay with the Price Agent's matching rules and the Skeptic (Nemotron Ultra, checked by
hard rules), which read these rows. So `is_safe = 1` means "credible store, new, full price, a real price, domain over a year
old", not "approved".

## Setup

Run statement by statement with `scripts/zetaris_usl.py run N` from [`pricise_usl.sql`](pricise_usl.sql)
(`scripts/zetaris_usl.py show` prints them with the key masked). All 22 ran:

| # | Statement | Result |
|---|---|---|
| 1 | `CREATE LIGHTNING DATABASE PRICISE_REST` | ok, 0.6 s |
| 2–7 | `CREATE LIGHTNING REST TABLE rdap_amazon / rdap_woot / rdap_walmart / rdap_bestbuy / rdap_target / rdap_jbl` | ok, 0.2–1.7 s each |
| 8–13 | `CACHE TABLE pricise_rest.rdap_*` | ok, 0.7–3.8 s each |
| 14 | `CREATE LIGHTNING REST TABLE serp_jbl_flip6` (endpoint `https://serpapi.com/search.json?engine=google_shopping&q=JBL+Flip+6+Blue&gl=us&hl=en&api_key=***`) | ok, 1.1 s |
| 15 | `CACHE TABLE pricise_rest.serp_jbl_flip6` | ok, 1.0 s |
| 16–17 | `CREATE NAMESPACE IF NOT EXISTS lightning.metastore` / `lightning.metastore.pricise` | ok |
| 18 | `COMPILE USL IF NOT EXISTS pricise_usl …` (two tables) | ok on the third try, 1.7 s (see "What went wrong") |
| 19 | `UPDATE USL … SET DESCRIPTION` | ok |
| 20 | `ACTIVATE USL TABLE …retailer_domain` | ok after adding `COALESCE` for the NOT NULL key |
| 21 | `ACTIVATE USL TABLE …safe_price` (a `WITH` query: REST + JDBC) | ok; re-activated twice (fixes below) |
| 22 | `REGISTER DQ new_full_price …` | ok, 9.6 s |

### DDL

```sql
COMPILE USL IF NOT EXISTS pricise_usl DEPLOY NAMESPACE lightning.metastore.pricise DDL
CREATE TABLE retailer_domain (
    store_domain varchar(100) NOT NULL PRIMARY KEY,
    registered varchar(10),
    age_days int
);

CREATE TABLE safe_price (
    offer_id varchar(220) NOT NULL PRIMARY KEY,
    product_key varchar(80),
    model_key varchar(80),
    origin varchar(20),            -- serpapi_live (the REST table) or pricise_cache (pricise_pg.shopping_offers)
    offer_source varchar(120),     -- the store name Google Shopping shows ("Walmart - JBL Store")
    retailer varchar(60),
    store_domain varchar(100),
    seller varchar(80),            -- a marketplace seller on the retailer, if any
    title varchar(200),
    price decimal(10,2),
    item_condition varchar(40),    -- new | open box | refurbished | used
    full_price int,                -- 0 for monthly / installment / trade-in prices
    warranty_note varchar(200),
    domain_registered varchar(10),
    domain_age_days int,
    observed_min decimal(10,2),    -- prices Pricise has seen for this model
    observed_median decimal(10,2),
    credible_median decimal(10,2), -- median of the credible stores' new, full prices for this product
    unusually_low int,             -- 50-70% of that median: for the Skeptic to review
    is_safe int
);
```

The activation queries (statements 20 and 21) are in [`pricise_usl.sql`](pricise_usl.sql). `safe_price` flattens the SerpApi
response with `LATERAL VIEW explode(shopping_results)`, takes the newest cached search per product from `shopping_offers`
(a window function), maps store names to retailer domains, splits marketplace sellers ("Walmart - OneCall" -> seller
"OneCall"), classifies condition and full price, and joins RDAP registration dates and Pricise's observed prices.

## Queries and results

Saved in [`queries/`](queries/), results as JSON in [`results/`](results/) (`scripts/zetaris_usl.py query <file>`).

### Source exploration
#### `pg_observations_latest` (1.3 s)

```sql
SELECT host, name, price, condition, safe, from_unixtime(seen_at) AS seen FROM pricise_pg.price_observations ORDER BY seen_at DESC LIMIT 5
```

| host | name | price | condition | safe | seen |
|---|---|---|---|---|---|
| amazon.com | Amazon Kindle Paperwhite Signature Edition 32GB (2024 model) | 249.990000000000000000 | new | good | 2026-10-27 06:06:49 |
| amazon.com | Apple AirPods Pro 3 Wireless Earbuds with Active Noise Cance | 199.990000000000000000 | new | good | 2026-10-27 05:43:42 |
| amazon.com | Chat history | 199.990000000000000000 | new | good | 2026-10-27 05:43:29 |
| ebay.com | 5 results for Amazon Kindle Paperwhite Signature Edition 32G | 149.990000000000000000 | new | good | 2026-10-27 05:40:00 |
| walmart.com | Amazon Kindle Paperwhite Signature Edition 32GB (2024 model) | 18 | new | good | 2026-10-27 05:39:46 |
_5 rows._

#### `pg_offers_by_query` (0.5 s)

```sql
SELECT query_key, count(*) AS offers, from_unixtime(max(fetched_at)) AS newest FROM pricise_pg.shopping_offers GROUP BY query_key ORDER BY newest DESC LIMIT 10
```

| query_key | offers | newest |
|---|---|---|
| appleairpodspro3wirelessearbudswithactivesellers | 3 | 2026-10-27 05:43:42 |
| appleairpodspro3wirelessearbudswithactive | 40 | 2026-10-27 05:43:41 |
| appleairpodspro2wirelessearbudssellers | 1 | 2026-10-27 05:35:41 |
| appleairpodspro2wirelessearbuds | 40 | 2026-10-27 05:35:40 |
| amazonkindlepaperwhitesignatureedition32gbsellers | 3 | 2026-10-27 05:27:54 |
| amazonkindlepaperwhitesignatureedition32gb | 80 | 2026-10-27 04:56:50 |
| thisstoreisunavailable | 2 | 2026-10-27 04:52:14 |
| searchresults | 40 | 2026-10-27 04:52:11 |
| amazonb0c8rr4wn3 | 40 | 2026-10-27 04:51:08 |
| factoryamazonkindlepaperwhitesignatureedition | 40 | 2026-10-27 04:50:56 |
_10 rows._

#### `pg_findings_by_agent` (0.2 s)

```sql
SELECT agent, status, count(*) AS n FROM pricise_pg.findings GROUP BY agent, status ORDER BY agent, status
```

| agent | status | n |
|---|---|---|
| deal | confirmed | 51 |
| deal | proposed | 21 |
| deal | unverified | 19 |
| page | confirmed | 54 |
| price | challenged | 41 |
| price | confirmed | 62 |
| price | proposed | 24 |
| price | rejected | 45 |
| price | unverified | 78 |
| trust | confirmed | 2 |
| trust | proposed | 69 |
| trust | unverified | 39 |
_12 rows._

#### `pg_stores_by_kind` (0.2 s)

```sql
SELECT kind, count(*) AS n FROM pricise_pg.stores GROUP BY kind
```

| kind | n |
|---|---|
| storelab | 10 |
| bench | 1 |
_2 rows._

#### `rdap_registration_dates` (0.5 s)

```sql
SELECT lower(`ldhName`) AS domain, substr(e.`eventDate`, 1, 10) AS registered FROM pricise_rest.rdap_woot LATERAL VIEW explode(events) AS e WHERE e.`eventAction` = 'registration' UNION ALL SELECT lower(`ldhName`), substr(e.`eventDate`, 1, 10) FROM pricise_rest.rdap_walmart LATERAL VIEW explode(events) AS e WHERE e.`eventAction` = 'registration' UNION ALL SELECT lower(`ldhName`), substr(e.`eventDate`, 1, 10) FROM pricise_rest.rdap_amazon LATERAL VIEW explode(events) AS e WHERE e.`eventAction` = 'registration'
```

| domain | registered |
|---|---|
| woot.com | 2000-01-12 |
| walmart.com | 1995-02-23 |
| amazon.com | 1994-11-01 |
_3 rows._

#### `rdap_woot_events` (0.2 s)

```sql
SELECT e.`eventAction` AS action, e.`eventDate` AS at FROM pricise_rest.rdap_woot LATERAL VIEW explode(events) AS e
```

| action | at |
|---|---|
| registration | 2000-01-12T21:41:49Z |
| expiration | 2027-01-12T21:41:49Z |
| last changed | 2025-10-31T17:06:07Z |
| last update of RDAP database | 2026-10-27T08:01:43Z |
_4 rows._

#### `rdap_bestbuy_status` (0.3 s)

```sql
SELECT lower(`ldhName`) AS domain, s AS status FROM pricise_rest.rdap_bestbuy LATERAL VIEW explode(status) AS s
```

| domain | status |
|---|---|
| bestbuy.com | client delete prohibited |
| bestbuy.com | client transfer prohibited |
| bestbuy.com | client update prohibited |
| bestbuy.com | server delete prohibited |
| bestbuy.com | server transfer prohibited |
| bestbuy.com | server update prohibited |
_6 rows._

#### `serp_cheapest` (1.7 s)

```sql
SELECT r.position, r.source, r.extracted_price AS price, r.second_hand_condition AS cond, substr(r.title, 1, 60) AS title FROM pricise_rest.serp_jbl_flip6 LATERAL VIEW explode(shopping_results) AS r ORDER BY r.extracted_price LIMIT 10
```

| position | source | price | cond | title |
|---|---|---|---|---|
| 8 | eBay - littlefoote | 34.99 | pre-owned | Jbl Flip 6 Portable Speaker System - Blue W/case. Not Workin |
| 29 | eBay | 49.99 | pre-owned | Jbl Flip 6 Portable Waterproof Bluetooth Speaker Camo |
| 33 | PayMore University City | 56.99 | pre-owned | Jbl Flip 6 Portable Waterproof Bluetooth Speaker Camo JBLFLI |
| 40 | Mercari | 59.0 |  | JBL BRAND NEW Flip 6 Bluetooth Speaker Blue - New Electronic |
| 6 | eBay - bayoumy | 69.99 | pre-owned | Flip 6 Portable Waterproof Speaker - Blue / Bluetooth |
| 17 | eBay | 70.0 |  | JBL Flip 6 Portable Bluetooth Wireless Speaker, Blue |
| 37 | Mercari | 70.0 | pre-owned | JBL Flip6 Wireless Portable Bluetooth Speaker Waterproof & D |
| 38 | Poshmark | 70.0 |  | Jbl Portable Audio & Video / Jbl Flip 6 Waterproof Portable  |
| 2 | eBay - da-kar | 74.99 | pre-owned | Jbl Flip 6 Portable Waterproof Bluetooth Speaker Blue |
| 26 | Artblossom | 75.0 |  | JBL Flip 6 |
_10 rows._

#### `serp_by_condition` (0.6 s)

```sql
SELECT coalesce(r.second_hand_condition, 'new (not stated)') AS cond, count(*) AS offers, min(r.extracted_price) AS lowest FROM pricise_rest.serp_jbl_flip6 LATERAL VIEW explode(shopping_results) AS r GROUP BY coalesce(r.second_hand_condition, 'new (not stated)')
```

| cond | offers | lowest |
|---|---|---|
| new (not stated) | 28 | 59.0 |
| pre-owned | 11 | 34.99 |
| refurbished | 1 | 188.99 |
_3 rows._

#### `serp_woot_and_walmart` (0.2 s)

```sql
SELECT r.source, r.extracted_price AS price, r.extracted_old_price AS was, substr(r.title, 1, 60) AS title FROM pricise_rest.serp_jbl_flip6 LATERAL VIEW explode(shopping_results) AS r WHERE r.source IN ('Woot', 'Walmart')
```

| source | price | was | title |
|---|---|---|---|
| Walmart | 114.95 | 130 | JBL FLIP 6 Bluetooth Portable Waterproof Speaker |
| Woot | 79.95 |  | (NEW) JBL Flip 6 Portable IP67 Waterproof Speaker |
_2 rows._

### USL queries
#### `usl_tables` (0.1 s)

```sql
SHOW NAMESPACES OR TABLES IN lightning.metastore.pricise
```

| name | type | usl_id | count | activated |
|---|---|---|---|---|
| pricise_usl | usl | 1020 | 2 |  |
_1 row._

#### `cheapest_safe_offer` (3.5 s)

```sql
SELECT retailer, price, item_condition, warranty_note, domain_age_days, credible_median, unusually_low FROM lightning.metastore.pricise.pricise_usl.safe_price WHERE product_key = 'jblflip6blue' AND is_safe = 1 ORDER BY price LIMIT 3
```

| retailer | price | item_condition | warranty_note | domain_age_days | credible_median | unusually_low |
|---|---|---|---|---|---|---|
| Woot | 79.95 | new | 90-day Woot warranty, not the JBL manufacturer warranty (usu | 9785 | 97.45 | 0 |
| Walmart | 114.95 | new |  | 11569 | 97.45 | 0 |
_2 rows._

#### `retailers_by_domain_age` (1.0 s)

```sql
SELECT store_domain, registered, age_days FROM lightning.metastore.pricise.pricise_usl.retailer_domain ORDER BY age_days DESC
```

| store_domain | registered | age_days |
|---|---|---|
| bestbuy.com | 1994-03-03 | 11926 |
| amazon.com | 1994-11-01 | 11683 |
| walmart.com | 1995-02-23 | 11569 |
| jbl.com | 1995-03-21 | 11543 |
| target.com | 1997-01-02 | 10890 |
| woot.com | 2000-01-12 | 9785 |
_6 rows._

#### `unusually_low_offers` (4.5 s)

```sql
SELECT retailer, price, credible_median, round(price / credible_median, 2) AS share_of_median, warranty_note, is_safe FROM lightning.metastore.pricise.pricise_usl.safe_price WHERE unusually_low = 1
```

_0 rows._

#### `offers_by_origin_and_safety` (4.9 s)

```sql
SELECT origin, is_safe, count(*) AS offers FROM lightning.metastore.pricise.pricise_usl.safe_price GROUP BY origin, is_safe ORDER BY origin, is_safe
```

| is_safe | offers |
|---|---|
| 0 | 316 |
| 1 | 13 |
| 0 | 38 |
| 1 | 2 |
_4 rows._

#### `observed_vs_offers` (4.1 s)

```sql
SELECT retailer, price, observed_min, observed_median FROM lightning.metastore.pricise.pricise_usl.safe_price WHERE is_safe = 1 ORDER BY price
```

| retailer | price | observed_min | observed_median |
|---|---|---|---|
| Walmart | 18.00 | 199.99 | 199.99 |
| Walmart | 19.99 | 199.99 | 199.99 |
| Woot | 79.95 |  |  |
| Walmart | 114.95 |  |  |
| Target | 199.99 |  |  |
| Best Buy | 215.49 |  |  |
| Best Buy | 239.00 | 199.99 | 199.99 |
| Best Buy | 249.99 |  |  |
| Best Buy | 249.99 | 199.99 | 199.99 |
| Best Buy | 249.99 | 199.99 | 199.99 |
| Best Buy | 249.99 | 18.00 | 249.99 |
| Best Buy | 249.99 | 18.00 | 249.99 |
_15 rows; first 12 shown._

### Data-quality queries
#### `dq_rules` (0.2 s)

```sql
LIST DQ USL lightning.metastore.pricise.pricise_usl
```

| name | table | type | expression |
|---|---|---|---|
| store_domain | retailer_domain | Primary key constraints |  |
| offer_id | safe_price | Primary key constraints |  |
| new_full_price | safe_price | Custom Data Quality | price > 0 AND item_condition = 'new' AND full_price = 1 |
_3 rows._

#### `dq_run` (8.8 s)

```sql
RUN DQ new_full_price TABLE lightning.metastore.pricise.pricise_usl.safe_price
```

| name | table | type | total_record | valid_record | invalid_record |
|---|---|---|---|---|---|
| new_full_price | safe_price | Custom Data Quality | 369 | 309 | 60 |
_1 row._

#### `dq_invalid` (4.7 s)

```sql
SHOW DQ INVALID RECORD new_full_price TABLE lightning.metastore.pricise.pricise_usl.safe_price
```

_All products: 60 rows. JBL Flip 6 rows below._

| offer_source | price | item_condition | full_price | title |
|---|---|---|---|---|
| eBay - da-kar | 74.99 | used | 1 | Jbl Flip 6 Portable Waterproof Bluetooth Speaker Blue |
| eBay - bayoumy | 69.99 | used | 1 | Flip 6 Portable Waterproof Speaker - Blue / Bluetooth |
| eBay - littlefoote | 34.99 | used | 1 | Jbl Flip 6 Portable Speaker System - Blue W/case. Not Workin |
| eBay | 85.00 | used | 1 | Jbl Flip 6 Portable Waterproof Bluetooth Speaker - Navy Blue |
| mercari.com | 79.99 | used | 1 | Brand New Navy Blue JBL Flip 6 – Portable Waterproof Bluetoo |
| Poshmark | 79.00 | used | 1 | JBL Flip 6 Portable |
| eBay | 80.00 | used | 1 | JBL Flip 6 bluetooth speaker - Blue |
| eBay | 49.99 | used | 1 | Jbl Flip 6 Portable Waterproof Bluetooth Speaker Camo |
| Mercari | 116.00 | used | 1 | JBL JLB Flip 6 - Electronics |
| eBay - focuscamera | 188.99 | refurbished | 1 | JBL Charge 6 Portable Bluetooth Speaker Waterproof |
| PayMore University City | 56.99 | used | 1 | Jbl Flip 6 Portable Waterproof Bluetooth Speaker Camo JBLFLI |
| Mercari | 70.00 | used | 1 | JBL Flip6 Wireless Portable Bluetooth Speaker Waterproof & D |
_12 rows._

#### `dq_valid_count` (4.8 s)

```sql
SHOW DQ VALID RECORD new_full_price TABLE lightning.metastore.pricise.pricise_usl.safe_price
```

_All products: 309 rows. JBL Flip 6 rows below._

| offer_source | price | item_condition | title |
|---|---|---|---|
| Walmart | 114.95 | new | JBL FLIP 6 Bluetooth Portable Waterproof Speaker |
| Walmart - OneCall (Authorized Dealer) | 114.95 | new | Jbl Flip 6 Blue Portable Bluetooth Speaker and Divvi Case Ki |
| Woot | 79.95 | new | (NEW) JBL Flip 6 Portable IP67 Waterproof Speaker |
| Newegg.com - Onecall (Authorized Dealer) | 114.95 | new | JBL Flip 6 Portable Bluetooth Speaker Bundle with divvi! Har |
| Shop - Shop app | 121.00 | new | FormFit JBL Flip 6 Portable Waterproof Bluetooth Speaker - I |
| wafuu.com | 126.00 | new | JBL Flip 6 - Portable Bluetooth Speaker, Malakas na Tunog at |
| eBay | 100.00 | new | Jbl Flip 6 Portable Speaker System - Blue |
| Newegg.com - DrakeMount FortunaTech Parts | 172.49 | new | Flip 6 (Teal) + Bundle with divvi! Protective Hardshell Case |
| eBay - techronics-store | 169.95 | new | JBL Flip 6 Portable Bluetooth Portable Speaker System -BLUE |
| promolocker.com | 134.95 | new | JBL Flip 6 Portable Waterproof Speaker |
| eBay | 119.00 | new | JBL Flip 6 Waterproof Wireless Bluetooth Speaker - Blue / Po |
| eBay | 70.00 | new | JBL Flip 6 Portable Bluetooth Wireless Speaker, Blue |
_28 rows; first 12 shown._


## Data quality rule

```sql
REGISTER DQ new_full_price TABLE lightning.metastore.pricise.pricise_usl.safe_price
AS price > 0 AND item_condition = 'new' AND full_price = 1
```

`RUN DQ`: **369 rows, 309 valid, 60 invalid** (all products in the USL; this saved run is in `results/dq.json`. A later run, after more searches were cached, flagged 79: see `docs/images/11-zetaris-dq-invalid.png`). The JBL Flip 6 rows that fail are the real
pre-owned and refurbished offers in the live search: eBay sellers (da-kar $74.99, bayoumy $69.99, littlefoote $34.99 "Not
Working. Parts", focuscamera $188.99 refurbished, …), Mercari, Poshmark and PayMore. Zetaris also generated a primary-key rule
for each table (`LIST DQ`).

The live search had **no Walmart open-box offer** (neither the $89.97 Flip 6 nor the $68.53 Flip 7), so none appears here.
When a search returns one, the rule fails it (`item_condition = 'open box'`), and the Skeptic rejects it with every reason
(for the Flip 7: "a different model (Flip 7, not Flip 6), open box, not new"); tests cover both.

## Cache and refresh

- Every REST table was cached right after it was created (`CACHE TABLE`): an uncached REST table fetches its endpoint on
  every query, and for SerpApi each fetch is a paid search. The cached SerpApi table is the search of 27 Oct 2026.
- To refresh it, run statement 15 (`CACHE TABLE pricise_rest.serp_jbl_flip6`) again: one SerpApi search. RDAP rarely
  changes; refresh it the same way (statements 8–13).
- The `pricise_cache` rows refresh on their own: the Price Agent writes every search (and sellers list) to
  `pricise_pg.shopping_offers` with a 6 h cache, and `safe_price` reads the newest per product at query time.
- Whether a cache survives a Zetaris restart wasn't tested; if a query is slow after one, re-run the `CACHE TABLE` lines.

## Python each USL query replaced

Counted as the body lines of the Python functions doing the same work (`ast`, docstrings excluded). The Python stays as
the fallback when Zetaris is down, so nothing was deleted.

| USL query | Replaces | Python lines | SQL lines |
|---|---|---|---|
| `retailer_domain` (RDAP registration date + age) | `backend/tools/rdap.py`: `registrable`, `rdap_url`, `registered_on`, … | 28 | 15 |
| `safe_price`: fetch + flatten the search, sellers list, cache read | `shopping.py`: `_search_serpapi`, `parse`, `excluded`, `shipping_of`, `search`, `sellers`; `repo.py`: `cached_offers`, `offers_sql` | 70 + 31 | |
| `safe_price`: retailer, seller, condition, warranty | `trusted_retailers.py`: `credible_domain`, `official_seller`, `seller_of`, `warranty_note`; `shopping.py`: `store_domain`, `condition_kind` | 26 | |
| `safe_price`: credible median, unusually low, 50% floor | `shopping.py`: `classify_offers` (its median and low/floor part; it also does same-item matching, which stays in Python) | 42 | |
| `safe_price`: prices Pricise has seen | `repo.py`: `other_observations`, `observations_sql` | 21 | |
| **`safe_price` total** | | **190** | **76** |
| `REGISTER DQ new_full_price` | the "new and full price" checks in `shopping.excluded` + `condition_kind` | 8 | 1 |

So about **218 lines of Python (RDAP 28 + safe_price 190) map to 91 lines of SQL (15 + 76) plus one DQ line**. The real
saving is less the line count than the joins: the Python needs four modules and two caches to do what one `SELECT` on
`safe_price` returns.

## What went wrong, and the fixes

1. **`COMPILE USL` returned "Invalid DDL statement"** with columns named `domain`, `condition` and `source` (SQL keywords)
   and no blank line between the two `CREATE TABLE`s. Keeping the trailing `);` (my client stripped it) didn't help.
   Renaming to `store_domain`, `item_condition`, `offer_source` and adding the blank line, as in the docs'
   `02_sic_edgar_usl_create.sql`, compiled. Which of the two changes mattered wasn't isolated.
2. **`ACTIVATE … retailer_domain`**: "Column 'store_domain': nullable -> NOT NULL"; fixed with `COALESCE(domain, '')`
   (the documented rule).
3. **`ACTIVATE … safe_price`** accepted the query, but reading the table failed: Spark couldn't resolve a self-join of
   `pricise_pg.shopping_offers` with its own `GROUP BY` ("Resolved attribute(s) fetched_at missing"). `REGISTER DQ` failed
   the same way. Replaced by a window (`max(fetched_at) OVER (PARTITION BY query_key)`); both then worked. **Activation
   checks the schema only, so read the table once before trusting it.**
4. The first results marked a $36.99 "Kindle" and two $18 / $19.99 rows as safe (`percentile_approx` gave a too-low
   median, and there was no 50% floor). Re-activated with the exact `percentile` and the floor.
5. `WITH` (CTEs) inside `ACTIVATE USL TABLE` isn't in the docs' examples but works.

## Known limits of this data

- **Junk rows in Pricise's own cache.** `shopping_offers` and `price_observations` hold searches from earlier development
  runs, including pages that weren't products ("Chat history", which matched books on Walmart at $18 and $19.99). They show
  up in `observed_vs_offers` and in the DQ counts. The Price Agent only reads its own product's rows, and its same-item rules
  drop these, but the tables should be cleaned (or keyed by product) before anyone reads them as a whole.
- **No JBL observations yet.** `observed_min` / `observed_median` are empty for the JBL rows until a full-mode check of the
  Amazon page saves one.
- **JBL's sellers list isn't in the USL yet.** Best Buy and JBL.com ($114.95) came from Walmart's sellers list, which reaches
  `shopping_offers` only after a full-mode run. Until then `safe_price` has Woot and Walmart for this product.
- `credible_median` has no page price, so a JBL row is "unusually low" only against the other stores (Woot $79.95 is 82% of
  the $97.45 median here). The Price Agent also compares with the page's price ($114.95), which flags Woot for review.
