"""The Price Agent reads the USL's safe_price table through Zetaris when it answers, and falls back to the Google Shopping
search otherwise. No live calls: Zetaris and SerpApi are stubbed."""
import json
from pathlib import Path

import pytest

from backend import agent_trace
from backend.agents import price
from backend.contracts import Evidence, Finding, PageFacts
from backend.data import repo, zetaris
from backend.tools import shopping

FIX = Path(__file__).parent / "fixtures" / "serpapi"
RAW = json.loads((FIX / "jbl_flip_6_blue.json").read_text())["shopping_results"]
URL = "https://www.amazon.com/dp/B09GJZKQ3K"
PAGE = PageFacts(url=URL, host="www.amazon.com", product={"name": "JBL Flip 6 - Portable Bluetooth Speaker (Blue)", "brand": "JBL",
                                                          "price": 114.95})
USL_ROWS = [  # what safe_price returns (Zetaris gives every value as a string)
    {"offer_source": "Woot", "retailer": "Woot", "store_domain": "woot.com", "seller": None, "title": "(NEW) JBL Flip 6 Portable IP67 Waterproof Speaker",
     "price": "79.95", "item_condition": "new", "full_price": "1", "warranty_note": "90-day Woot warranty", "domain_age_days": "9000",
     "unusually_low": "1", "is_safe": "1"},
    {"offer_source": "Walmart", "retailer": "Walmart", "store_domain": "walmart.com", "seller": None, "title": "JBL FLIP 6 Bluetooth Portable Waterproof Speaker",
     "price": "114.95", "item_condition": "new", "full_price": "1", "warranty_note": None, "domain_age_days": "11000", "unusually_low": "0",
     "is_safe": "1"},
    {"offer_source": "Best Buy", "retailer": "Best Buy", "store_domain": "bestbuy.com", "seller": None, "title": "JBL - FLIP6 Portable Waterproof Speaker - Blue",
     "price": "114.95", "item_condition": "new", "full_price": "1", "warranty_note": None, "domain_age_days": "11500", "unusually_low": "0",
     "is_safe": "1"},
    {"offer_source": "Walmart", "retailer": "Walmart", "store_domain": "walmart.com", "seller": None, "title": "JBL Flip 6 Speaker - Blue (Open Box)",
     "price": "89.97", "item_condition": "open box", "full_price": "1", "warranty_note": None, "domain_age_days": "11000",
     "unusually_low": "0", "is_safe": "0"},
]


@pytest.fixture
def stubs(monkeypatch):
    calls = {"sql": [], "search": 0, "sellers": 0, "fail": False}

    def run_sql(sql, limit=1000, timeout=8):
        calls["sql"].append(sql)
        if calls["fail"]:
            raise zetaris.ZetarisError("Zetaris did not answer within 8s")
        if "pricise_usl.safe_price" in sql:
            return USL_ROWS
        raise zetaris.ZetarisError("not stubbed")

    def search(q):
        calls["search"] += 1
        return shopping.parse(RAW)

    def sellers(token):
        calls["sellers"] += 1
        return []

    monkeypatch.setenv("DATA_SOURCE", "zetaris")
    monkeypatch.setenv("SERPAPI_KEY", "test-key-not-real")
    monkeypatch.setattr(zetaris, "configured", lambda: True)
    monkeypatch.setattr(zetaris, "run_sql", run_sql)
    monkeypatch.setattr(shopping, "_search_serpapi", search)
    monkeypatch.setattr(shopping, "_sellers_serpapi", sellers)
    monkeypatch.setattr(repo, "cached_offers", lambda key, since: None)
    monkeypatch.setattr(repo, "save_offers", lambda *a: None)
    monkeypatch.setattr(repo, "other_observations", lambda *a: ([], None, "none"))
    monkeypatch.setattr(price, "_vet_domains", lambda cid, ds: {d: {"verdict": "safe", "checked": True, "reasons": []} for d in ds})
    return calls


def test_price_agent_reads_safe_price_through_the_usl(stubs):
    res = price.assess("c1", URL, PAGE)
    assert stubs["sql"] == [repo.usl_offers_sql("jblflip6blue")] and stubs["search"] == 0 and stubs["sellers"] == 0
    assert res.online["source"] == "zetaris usl" and "pricise_usl.safe_price" in res.online["sql"]
    by = {f.summary.split(":")[0]: f for f in res.findings if f.type == "best_price"}
    assert by["Skeptic approved Woot $79.95, $35 less than here, unusually low and reviewed"].status == "confirmed"
    assert by["Skeptic rejected Walmart $89.97"].summary.endswith("open box, not new.")


def test_trace_lines_name_the_usl(stubs):
    res = price.assess("c1", URL, PAGE)
    f = next(f for f in res.findings if f.type == "best_price")
    assert agent_trace._tables(f) == ["pricise_usl.safe_price"]
    old = Finding(check_id="c", agent="price", type="catalog_match", status="confirmed", summary="x",
                  evidence=[Evidence(kind="sql", ref="SELECT * FROM pricise_pg.listings l JOIN pricise_pg.stores s ON 1=1")])
    assert agent_trace._tables(old) == ["listings", "stores"]  # pricise_pg tables keep their short names


