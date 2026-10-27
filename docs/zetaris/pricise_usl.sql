-- Pricise on Zetaris: a Unified Semantic Layer (pricise_usl) over three sources, and one data-quality rule.
-- Syntax from the Zetaris getting-started repo: docs/guides/zetaris-lightning-sql-commands.md (CREATE LIGHTNING DATABASE,
-- CREATE LIGHTNING REST TABLE, CACHE TABLE, CREATE NAMESPACE, COMPILE USL, UPDATE USL, ACTIVATE USL TABLE, REGISTER DQ)
-- and open_data/rest_apis (an API key in the endpoint's query string: the NASA NeoWs api_key= recipe).
--
-- Run with scripts/zetaris_usl.py, ONE statement at a time, in this order. <SERPAPI_KEY> is filled in from .env at run
-- time only: the key lives in Zetaris's REST table definition, never in this repo, and is masked in everything printed.
-- Source (a) pricise_pg (the Pricise Postgres) is already registered (scripts/register_zetaris.py).
-- Statements end with ";" on their own line or at the end of a line; "-- @one" ... "-- @end" marks one multi-line statement.
-- USL column names avoid SQL keywords (domain, condition, source): the first COMPILE with them returned "Invalid DDL statement".

-- 1. Source (b): public RDAP registration records (Verisign, .com) for the retailers in the demo, plus the brand's store
CREATE LIGHTNING DATABASE PRICISE_REST DESCRIBE BY "Pricise public RDAP domain records and one Google Shopping search via SerpApi";
CREATE LIGHTNING REST TABLE rdap_amazon FROM PRICISE_REST REQUEST(endpoint "https://rdap.verisign.com/com/v1/domain/amazon.com", method "get", response_type "json", http_encoding "URLENCODED") HEADER () BODY ();
CREATE LIGHTNING REST TABLE rdap_woot FROM PRICISE_REST REQUEST(endpoint "https://rdap.verisign.com/com/v1/domain/woot.com", method "get", response_type "json", http_encoding "URLENCODED") HEADER () BODY ();
CREATE LIGHTNING REST TABLE rdap_walmart FROM PRICISE_REST REQUEST(endpoint "https://rdap.verisign.com/com/v1/domain/walmart.com", method "get", response_type "json", http_encoding "URLENCODED") HEADER () BODY ();
CREATE LIGHTNING REST TABLE rdap_bestbuy FROM PRICISE_REST REQUEST(endpoint "https://rdap.verisign.com/com/v1/domain/bestbuy.com", method "get", response_type "json", http_encoding "URLENCODED") HEADER () BODY ();
CREATE LIGHTNING REST TABLE rdap_target FROM PRICISE_REST REQUEST(endpoint "https://rdap.verisign.com/com/v1/domain/target.com", method "get", response_type "json", http_encoding "URLENCODED") HEADER () BODY ();
CREATE LIGHTNING REST TABLE rdap_jbl FROM PRICISE_REST REQUEST(endpoint "https://rdap.verisign.com/com/v1/domain/jbl.com", method "get", response_type "json", http_encoding "URLENCODED") HEADER () BODY ();
CACHE TABLE pricise_rest.rdap_amazon;
CACHE TABLE pricise_rest.rdap_woot;
CACHE TABLE pricise_rest.rdap_walmart;
CACHE TABLE pricise_rest.rdap_bestbuy;
CACHE TABLE pricise_rest.rdap_target;
CACHE TABLE pricise_rest.rdap_jbl;

-- 2. Source (c): one Google Shopping search for the demo product (SerpApi). Cached right away: an uncached REST table
--    re-fetches on every query, and each fetch is a SerpApi search.
CREATE LIGHTNING REST TABLE serp_jbl_flip6 FROM PRICISE_REST REQUEST(endpoint "https://serpapi.com/search.json?engine=google_shopping&q=JBL+Flip+6+Blue&gl=us&hl=en&api_key=<SERPAPI_KEY>", method "get", response_type "json", http_encoding "URLENCODED") HEADER () BODY ();
CACHE TABLE pricise_rest.serp_jbl_flip6;

-- 3. The USL: namespace, model (two tables in ONE compile statement), description, activation
CREATE NAMESPACE IF NOT EXISTS lightning.metastore;
CREATE NAMESPACE IF NOT EXISTS lightning.metastore.pricise;
-- @one
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
    origin varchar(20),
    offer_source varchar(120),
    retailer varchar(60),
    store_domain varchar(100),
    seller varchar(80),
    title varchar(200),
    price decimal(10,2),
    item_condition varchar(40),
    full_price int,
    warranty_note varchar(200),
    domain_registered varchar(10),
    domain_age_days int,
    observed_min decimal(10,2),
    observed_median decimal(10,2),
    credible_median decimal(10,2),
    unusually_low int,
    is_safe int
);
-- @end
UPDATE USL lightning.metastore.pricise.pricise_usl SET DESCRIPTION 'Pricise safe price - credible retailer offers from Google Shopping with RDAP domain age and the prices Pricise has seen';
-- @one
ACTIVATE USL TABLE lightning.metastore.pricise.pricise_usl.retailer_domain AS
SELECT COALESCE(domain, '') AS store_domain, registered, CAST(datediff(current_date(), to_date(registered)) AS int) AS age_days FROM (
    SELECT lower(`ldhName`) AS domain, substr(e.`eventDate`, 1, 10) AS registered
    FROM pricise_rest.rdap_amazon LATERAL VIEW explode(events) AS e WHERE e.`eventAction` = 'registration'
    UNION ALL SELECT lower(`ldhName`), substr(e.`eventDate`, 1, 10)
    FROM pricise_rest.rdap_woot LATERAL VIEW explode(events) AS e WHERE e.`eventAction` = 'registration'
    UNION ALL SELECT lower(`ldhName`), substr(e.`eventDate`, 1, 10)
    FROM pricise_rest.rdap_walmart LATERAL VIEW explode(events) AS e WHERE e.`eventAction` = 'registration'
    UNION ALL SELECT lower(`ldhName`), substr(e.`eventDate`, 1, 10)
    FROM pricise_rest.rdap_bestbuy LATERAL VIEW explode(events) AS e WHERE e.`eventAction` = 'registration'
    UNION ALL SELECT lower(`ldhName`), substr(e.`eventDate`, 1, 10)
    FROM pricise_rest.rdap_target LATERAL VIEW explode(events) AS e WHERE e.`eventAction` = 'registration'
    UNION ALL SELECT lower(`ldhName`), substr(e.`eventDate`, 1, 10)
    FROM pricise_rest.rdap_jbl LATERAL VIEW explode(events) AS e WHERE e.`eventAction` = 'registration'
) d
-- @end
;
-- @one
ACTIVATE USL TABLE lightning.metastore.pricise.pricise_usl.safe_price AS
WITH live AS (
    SELECT 'jblflip6blue' AS product_key, 'serpapi_live' AS origin, CAST(r.position AS string) AS pos, r.source AS source,
           r.title AS title, CAST(r.extracted_price AS double) AS price, CAST(r.price AS string) AS price_text,
           lower(coalesce(r.second_hand_condition, '')) AS cond_raw
    FROM pricise_rest.serp_jbl_flip6 LATERAL VIEW explode(shopping_results) AS r
), cached AS (  -- the newest cached search per query (a window, not a self-join: Spark can't resolve that one here)
    SELECT regexp_replace(o.query_key, 'sellers$', '') AS product_key, 'pricise_cache' AS origin, CAST(o.id AS string) AS pos,
           o.source AS source, o.title AS title, CAST(o.price AS double) AS price, CAST(o.price AS string) AS price_text,
           CASE WHEN o.condition LIKE 'excluded:%' THEN lower(o.condition) ELSE '' END AS cond_raw
    FROM (SELECT *, max(fetched_at) OVER (PARTITION BY query_key) AS newest FROM pricise_pg.shopping_offers) o
    WHERE o.source <> '' AND o.fetched_at = o.newest
), offers AS (
    SELECT * FROM live UNION ALL SELECT * FROM cached
), mapped AS (
    SELECT *,
           regexp_replace(product_key, '(black|white|blue|red|green|gray|grey|pink|teal|camo|squad)$', '') AS model_key,
           CASE lower(trim(split(source, ' - ')[0]))
                WHEN 'amazon' THEN 'amazon.com' WHEN 'amazon.com' THEN 'amazon.com' WHEN 'woot' THEN 'woot.com'
                WHEN 'walmart' THEN 'walmart.com' WHEN 'best buy' THEN 'bestbuy.com' WHEN 'target' THEN 'target.com'
                WHEN 'jbl' THEN 'jbl.com' END AS domain,
           CASE WHEN instr(source, ' - ') > 0 THEN trim(substr(source, instr(source, ' - ') + 3)) END AS seller,
           CASE WHEN cond_raw RLIKE 'open.?box' OR lower(title) RLIKE 'open.?box' THEN 'open box'
                WHEN cond_raw RLIKE 'refurbished|renewed|reconditioned|restored' THEN 'refurbished'
                WHEN cond_raw RLIKE 'pre-?owned|used|like.?new' THEN 'used'
                WHEN cond_raw = '' THEN 'new' ELSE cond_raw END AS condition,
           CASE WHEN lower(price_text) RLIKE '/ ?mo|per month|monthly' OR cond_raw RLIKE '/mo|month|installment|trade.?in'
                THEN 0 ELSE 1 END AS full_price
    FROM offers
), credible AS (
    SELECT *, CASE WHEN domain IS NOT NULL AND (seller IS NULL OR lower(seller) RLIKE '^jbl( official)?( store)?$')
                   THEN 1 ELSE 0 END AS credible_store
    FROM mapped
), medians AS (
    SELECT product_key, percentile(price, 0.5) AS credible_median FROM credible
    WHERE credible_store = 1 AND condition = 'new' AND full_price = 1 GROUP BY product_key
), seen AS (
    SELECT m.model_key, min(o.price) AS observed_min, percentile_approx(o.price, 0.5) AS observed_median
    FROM (SELECT DISTINCT model_key FROM credible) m
    JOIN pricise_pg.price_observations o ON regexp_replace(lower(o.name), '[^a-z0-9]', '') LIKE concat(m.model_key, '%')
    GROUP BY m.model_key
), age AS (
    SELECT domain, registered, CAST(datediff(current_date(), to_date(registered)) AS int) AS age_days FROM (
        SELECT lower(`ldhName`) AS domain, substr(e.`eventDate`, 1, 10) AS registered
        FROM pricise_rest.rdap_amazon LATERAL VIEW explode(events) AS e WHERE e.`eventAction` = 'registration'
        UNION ALL SELECT lower(`ldhName`), substr(e.`eventDate`, 1, 10)
        FROM pricise_rest.rdap_woot LATERAL VIEW explode(events) AS e WHERE e.`eventAction` = 'registration'
        UNION ALL SELECT lower(`ldhName`), substr(e.`eventDate`, 1, 10)
        FROM pricise_rest.rdap_walmart LATERAL VIEW explode(events) AS e WHERE e.`eventAction` = 'registration'
        UNION ALL SELECT lower(`ldhName`), substr(e.`eventDate`, 1, 10)
        FROM pricise_rest.rdap_bestbuy LATERAL VIEW explode(events) AS e WHERE e.`eventAction` = 'registration'
        UNION ALL SELECT lower(`ldhName`), substr(e.`eventDate`, 1, 10)
        FROM pricise_rest.rdap_target LATERAL VIEW explode(events) AS e WHERE e.`eventAction` = 'registration'
        UNION ALL SELECT lower(`ldhName`), substr(e.`eventDate`, 1, 10)
        FROM pricise_rest.rdap_jbl LATERAL VIEW explode(events) AS e WHERE e.`eventAction` = 'registration'
    ) r
)
SELECT
    COALESCE(concat(c.origin, ':', c.product_key, ':', c.pos), '') AS offer_id, c.product_key, c.model_key, c.origin, c.source AS offer_source,
    CASE c.domain WHEN 'amazon.com' THEN 'Amazon' WHEN 'woot.com' THEN 'Woot' WHEN 'walmart.com' THEN 'Walmart'
                  WHEN 'bestbuy.com' THEN 'Best Buy' WHEN 'target.com' THEN 'Target' WHEN 'jbl.com' THEN 'JBL' END AS retailer,
    c.domain AS store_domain, c.seller, substr(c.title, 1, 200) AS title, CAST(c.price AS decimal(10,2)) AS price,
    c.condition AS item_condition, c.full_price,
    CASE c.domain WHEN 'woot.com' THEN '90-day Woot warranty, not the JBL manufacturer warranty (usual Woot terms; check the listing)'
                  END AS warranty_note,
    a.registered AS domain_registered, a.age_days AS domain_age_days,
    CAST(s.observed_min AS decimal(10,2)) AS observed_min, CAST(s.observed_median AS decimal(10,2)) AS observed_median,
    CAST(md.credible_median AS decimal(10,2)) AS credible_median,
    CASE WHEN c.credible_store = 1 AND c.condition = 'new' AND c.full_price = 1 AND c.price < 0.7 * md.credible_median
              AND c.price >= 0.5 * md.credible_median THEN 1 ELSE 0 END AS unusually_low,
    CASE WHEN c.credible_store = 1 AND c.condition = 'new' AND c.full_price = 1 AND coalesce(a.age_days, 0) >= 365
              AND c.price >= 0.5 * md.credible_median THEN 1 ELSE 0 END AS is_safe  -- under 50% of the median: not a real price
FROM credible c
LEFT JOIN medians md ON md.product_key = c.product_key
LEFT JOIN seen s ON s.model_key = c.model_key
LEFT JOIN age a ON a.domain = c.domain
-- @end
;

-- 4. Data quality: an offer must be new and a full one-time price
REGISTER DQ new_full_price TABLE lightning.metastore.pricise.pricise_usl.safe_price AS price > 0 AND item_condition = 'new' AND full_price = 1;
