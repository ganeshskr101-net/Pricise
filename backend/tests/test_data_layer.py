"""Data layer switches: DATA_SOURCE=fixture uses the catalog, DATA_SOURCE=zetaris runs Lightning SQL (mocked here) and falls
back to the catalog when Zetaris fails. Fake-discount findings cite the SQL and rows as evidence (kind "sql")."""
import pytest

from backend.contracts import PageFacts
from backend.data import history, repo, zetaris
from backend.data.history import get_price_history
from backend.data.zetaris import ZetarisError
from backend.tools.discount import check_discount

DEMO_URL = "http://localhost:8787/storelab/audiodealz/"
CLAIMS = [{"type": "claimed_discount", "selector": "#deal-badge > div:nth-of-type(3)", "text": "List Price: $249.00", "amount": 249.0}]


def _zetaris_history_rows():
    prices = [149] * 30 + [169] * 7 + [149] * 3 + [249] * 2 + [149] * 48
    return [{"day_offset": str(i - 89), "price": f"{p:.2f}"} for i, p in enumerate(prices)]  # Zetaris returns strings


@pytest.fixture
def zetaris_mode(monkeypatch):
    monkeypatch.setenv("DATA_SOURCE", "zetaris")
    calls = []

    def fake(sql, limit=1000):
        calls.append(sql)
        return _zetaris_history_rows() if "price_history" in sql and "audiodealz" in sql else []

    monkeypatch.setattr(zetaris, "run_sql", fake)
    return calls


def test_fixture_mode_covers_every_storelab_store():
    rows = get_price_history("storelab/store-f", "MM-XM5")
    assert len(rows) == 90 and max(p for _, p in rows) == 399 and rows.source == "fixture"
    assert get_price_history("storelab/store-c", "WH-1000XM5")[-1][1] == 255
    assert get_price_history("bench/deal", "BENCH-FD-06") is not None  # bench pages still covered


def test_zetaris_mode_queries_with_qualified_names(zetaris_mode):
    rows = get_price_history("storelab/audiodealz", "WH1000XM5/B")
    assert rows.source == "zetaris" and len(rows) == 90 and [p for _, p in rows].count(249) == 2
    sql = zetaris_mode[0]
    assert "pricise_pg.price_history" in sql and "'storelab/audiodealz'" in sql and "'wh1000xm5b'" in sql
    assert not sql.rstrip().endswith(";")


def test_zetaris_failure_falls_back_to_catalog(monkeypatch):
    monkeypatch.setenv("DATA_SOURCE", "zetaris")

    def boom(sql, limit=1000):
        raise ZetarisError("Cannot reach Zetaris")

    monkeypatch.setattr(zetaris, "run_sql", boom)
    rows = get_price_history("storelab/audiodealz", "WH1000XM5/B")
    assert rows.source == "fixture" and len(rows) == 90


def test_page_text_never_reaches_sql(zetaris_mode):
    assert get_price_history("shop.example'; DROP TABLE x --", "x") is None
    assert get_price_history("shop.example", "'; DROP TABLE x --") is None  # keyed to letters+digits: harmless
    assert all("DROP" not in s for s in zetaris_mode)
    with pytest.raises(ValueError):
        zetaris.lit("a'b")


def test_fake_discount_cites_sql_and_rows(zetaris_mode):
    page = PageFacts(url=DEMO_URL, host="localhost:8787", product={"price": 149.0, "sku": "WH1000XM5/B"}, signals=CLAIMS)
    res = check_discount("c1", DEMO_URL, page)
    [f] = res.findings
    assert (f.type, f.status) == ("fake_discount", "confirmed")
    [sql_ev] = [e for e in f.evidence if e.kind == "sql"]
    assert "pricise_pg.price_history" in sql_ev.ref
    assert "90 daily rows" in sql_ev.label and "$149.00 x 81" in sql_ev.label and "$249.00 x 2" in sql_ev.label
    assert res.evidence_data["price_history"]["source"] == "Zetaris"


def test_listings_same_shape_in_both_modes(monkeypatch):
    rows, sql, source = repo.listings(day=1)
    assert source == "fixture" and sql is None
    c = next(r for r in rows if r["store_id"] == "store-c")
    assert c["true_total"] == 229 and c["card_offer"] == 26
    assert next(r for r in repo.listings(day=9)[0] if r["store_id"] == "store-c")["true_total"] == 209

    monkeypatch.setenv("DATA_SOURCE", "zetaris")
    as_strings = [{k: (f"{v:.2f}" if isinstance(v, float) else v) for k, v in r.items()} for r in rows]
    monkeypatch.setattr(zetaris, "run_sql", lambda sql, limit=1000: as_strings)
    zrows, zsql, zsource = repo.listings(day=1)
    assert zsource == "zetaris" and "h.day_offset = 0" in zsql and zrows == rows


def test_history_module_never_imports_the_json_fixture_directly():
    assert not hasattr(history, "FIXTURE")
