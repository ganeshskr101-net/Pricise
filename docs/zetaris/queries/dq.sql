-- The data-quality rule. Run: .venv/bin/python scripts/zetaris_usl.py query docs/zetaris/queries/dq.sql

-- name: dq_rules
LIST DQ USL lightning.metastore.pricise.pricise_usl;
-- name: dq_run
RUN DQ new_full_price TABLE lightning.metastore.pricise.pricise_usl.safe_price;
-- name: dq_invalid
SHOW DQ INVALID RECORD new_full_price TABLE lightning.metastore.pricise.pricise_usl.safe_price;
-- name: dq_valid_count
SHOW DQ VALID RECORD new_full_price TABLE lightning.metastore.pricise.pricise_usl.safe_price;
