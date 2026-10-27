-- Pricise data in its own Postgres (docker-compose.pricise.yml). Zetaris reads it as the pricise_pg datasource.
-- All rows are SYNTHETIC (made-up Store Lab stores). scripts/seed.py drops and recreates these tables, then fills them.
-- Money is numeric(10,2); price history uses day offsets (0 = demo Day 1), see backend/data/catalog.py.

CREATE TABLE stores (
    store_id   text PRIMARY KEY,
    name       text NOT NULL,              -- "Store C"
    brand      text NOT NULL,              -- the made-up brand on the page
    kind       text NOT NULL,              -- storelab | bench
    path       text NOT NULL UNIQUE,       -- storelab/store-c (how the backend keys a Store Lab page)
    url        text NOT NULL,
    trust      text NOT NULL,              -- ok | risky | scam | unknown (seeded; the Trust Agent will replace this)
    status     text NOT NULL,              -- up | outage
    trick      text NOT NULL,
    synthetic  boolean NOT NULL DEFAULT true
);

CREATE TABLE products (
    product_id text PRIMARY KEY,
    brand      text NOT NULL,
    family     text NOT NULL,              -- model family, e.g. WH-1000X: candidates for "same item?"
    model      text NOT NULL,
    model_key  text NOT NULL,              -- model lower-cased, letters and digits only
    name       text NOT NULL
);

CREATE TABLE listings (
    listing_id text PRIMARY KEY,
    store_id   text NOT NULL REFERENCES stores,
    product_id text NOT NULL REFERENCES products,
    sku        text NOT NULL,
    sku_key    text NOT NULL,
    condition  text NOT NULL,              -- new | refurbished
    price      numeric(10,2) NOT NULL,     -- Day 1 list price (price_history has the daily price)
    was_price  numeric(10,2),
    shipping   numeric(10,2) NOT NULL DEFAULT 0,
    fees       numeric(10,2) NOT NULL DEFAULT 0,   -- fees added at checkout
    addon      numeric(10,2) NOT NULL DEFAULT 0,   -- pre-ticked add-on (avoidable, so not in the true total)
    url        text NOT NULL
);

CREATE TABLE price_history (
    store_id   text NOT NULL REFERENCES stores,
    product_id text NOT NULL REFERENCES products,
    day_offset integer NOT NULL,           -- -89..0 = the 90 days up to demo Day 1; 1..8 = demo Days 2..9
    price      numeric(10,2) NOT NULL,
    PRIMARY KEY (store_id, product_id, day_offset)
);

CREATE TABLE card_offers (
    offer_id    text PRIMARY KEY,
    store_id    text NOT NULL REFERENCES stores,
    product_id  text NOT NULL REFERENCES products,
    amount      numeric(10,2) NOT NULL,
    description text NOT NULL
);

CREATE TABLE wishlist (
    id         text PRIMARY KEY,
    name       text NOT NULL,
    url        text,
    rule       text NOT NULL DEFAULT '',
    target     numeric(10,2),
    best_price numeric(10,2),
    best_store text,
    status     text NOT NULL DEFAULT 'Watching',
    tone       text NOT NULL DEFAULT 'unknown',
    product_id text,
    sort       integer NOT NULL DEFAULT 0
);

CREATE TABLE findings (
    id            bigserial PRIMARY KEY,
    check_id      text NOT NULL,
    url           text,
    agent         text NOT NULL,
    type          text NOT NULL,
    status        text NOT NULL,
    summary       text NOT NULL,
    evidence      text NOT NULL DEFAULT '[]',   -- JSON list of evidence objects
    confidence    double precision,
    model         text,
    challenged_by text,
    rounds        integer NOT NULL DEFAULT 0,
    ts            double precision
);

-- Prices the shopper saw on real store pages Pricise checked (no account, cookie or personal data: product fields and a
-- URL without its query string). The Price Agent compares the same model across hosts. seen_at = Unix seconds.
CREATE TABLE IF NOT EXISTS price_observations (
    id         bigserial PRIMARY KEY,
    host       text NOT NULL,
    brand      text,
    model      text,
    model_key  text,
    sku        text,
    name       text,
    price      numeric(10,2) NOT NULL,
    currency   text,
    condition  text NOT NULL DEFAULT 'new',
    safe       text,                       -- the check's "Shop safe?" status: good | caution | unknown (bad is never saved)
    url        text NOT NULL,
    seen_at    double precision NOT NULL
);

-- Google Shopping offers (SerpApi) per search, kept 6 h as a cache. A search with no offers keeps one marker row
-- (source '', price 0) so it isn't repeated. fetched_at = Unix seconds.
CREATE TABLE IF NOT EXISTS shopping_offers (
    id            bigserial PRIMARY KEY,
    query_key     text NOT NULL,
    fetched_at    double precision NOT NULL,
    source        text NOT NULL,
    domain        text,
    title         text,
    price         numeric(10,2) NOT NULL,
    shipping      numeric(10,2),
    total         numeric(10,2),
    shipping_note text,
    condition     text,
    link          text,
    token         text,                    -- SerpApi's lookup token for this product's sellers list (not read by Zetaris)
    multi         boolean                  -- Google lists several stores for this product
);
ALTER TABLE shopping_offers ADD COLUMN IF NOT EXISTS token text;
ALTER TABLE shopping_offers ADD COLUMN IF NOT EXISTS multi boolean;