def test_falls_back_to_the_search_when_zetaris_fails(stubs):
    stubs["fail"] = True
    res = price.assess("c1", URL, PAGE)
    assert stubs["search"] == 1 and res.online["source"] == "serpapi"
    assert any(f.type == "best_price" and f.summary.startswith("Skeptic approved Woot $79.95") for f in res.findings)


def test_falls_back_when_the_usl_has_no_rows_for_this_product(stubs, monkeypatch):
    monkeypatch.setattr(zetaris, "run_sql", lambda sql, limit=1000, timeout=8: [])
    res = price.assess("c1", URL, PAGE)
    assert stubs["search"] == 1 and res.online["source"] == "serpapi"


def test_usl_is_skipped_without_zetaris_or_when_switched_off(stubs, monkeypatch):
    monkeypatch.setenv("PRICISE_USL", "off")
    price.assess("c1", URL, PAGE)
    assert stubs["sql"] == [] and stubs["search"] == 1
    monkeypatch.delenv("PRICISE_USL")
    monkeypatch.setenv("DATA_SOURCE", "fixture")
    assert repo.usl_offers("jblflip6blue") == (None, None)


def test_setup_statements_parse_and_mask_the_key():
    import importlib.util

    spec = importlib.util.spec_from_file_location("zusl", Path(__file__).parents[2] / "scripts" / "zetaris_usl.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    text = mod.SQL_FILE.read_text()
    stmts = mod.statements(text)
    assert len(stmts) == 22 and stmts[-1].startswith("REGISTER DQ new_full_price TABLE lightning.metastore.pricise.pricise_usl")
    assert "<SERPAPI_KEY>" in text and "api_key=<SERPAPI_KEY>" in text  # a placeholder, filled in only at run time
    serp = next(s for s in stmts if "serpapi.com" in s)
    assert "api_key=***" in mod.masked(serp) and "<SERPAPI_KEY>" not in mod.masked(serp)
    assert stmts.index(next(s for s in stmts if s.startswith("COMPILE USL"))) < \
        stmts.index(next(s for s in stmts if s.startswith("ACTIVATE USL TABLE lightning.metastore.pricise.pricise_usl.safe_price")))


def test_zetaris_errors_never_carry_an_api_key():
    assert zetaris.redact("HTTP 429 for URL: https://serpapi.com/search.json?q=x&api_key=abcdef0123&gl=us") == \
        "HTTP 429 for URL: https://serpapi.com/search.json?q=x&api_key=***&gl=us"


def test_the_agents_usl_query_only_names_columns_the_usl_declares():
    """The live run fell back because the query still sorted by `source` after the column became `offer_source`."""
    import re

    ddl = (Path(__file__).parents[2] / "docs" / "zetaris" / "pricise_usl.sql").read_text()
    table = ddl[ddl.index("CREATE TABLE safe_price ("):]
    declared = set(re.findall(r"^\s+([a-z_]+) (?:varchar|decimal|int)", table[:table.index(");")], re.M))
    sql = repo.usl_offers_sql("jblflip6blue")
    used = set(re.findall(r"[a-z_]+", sql.split(" FROM ")[0].removeprefix("SELECT "))) | \
        set(re.findall(r"[a-z_]+", sql.split("ORDER BY ")[1])) | {"product_key"}
    assert used <= declared, used - declared


def test_a_failed_usl_read_is_explained_on_the_evidence_page(stubs):
    stubs["fail"] = True
    res = price.assess("c1", URL, PAGE)
    assert res.online["usl_fallback"] == "Zetaris did not answer within 8s" and res.online["source"] == "serpapi"
    stubs["fail"] = False
    repo.USL_MEMO.clear()  # a later check (one check's agents share one read, failed or not)
    res = price.assess("c1", URL, PAGE)
    assert res.online["usl_fallback"] is None and "pricise_usl.safe_price" in res.online["sql"]  # the query actually used


def test_offers_in_both_the_live_table_and_the_cache_count_once(stubs, monkeypatch):
    """Check d35cdc7f34e4: safe_price returned the live search and the same search from the cache: 80 offers, counts doubled."""
    monkeypatch.setattr(zetaris, "run_sql", lambda sql, limit=1000, timeout=8: USL_ROWS + [dict(r) for r in USL_ROWS])
    offers, _ = repo.usl_offers("jblflip6blue")
    assert len(offers) == len(USL_ROWS) == 4
    res = price.assess("c1", URL, PAGE)
    assert res.online["offers_seen"] == 4


def test_the_usl_read_has_its_own_15_s_timeout(stubs, monkeypatch):
    seen = {}

    def run_sql(sql, limit=1000, timeout=8):
        seen["timeout"] = timeout
        return USL_ROWS

    monkeypatch.setattr(zetaris, "run_sql", run_sql)
    repo.usl_offers("jblflip6blue")
    assert seen["timeout"] == 15
