-- Queries on the USL. Run: .venv/bin/python scripts/zetaris_usl.py query docs/zetaris/queries/usl.sql

-- name: usl_tables
SHOW NAMESPACES OR TABLES IN lightning.metastore.pricise;
-- name: cheapest_safe_offer
SELECT retailer, price, item_condition, warranty_note, domain_age_days, credible_median, unusually_low FROM lightning.metastore.pricise.pricise_usl.safe_price WHERE product_key = 'jblflip6blue' AND is_safe = 1 ORDER BY price LIMIT 3;
-- name: retailers_by_domain_age
SELECT store_domain, registered, age_days FROM lightning.metastore.pricise.pricise_usl.retailer_domain ORDER BY age_days DESC;
-- name: unusually_low_offers
SELECT retailer, price, credible_median, round(price / credible_median, 2) AS share_of_median, warranty_note, is_safe FROM lightning.metastore.pricise.pricise_usl.safe_price WHERE unusually_low = 1;
-- name: offers_by_origin_and_safety
SELECT origin, is_safe, count(*) AS offers FROM lightning.metastore.pricise.pricise_usl.safe_price GROUP BY origin, is_safe ORDER BY origin, is_safe;
-- name: observed_vs_offers
SELECT retailer, price, observed_min, observed_median FROM lightning.metastore.pricise.pricise_usl.safe_price WHERE is_safe = 1 ORDER BY price;
