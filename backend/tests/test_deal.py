"""Deal Agent checks. Run: python -m pytest backend/tests"""
import pytest

from backend.tools.runahead import is_denied, looks_like_checkout

# ----------------------------------------------------------------------------- safety guard (runs before every click)

PAY_TEXTS = [
    "Pay now", "PAY $191.00", "Pay with PayPal", "Continue to payment", "Place order", "place  order", "Confirm and pay",
    "Confirm purchase", "Confirm", "Submit order", "Submit", "Complete purchase", "Buy now", "Buy Now", "Apple Pay",
]


@pytest.mark.parametrize("text", PAY_TEXTS)
def test_guard_blocks_pay_and_confirm_text(text):
    assert is_denied(text, on_checkout=True)


@pytest.mark.parametrize("attrs", [
    {"id": "place-order-btn"}, {"type": "submit"}, {"class": "btn btn-pay"}, {"aria-label": "Confirm order"},
    {"href": "/checkout/pay"}, {"value": "Complete purchase"}, {"data-action": "submit-order"}, {"name": "confirm"},
    {"title": "Buy now"}, {"formaction": "/payment"},
])
def test_guard_blocks_pay_and_confirm_attributes_even_with_harmless_text(attrs):
    assert is_denied("Continue", attrs, on_checkout=True)


@pytest.mark.parametrize("text", ["Continue", "Next", "Proceed to checkout", "Add to cart", "Back to cart", "Review order"])
def test_guard_allows_ordinary_navigation(text):
    assert not is_denied(text, {"class": "btn", "style": "display:block", "href": "checkout-2.html"}, on_checkout=True)


def test_guard_never_buys_from_the_product_page():
    assert is_denied("Buy Now", on_checkout=False)
    assert is_denied("Add to cart", {"id": "place-order"}, on_checkout=False)
    assert not is_denied("Add to Cart", {"type": "submit"}, on_checkout=False)  # a normal add-to-cart form button


def test_checkout_urls():
    assert looks_like_checkout("http://localhost:8787/storelab/audiodealz/cart.html")
    assert looks_like_checkout("http://localhost:8787/storelab/audiodealz/checkout-3.html?x=1")
    assert not looks_like_checkout("http://localhost:8787/storelab/audiodealz/")


# ----------------------------------------------------------------------------- add-ons, price history, discount, model client

from backend.contracts import PageFacts  # noqa: E402
from backend.data.history import get_price_history  # noqa: E402
from backend.tools.addons import check_addons  # noqa: E402
from backend.tools.common import is_storelab  # noqa: E402
from backend.tools.discount import check_discount  # noqa: E402

DEMO_URL = "http://localhost:8787/storelab/audiodealz/"


def _page(url, price, signals, **product):
    return PageFacts(url=url, host=url.split("/")[2], product={"price": price, **product}, signals=signals)


def test_addon_signal_becomes_confirmed_finding_with_outline():
    page = _page(DEMO_URL, 149.0, [{"type": "prechecked_addon", "selector": "#addon-row",
                                    "text": "Add 2-Year Protection Plan for $4.99 Covers accidental damage", "amount": 4.99}])
    [f] = check_addons("c1", page).findings
    assert (f.agent, f.type, f.status) == ("deal", "prechecked_addon", "confirmed")
    assert f.evidence[0].kind == "selector" and f.evidence[0].ref == "#addon-row" and f.evidence[0].label == "Pre-ticked $4.99"
    assert "$4.99" in f.summary  # the panel's "Untick $4.99 add-on" button reads the amount from the summary


def test_store_lab_mode_only_on_this_laptop():
    assert is_storelab(DEMO_URL) and is_storelab("http://127.0.0.1:5555/bench/deal/fd-01.html")
    assert not is_storelab("https://real-shop.example/storelab/item")  # a real site can never trigger the hidden browser


