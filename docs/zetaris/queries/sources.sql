-- Read-only exploratory queries, 2-3 per source. Run: .venv/bin/python scripts/zetaris_usl.py query docs/zetaris/queries/sources.sql

-- name: pg_observations_latest
SELECT host, name, price, condition, safe, from_unixtime(seen_at) AS seen FROM pricise_pg.price_observations ORDER BY seen_at DESC LIMIT 5;
-- name: pg_offers_by_query
SELECT query_key, count(*) AS offers, from_unixtime(max(fetched_at)) AS newest FROM pricise_pg.shopping_offers GROUP BY query_key ORDER BY newest DESC LIMIT 10;
-- name: pg_findings_by_agent
SELECT agent, status, count(*) AS n FROM pricise_pg.findings GROUP BY agent, status ORDER BY agent, status;
-- name: pg_stores_by_kind
SELECT kind, count(*) AS n FROM pricise_pg.stores GROUP BY kind;

-- name: rdap_registration_dates
SELECT lower(`ldhName`) AS domain, substr(e.`eventDate`, 1, 10) AS registered FROM pricise_rest.rdap_woot LATERAL VIEW explode(events) AS e WHERE e.`eventAction` = 'registration' UNION ALL SELECT lower(`ldhName`), substr(e.`eventDate`, 1, 10) FROM pricise_rest.rdap_walmart LATERAL VIEW explode(events) AS e WHERE e.`eventAction` = 'registration' UNION ALL SELECT lower(`ldhName`), substr(e.`eventDate`, 1, 10) FROM pricise_rest.rdap_amazon LATERAL VIEW explode(events) AS e WHERE e.`eventAction` = 'registration';
-- name: rdap_woot_events
SELECT e.`eventAction` AS action, e.`eventDate` AS at FROM pricise_rest.rdap_woot LATERAL VIEW explode(events) AS e;
-- name: rdap_bestbuy_status
SELECT lower(`ldhName`) AS domain, s AS status FROM pricise_rest.rdap_bestbuy LATERAL VIEW explode(status) AS s;

-- name: serp_cheapest
SELECT r.position, r.source, r.extracted_price AS price, r.second_hand_condition AS cond, substr(r.title, 1, 60) AS title FROM pricise_rest.serp_jbl_flip6 LATERAL VIEW explode(shopping_results) AS r ORDER BY r.extracted_price LIMIT 10;
-- name: serp_by_condition
SELECT coalesce(r.second_hand_condition, 'new (not stated)') AS cond, count(*) AS offers, min(r.extracted_price) AS lowest FROM pricise_rest.serp_jbl_flip6 LATERAL VIEW explode(shopping_results) AS r GROUP BY coalesce(r.second_hand_condition, 'new (not stated)');
-- name: serp_woot_and_walmart
SELECT r.source, r.extracted_price AS price, r.extracted_old_price AS was, substr(r.title, 1, 60) AS title FROM pricise_rest.serp_jbl_flip6 LATERAL VIEW explode(shopping_results) AS r WHERE r.source IN ('Woot', 'Walmart');