def test_history_fixture_matches_demo_numbers():
    rows = get_price_history("storelab/audiodealz", "WH1000XM5/B")
    prices = [p for _, p in rows]
    assert len(prices) == 90 and prices.count(149) == 81 and prices.count(249) == 2
    assert get_price_history("storelab/store-c", "WH1000XM5/B") is None  # same product, other store: no mix-up
    assert get_price_history("shop.example", "anything") is None


AUDIODEALZ_CLAIMS = [{"type": "claimed_discount", "selector": "#deal-badge > div:nth-of-type(2)", "text": "-40% $14900"},
                     {"type": "claimed_discount", "selector": "#deal-badge > div:nth-of-type(3)", "text": "List Price: $249.00", "amount": 249.0}]


def test_audiodealz_discount_is_fake_with_history_proof():
    page = _page(DEMO_URL, 149.0, AUDIODEALZ_CLAIMS, sku="WH1000XM5/B", model="WH-1000XM5")
    res = check_discount("c1", DEMO_URL, page)
    [f] = res.findings
    assert (f.type, f.status) == ("fake_discount", "confirmed")
    assert "81 of the last 90 days" in f.summary and "2 days only" in f.summary
    assert {e.kind for e in f.evidence} == {"selector", "price_row"} and f.evidence[0].label == "Fake 40% off"
    assert res.evidence_data["price_history"]["days_at_claim"] == 2


def test_real_page_without_history_is_unverified():
    url = "https://shop.example/item"
    page = _page(url, 89.0, [{"type": "claimed_discount", "selector": "#was", "text": "Was $129", "amount": 129.0}], name="Ticket")
    [f] = check_discount("c1", url, page).findings
    assert (f.type, f.status) == ("claimed_discount", "unverified") and "fake" not in f.summary.lower()


def test_genuine_discount_is_not_called_fake():
    url = "http://localhost:8787/bench/deal/fd-06.html"  # $80 for 75 days, now $60
    page = _page(url, 60.0, [{"type": "claimed_discount", "selector": "#was", "text": "Was $80", "amount": 80.0}], sku="BENCH-FD-06")
    [f] = check_discount("c1", url, page).findings
    assert (f.type, f.status) == ("claimed_discount", "confirmed")


def test_no_claim_no_finding():
    assert check_discount("c1", "http://localhost:8787/storelab/store-c/", _page(DEMO_URL, 255.0, [])).findings == []


def test_meterless_returns_empty_without_keys(monkeypatch):
    from backend.llm import meterless
    monkeypatch.setattr(meterless, "_dotenv", lambda: {})
    for k in ("METERLESS_BASE_URL", "METERLESS_RUNTIME_TOKEN", "NEMOTRON_FAST_MODEL"):
        monkeypatch.delenv(k, raising=False)
    assert meterless.complete("hello") == ""


# ----------------------------------------------------------------------------- the whole agent (Store Lab pages run in a real headless browser)

import asyncio  # noqa: E402
import time  # noqa: E402

from backend.agents import deal  # noqa: E402
from backend.tests.content_reader import read_page_facts, serve_repo  # noqa: E402

TRICKS = {"fake_discount", "resetting_timer", "prechecked_addon", "drip_fees"}


def _browser_ok() -> bool:
    try:
        from backend.tools.browser import chromium

        async def go():
            async with chromium():
                return True
        return asyncio.run(go())
    except Exception:
        return False


needs_browser = pytest.mark.skipif(not _browser_ok(), reason="needs the hidden browser: run scripts/setup.sh (installs Playwright Chromium)")


@pytest.fixture(scope="module")
def site():
    server, base = serve_repo()
    yield base
    server.shutdown()


async def _timed(cid, url, page):
    """Runs the agent; returns findings with the seconds after start at which each arrived."""
    t0, out = time.time(), []
    async for f in deal.iter_deal(cid, url, page):
        out.append((round(time.time() - t0, 2), f))
    return out


@needs_browser
def test_audiodealz_all_four_tricks_with_evidence_and_real_total(site, monkeypatch):
    monkeypatch.setenv("TIMER_GAP_S", "2")
    url = f"{site}/storelab/audiodealz/"
    page = PageFacts(**asyncio.run(read_page_facts(url)))
    timed = asyncio.run(_timed("t-audio", url, page))
    by_type = {f.type: f for _, f in timed}
    assert TRICKS <= set(by_type)
    for t in TRICKS:
        f = by_type[t]
        assert f.agent == "deal" and f.status == "confirmed" and f.evidence, t
    outlines = {e.ref for t in ("fake_discount", "resetting_timer", "prechecked_addon")
                for e in by_type[t].evidence if e.kind == "selector" and e.label}
    assert outlines == {"#deal-badge", "#timer", "#addon-row"}
    assert len([e for e in by_type["resetting_timer"].evidence if e.kind == "screenshot"]) == 3
    assert "Real total: $191.00" in by_type["drip_fees"].summary
    ev = deal.EVIDENCE["t-audio"]
    assert ev["total"] == 191.00 and [r["step"] for r in ev["fees"] if r["label"].endswith("fee")] == [4, 4]
    assert "refused \"Place order\"" in ev["runahead"]["stopped"]  # stopped at payment, never clicked it
    non_timer = [s for s, f in timed if f.type != "resetting_timer"]
    assert max(non_timer) < 10  # the three other tricks within 10 s (timer test excluded)


@needs_browser
def test_store_c_nothing_flagged(site):
    url = f"{site}/storelab/store-c/"
    page = PageFacts(**asyncio.run(read_page_facts(url)))
    found = asyncio.run(deal.assess_deal("t-storec", url, page))
    assert [f for f in found if f.status in {"confirmed", "proposed"}] == []


def test_real_page_is_read_only_and_careful(monkeypatch):
    def no_browser(*a, **k):
        raise AssertionError("the hidden browser must never open a real store page")
    monkeypatch.setattr("backend.tools.runahead.chromium", no_browser)
    monkeypatch.setattr("backend.tools.timer_test.chromium", no_browser)
    page = PageFacts(url="https://shop.example/item", host="shop.example", product={"name": "Ticket", "price": 89.0}, signals=[
        {"type": "claimed_discount", "selector": "#was", "text": "Was $129", "amount": 129.0},
        {"type": "countdown", "selector": "#t", "text": "Ends in 04:59"},
        {"type": "prechecked_addon", "selector": "#ins", "text": "Add insurance for $9.99", "amount": 9.99},
        {"type": "drip_fees", "selector": "#a", "text": "Service fee $18.50", "amount": 18.5},
        {"type": "drip_fees", "selector": "#b", "text": "Facility fee $6.00", "amount": 6.0}])
    found = {f.type: f for f in asyncio.run(deal.assess_deal("t-real", page.url, page))}
    assert found["claimed_discount"].status == "unverified" and "fake_discount" not in found
    assert found["countdown"].status == "proposed"
    assert found["prechecked_addon"].status == "confirmed"
    assert found["drip_fees"].status == "confirmed" and "$24.50" in found["drip_fees"].summary


def test_failed_tool_becomes_unverified_and_agent_never_raises(monkeypatch):
    async def slow(*a, **k):
        await asyncio.sleep(5)

    async def broken(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(deal, "TOOL_TIMEOUT_S", 0.2)
    monkeypatch.setattr(deal, "check_runahead", slow)
    monkeypatch.setattr(deal, "check_timer", broken)
    page = PageFacts(url="https://shop.example/x", host="shop.example")
    found = {f.type: f for f in asyncio.run(deal.assess_deal("t-fail", page.url, page))}
    assert found["drip_fees"].status == "unverified" and "longer than 0.2 s" in found["drip_fees"].summary
    assert found["resetting_timer"].status == "unverified" and "RuntimeError" in found["resetting_timer"].summary


def test_skeptic_rerun(monkeypatch):
    page = PageFacts(url="https://shop.example/x", host="shop.example",
                     signals=[{"type": "prechecked_addon", "selector": "#a", "text": "Protection plan $3.00", "amount": 3.0}])
    asyncio.run(deal.assess_deal("t-rerun", page.url, page))
    again = asyncio.run(deal.rerun("t-rerun", "prechecked_addon"))
    assert (again.type, again.status, again.rounds, again.challenged_by) == ("prechecked_addon", "confirmed", 1, "skeptic")
    assert asyncio.run(deal.rerun("t-rerun", "resetting_timer")).status == "rejected"  # no timer on the page
    assert asyncio.run(deal.rerun("nope", "drip_fees")).status == "unverified"


def test_bench_labels_match_pages():
    import csv
    from collections import Counter
    from backend.tests.content_reader import ROOT
    rows = [r for r in csv.DictReader(open(ROOT / "bench" / "labels.csv")) if r["page"].startswith("deal/")]
    assert all((ROOT / "bench" / r["page"]).exists() for r in rows)
    assert Counter(r["trick"] for r in rows) == {"fake_discount": 7, "resetting_timer": 6, "drip_fees": 7, "prechecked_addon": 6}


# ----------------------------------------------------------------------------- streaming + timer cache

def test_store_lab_streams_placeholders_first_and_collect_drops_them(monkeypatch):
    from backend.tools.common import ToolResult, finding as mk

    async def slow_runahead(cid, url, page):
        await asyncio.sleep(0.2)
        return ToolResult([mk(cid, "drip_fees", "confirmed", "$37.01 in fees appear only at checkout step 4.")])

    async def no_timer(cid, url, page):
        return ToolResult()
    monkeypatch.setattr(deal, "check_runahead", slow_runahead)
    monkeypatch.setattr(deal, "check_timer", no_timer)
    page = _page(DEMO_URL, 149.0, [{"type": "countdown", "selector": "#timer", "text": "Deal ends in 09:58"}])
    done = []
    streamed = asyncio.run(_collect(deal.iter_deal("t-stream", DEMO_URL, page, fresh=True,
                                                   on_tool_done=lambda name, secs: done.append(name))))
    assert [f.model for f in streamed[:2]] == [deal.PENDING, deal.PENDING]  # "still checking" rows come first
    assert {f.summary for f in streamed[:2]} == {"Checking checkout for late fees…", "Checking if the timer resets…"}
    assert set(done) == {"addons", "discount", "scarcity", "runahead", "timer"} and done.index("runahead") > done.index("addons")
    assert all(f.model != deal.PENDING for f in asyncio.run(deal.assess_deal("t-stream2", DEMO_URL, page, fresh=True)))


async def _collect(agen):
    return [f async for f in agen]


def test_timer_result_is_reused_for_ten_minutes_unless_fresh(monkeypatch):
    from backend.tools.common import ToolResult, finding as mk
    calls = []

    async def counting_timer(cid, url, page):
        calls.append(cid)
        return ToolResult([mk(cid, "resetting_timer", "confirmed", "The timer restarts at 09:59 on every visit.")])
    monkeypatch.setattr(deal, "check_timer", counting_timer)
    monkeypatch.setattr(deal, "TIMER_CACHE", {})
    url = "http://localhost:8787/storelab/audiodealz/?cache-test"
    page = _page(url, 149.0, [])
    first = asyncio.run(deal._timer("c1", url, page, fresh=False))
    again = asyncio.run(deal._timer("c2", url, page, fresh=False))
    assert len(calls) == 1 and again.findings[0].check_id == "c2" and "Timer test from" in again.findings[0].summary
    asyncio.run(deal._timer("c3", url, page, fresh=True))  # the refresh button
    assert len(calls) == 2
    monkeypatch.setitem(deal.TIMER_CACHE, url, (time.time() - deal.TIMER_CACHE_S - 1, first))
    asyncio.run(deal._timer("c4", url, page, fresh=False))  # older than 10 minutes: run it again
    assert len(calls) == 3
    real = "https://shop.example/item"
    asyncio.run(deal._timer("c5", real, page, fresh=False))
    assert real not in deal.TIMER_CACHE  # only Store Lab results are cached
