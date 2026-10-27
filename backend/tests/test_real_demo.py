"""The real-retailer demo (JBL Flip 6 on Amazon), with no live calls: the Google Shopping search and sellers list recorded
from SerpApi on 27 Oct 2026 (fixtures/serpapi/jbl_flip_6_*.json), the Trust Agent's store checks stubbed as "safe".

Covers: Woot added as a credible retailer; an unusually low new offer goes to the Skeptic and can be approved with a caveat;
open box / installment offers can never be approved; a brand's official store on a retailer counts as credible; MSRP
discount wording; the Amazon third-party seller wording; sample-mode replay of a recorded run.
"""
import json
from pathlib import Path

import pytest

from backend.agents import price, skeptic
from backend.contracts import Evidence, Finding, PageFacts, PageProduct
from backend.data import trusted_retailers as tr
from backend.tools import discount, shopping

FIX = Path(__file__).parent / "fixtures" / "serpapi"
RAW = json.loads((FIX / "jbl_flip_6_blue.json").read_text())["shopping_results"]
SELLERS = json.loads((FIX / "jbl_flip_6_sellers.json").read_text())["product_results"]["stores"]
URL = "https://www.amazon.com/dp/B09GJZKQ3K"
NAME = ("JBL Flip 6 - Portable Bluetooth Speaker, Powerful Sound and deep bass, IP67 Waterproof, 12 Hours of Playtime, "
        "JBL PartyBoost for Multiple Speaker Pairing for Home, Outdoor and Travel (Blue)")
OPEN_BOX = {"title": "JBL Flip 6 Portable Waterproof Speaker - Blue (Open Box)", "source": "Walmart", "price": "$89.97",
            "extracted_price": 89.97, "second_hand_condition": "open box", "delivery": "Free delivery"}
FLIP7_OPEN_BOX = {"title": "JBL Flip 7 Portable Waterproof Speaker, Blue (Open Box)", "source": "Walmart", "price": "$68.53",
                  "extracted_price": 68.53, "extracted_old_price": 77.0, "second_hand_condition": "open box"}
OFFICIAL = {"title": "JBL Flip 6 Portable Waterproof Speaker - Blue", "source": "Walmart - JBL Store", "price": "$109.95",
            "extracted_price": 109.95, "delivery": "Free delivery"}


def page(was=129.95, price_=114.95, signals=None) -> PageFacts:
    sig = signals if signals is not None else [{"type": "claimed_discount", "text": f"List Price: ${was}", "amount": was,
                                                "selector": "#listPrice"}]
    return PageFacts(url=URL, host="www.amazon.com", product={"name": NAME, "brand": "JBL", "price": price_, "was_price": was},
                     signals=sig)


@pytest.fixture
def jbl(monkeypatch):
    calls = {"search": 0, "sellers": 0, "raw": list(RAW)}

    def fake_search(q):
        calls["search"] += 1
        return shopping.parse(calls["raw"])

    def fake_sellers(token):
        calls["sellers"] += 1
        return shopping.parse_sellers(SELLERS)

    monkeypatch.setenv("SERPAPI_KEY", "test-key-not-real")
    monkeypatch.setattr(shopping, "_search_serpapi", fake_search)
    monkeypatch.setattr(shopping, "_sellers_serpapi", fake_sellers)
    monkeypatch.setattr(price, "_vet_domains", lambda cid, ds: {d: {"verdict": "safe", "checked": True, "reasons": []} for d in ds})
    return calls


# ----------------------------------------------------------------------------- credible stores and the Skeptic's rules


def test_woot_is_a_credible_retailer_and_the_brands_official_store_on_a_retailer_counts():
    assert tr.credible_domain("Woot", "JBL", NAME) == ("woot.com", "allowlisted retailer")
    assert tr.credible_domain("Walmart - JBL Store", "JBL", NAME) == ("walmart.com", "the brand's official store on the retailer")
    assert tr.credible_domain("Walmart - OneCall (Authorized Dealer)", "JBL", NAME)[0] is None  # a dealer is not the brand
    assert tr.credible_domain("Walmart - JBL Store", "Sony", "Sony WH-1000XM5")[0] is None      # JBL's store, not Sony's
    assert tr.credible_domain("JBL", "JBL", NAME) == ("jbl.com", "the brand's own store")


def test_the_real_search_keeps_woot_as_unusually_low_and_drops_marketplace_and_used_offers(jbl):
    p = page().product
    kept, blocked, dropped = shopping.classify_offers(p, shopping.parse(RAW) + shopping.parse_sellers(SELLERS), p.price,
                                                      "amazon.com")
    assert [(o["source"], o["total"], o["low"]) for o in kept] == [
        ("Woot", 79.95, True), ("Best Buy", 114.95, False), ("JBL", 114.95, False), ("Walmart", 114.95, False),
        ("Walmart", 114.95, False)]
    why = {(d["source"], d["price"]): d["reason"] for d in dropped}
    assert why[("eBay - da-kar", 74.99)] == '"pre-owned" in its condition'
    assert why[("Walmart - OneCall (Authorized Dealer)", 114.95)] == "marketplace seller, not the retailer"
    assert why[("Samsung", 129.95)] == "not an allowlisted store"
    assert blocked == []  # this search had no open-box offer at a credible store


def test_unusually_low_new_offer_is_approved_with_its_warranty_caveat(jbl):
    res = price.assess("c1", URL, page())
    [woot] = [f for f in res.findings if f.type == "best_price"]
    assert (woot.status, woot.challenged_by, woot.rounds) == ("confirmed", "skeptic", 1)
    assert woot.summary.startswith("Skeptic approved Woot $79.95, $35 less than here, unusually low and reviewed: same item, "
                                   "new, full price, sold by Woot; store checks passed; decided by rules (model unavailable). "
                                   "Caveat: 90-day Woot warranty, not JBL's manufacturer warranty")
    assert woot.model == "rules (model unavailable)"  # no deep lane in tests: the rules stand in, and say so
    assert res.best_price.headline == "Woot · $79.95" and res.best_price.status == "caution"  # approved, with a caveat
    assert res.alt["kind"] == "cheaper" and res.alt["save"] == 35.0 and res.alt["caveat"].startswith("90-day Woot warranty")
    assert not [f for f in res.findings if f.type == "lowest_price"]  # Woot is cheaper: never "as good as it gets"
    assert {"Best Buy", "JBL", "Walmart"} <= {s["store"] for s in res.online["shown"]}  # the same-price stores: in the comparison
    assert jbl == {"search": 1, "sellers": 1, "raw": jbl["raw"]}


def test_open_box_at_a_credible_store_goes_to_the_skeptic_and_is_rejected(jbl):
    jbl["raw"] = [OPEN_BOX, *RAW]
    res = price.assess("c1", URL, page())
    by = {f.summary.split(":")[0]: f for f in res.findings if f.type == "best_price"}
    ob = by["Skeptic rejected Walmart $89.97"]
    assert ob.summary == "Skeptic rejected Walmart $89.97: open box, not new." and ob.status == "rejected"
    assert res.best_price.headline == "Woot · $79.95"
    assert "Skeptic rejected Walmart $89.97: open box, not new" in [r.text for r in res.best_price.reasons]


def test_open_box_can_never_be_approved_even_when_everything_else_holds():
    from backend import rechecker

    offer = {"total": 89.97, "domain": "walmart.com", "store": "Walmart", "kind": "open box"}
    ref = rechecker.offer_ref("https://www.walmart.com/ip/1", offer, model=True, variant=True)
    f = Finding(check_id="c", agent="price", type="best_price", status="proposed", summary="Walmart $89.97: open box",
                evidence=[Evidence(kind="price_row", ref=ref)])
    parsed = rechecker.parse_ref(f)
    for verdict in ("safe", "caution"):
        r = skeptic.check_best_price(f, parsed["match"], verdict, rechecker.labels_for(parsed["offer"]), ["anything"])
        assert (r.passed, r.status, r.reason) == (False, "rejected", "open box, not new")
    monthly = rechecker.offer_ref("u", {**offer, "kind": "monthly or installment price", "total": 36}, model=True, variant=True)
    m = rechecker.parse_ref(f.model_copy(update={"evidence": [Evidence(kind="price_row", ref=monthly)]}))
    assert m["match"]["full_price"] is False and m["match"]["condition"] is True
    r = skeptic.check_best_price(f, m["match"], "safe", rechecker.labels_for(m["offer"]))
    assert r.status == "rejected" and r.reason == "a monthly or installment price, not the full price"


def test_brand_official_store_offer_is_proposed_and_approved_naming_the_seller(jbl):
    jbl["raw"] = [OFFICIAL, *RAW]
    res = price.assess("c1", URL, page())
    official = next(f for f in res.findings if f.type == "best_price" and "Walmart $109.95" in f.summary)
    assert official.status == "confirmed" and official.challenged_by == "skeptic"
    assert res.online["shown"][1]["seller"] == "JBL Store"


def test_orchestrator_sends_every_cheaper_offer_to_the_skeptic(jbl, monkeypatch):
    """The demo path (AGENT_ORCH=on, AGENT_PRICE=real): proposals, then the Skeptic's decisions in the stream and trace."""
    from fastapi.testclient import TestClient

    from backend import stub_server

    monkeypatch.setenv("AGENT_ORCH", "on")
    monkeypatch.setenv("AGENT_PRICE", "real")
    jbl["raw"] = [OPEN_BOX, *RAW]
    client = TestClient(stub_server.app)
    cid = client.post("/check?fresh=1", json={"url": URL, "page": page().model_dump()}).json()["check_id"]
    body = client.get(f"/check/{cid}/events").text
    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    finals = {}
    for e in events:
        if e["kind"] == "finding" and e["finding"]["type"] == "best_price":
            finals[e["finding"]["summary"].split(":")[0].split(",")[0]] = e["finding"]
    statuses = [e["finding"]["status"] for e in events if e["kind"] == "finding" and e["finding"]["type"] == "best_price"]
    assert "proposed" in statuses and "challenged" in statuses
    assert finals["Skeptic approved Woot $79.95"]["status"] == "confirmed"
    assert finals["Skeptic rejected Walmart $89.97"]["status"] == "rejected"
    card = [e["card"] for e in events if e["kind"] == "final_card"][-1]
    assert card["best_price"]["headline"] == "Woot · $79.95"
    assert any(r["text"].startswith("Caveat: 90-day Woot warranty") for r in card["best_price"]["reasons"])
    trace = stub_server.agent_trace.path_for(cid).read_text().splitlines()
    rows = [json.loads(x) for x in trace]
    assert any(r["agent"] == "skeptic" and r["action"] == "reject" and r["target_agent"] == "price" for r in rows)
    assert any(r["agent"] == "skeptic" and r["action"] == "confirm" and "Woot" in r["output_summary"] for r in rows)


# ----------------------------------------------------------------------------- discount claims on real pages


def test_list_price_equal_to_msrp_is_real_against_msrp_and_says_what_others_charge(jbl):
    [f] = discount.check_discount("c", URL, page()).findings
    assert (f.type, f.status) == ("claimed_discount", "confirmed")
    assert f.summary == ("The $129.95 list price matches JBL's launch price (MSRP), so the discount is real against MSRP; "
                         "others charge the same or less. Credible stores now: Woot $79.95 lower, Best Buy $114.95 same, "
                         "JBL $114.95 same, Walmart $114.95 same.")
    assert any(e.kind == "text" and e.ref.startswith("msrp: $129.95") for e in f.evidence)


def test_inflated_list_price_is_proposed_never_called_fake(jbl):
    [f] = discount.check_discount("c", URL, page(was=199.99)).findings
    assert (f.type, f.status) == ("inflated_discount", "proposed")
    assert f.summary.startswith("Discount claim looks inflated: every credible store charges $79.95 to $114.95, well below "
                                "the $199.99 list price.")
    assert "fake" not in f.summary.lower().replace("not called fake", "")


def test_unknown_list_price_close_to_the_market_is_unverified_with_its_reason(jbl):
    [f] = discount.check_discount("c", URL, page(was=119.99)).findings
    assert (f.type, f.status) == ("claimed_discount", "unverified")
    assert "Unverified: the list price isn't a launch price we know" in f.summary


def test_without_a_search_the_claim_stays_unverified_with_its_reason(monkeypatch):
    monkeypatch.setenv("SERPAPI_KEY", "")
    [f] = discount.check_discount("c", URL, page(was=119.99)).findings
    assert f.status == "unverified" and f.summary.endswith("Unverified: no credible store had this item new to compare.")


# ----------------------------------------------------------------------------- Amazon seller


def test_amazon_third_party_seller_is_worded_neutrally():
    from backend.agents import trust

    assert trust.seller_text("www.amazon.com", {"sold_by": "Huppins", "ships_from": "Amazon"}) == (
        "Sold by Huppins, shipped by Amazon: a third-party seller on Amazon, not Amazon itself; Pricise didn't check the seller.")
    assert trust.seller_text("www.amazon.com", {"sold_by": "Amazon.com", "ships_from": "Amazon.com"}) == \
        "Sold by Amazon and shipped by Amazon."


def test_trust_reports_the_seller_without_a_warning(monkeypatch):
    import asyncio

    from backend.agents import trust
    from backend.tools import rdap

    async def registered(_client, _domain):
        from datetime import date
        return date(1994, 11, 1)

    monkeypatch.setattr(rdap, "registered_on", registered)
    pf = page(signals=[{"type": "seller", "text": "Sold by Huppins, ships from Amazon",
                        "detail": {"sold_by": "Huppins", "ships_from": "Amazon"}}])
    tv = asyncio.run(trust.assess_store(URL, "c", pf, use_cache=False))
    assert tv["verdict"] == "safe"
    [row] = [c for c in tv["checks"] if c["check"] == "seller"]
    assert row["status"] == "unchecked" and row["result"].startswith("Sold by Huppins, shipped by Amazon")


# ----------------------------------------------------------------------------- sample mode replays a recorded real run


def test_sample_mode_replays_a_recorded_real_run(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from backend import recordings, stub_server

    rec = {"name": "jbl", "recorded_at": "2026-10-27", "url": URL + "?th=1", "page": page().model_dump(),
           "evidence_data": {"online": {"query": "JBL Flip 6 Blue"}},
           "events": [{"kind": "finding", "finding": Finding(check_id="old", agent="price", type="best_price", status="confirmed",
                                                             summary="Skeptic approved Woot $79.95", challenged_by="skeptic",
                                                             model="nvidia/nemotron-3-ultra-550b-a55b").model_dump()},
                      {"kind": "final_card", "card": {"check_id": "old", "title": "Check before you buy", "subtitle": "0 tricks",
                                                      "tone": "caution", "safe": {"status": "good", "headline": "Looks safe"},
                                                      "honest": {"status": "good", "headline": "Looks honest"},
                                                      "best_price": {"status": "caution", "headline": "Woot · $79.95"}}}]}
    (tmp_path / "jbl.json").write_text(json.dumps(rec))
    monkeypatch.setattr(recordings, "SAMPLES_DIR", tmp_path)
    monkeypatch.setenv("SAMPLE_MODE", "true")
    client = TestClient(stub_server.app)
    cid = client.post("/check", json={"url": "https://www.amazon.com/JBL-Flip-6/dp/B09GJZKQ3K/ref=x", "page": None}).json()["check_id"]
    events = [json.loads(x[6:]) for x in client.get(f"/check/{cid}/events").text.splitlines() if x.startswith("data: ")]
    assert events[0]["finding"]["check_id"] == cid and events[0]["finding"]["summary"] == "Skeptic approved Woot $79.95"
    assert events[-1]["card"]["subtitle"] == "0 tricks · recorded from a real run on 27 Oct 2026"
    assert client.get(f"/check/{cid}").json()["evidence_data"] == {"online": {"query": "JBL Flip 6 Blue"}}
    other = client.post("/check", json={"url": "https://www.amazon.com/dp/B000000000", "page": None}).json()["check_id"]
    assert "replay" not in client.get(f"/check/{other}").json()  # no recording: the scripted stub, as before


def test_recording_is_built_from_a_finished_check():
    from backend import recordings

    chk = {"url": URL, "page": page().model_dump(), "findings": [{"summary": "a"}], "final_card": {"title": "t"},
           "evidence_data": {"x": 1}}
    rec = recordings.from_check(chk, "jbl")
    assert [e["kind"] for e in rec["events"]] == ["finding", "final_card"] and rec["url"] == URL
    with pytest.raises(ValueError):
        recordings.from_check({**chk, "final_card": None}, "jbl")
    assert recordings.page_key(URL + "?th=1") == recordings.page_key("https://amazon.com/JBL/dp/B09GJZKQ3K/ref=sr") == \
        "amazon.com/dp/B09GJZKQ3K"


def test_query_uses_the_title_and_colour_not_a_store_item_code():
    assert shopping.query_for(PageProduct(name=NAME, brand="JBL", model="JBLFLIP6BLUAM")) == "JBL Flip 6 Blue"
    assert shopping.query_for(PageProduct(brand="Sony", model="WH-1000XM5")) == "Sony WH-1000XM5"


def test_flip_7_open_box_is_rejected_for_both_reasons(jbl):
    jbl["raw"] = [FLIP7_OPEN_BOX, *RAW]
    res = price.assess("c1", URL, page())
    [f7] = [f for f in res.findings if f.type == "best_price" and "$68.53" in f.summary]
    assert f7.status == "rejected"
    assert f7.summary == "Skeptic rejected Walmart $68.53: a different model (Flip 7, not Flip 6), open box, not new."
    assert res.best_price.headline == "Woot · $79.95"


def test_a_card_signup_offer_is_not_a_price_or_a_discount(jbl):
    visa = {"type": "claimed_discount", "text": "Get $50 off instantly: pay $64.95 upon approval for Amazon Visa", "amount": 64.95}
    p = page(signals=[visa])
    p.product.was_price = None
    assert discount.check_discount("c", URL, p).findings == []  # no discount claim on the page apart from the card offer
    p2 = page(signals=[visa, {"type": "claimed_discount", "text": "List Price: $129.95", "amount": 129.95}])
    [f] = discount.check_discount("c", URL, p2).findings
    assert f.summary.startswith("The $129.95 list price matches JBL's launch price (MSRP)") and "64.95" not in f.summary


# ----------------------------------------------------------------------------- unusually low offers: the deep lane decides

ULTRA = "nvidia/nemotron-3-ultra-550b-a55b"


def _review(decision, reasons=("Woot is Amazon-owned and sells new stock",), caveats=("Check the warranty",)):
    return {"decision": decision, "reasons": list(reasons), "caveats": list(caveats), "model": ULTRA}


def _low(kind="new", **flags):
    from backend import rechecker

    offer = {"total": 79.95, "domain": "woot.com", "store": "Woot", "kind": kind, "low": True}
    ref = rechecker.offer_ref("https://www.woot.com/x", offer, model=flags.get("model", True), variant=True)
    f = Finding(check_id="c", agent="price", type="best_price", status="proposed", summary="Woot $79.95: new",
                evidence=[Evidence(kind="price_row", ref=ref), Evidence(kind="text", ref="caveat: 90-day Woot warranty")])
    return f, rechecker.parse_ref(f)


def test_model_approves_an_unusually_low_offer_with_its_reasons_and_caveats():
    from backend import rechecker

    f, ref = _low()
    r = skeptic.decide_low_offer(f, ref["match"], "safe", rechecker.labels_for(ref["offer"]), rechecker.caveats_of(f),
                                 _review("approve"))
    assert (r.status, r.decided_by) == ("confirmed", ULTRA)
    assert r.reason == "Woot is Amazon-owned and sells new stock" and r.caveat == "90-day Woot warranty"  # "Check the warranty" is the same topic: deduped
    assert rechecker.offer_summary(f, r) == ("Skeptic approved Woot $79.95, unusually low and reviewed: Woot is Amazon-owned "
                                             f"and sells new stock (decided by {ULTRA}). Caveat: 90-day Woot warranty.")


def test_model_rejects_an_unusually_low_offer():
    from backend import rechecker

    f, ref = _low()
    r = skeptic.decide_low_offer(f, ref["match"], "safe", {}, (), _review("reject", ["Price too far below every other store"]))
    assert (r.status, r.decided_by, r.reason) == ("rejected", ULTRA, "Price too far below every other store")
    assert rechecker.offer_summary(f, r) == f"Skeptic rejected Woot $79.95: Price too far below every other store (decided by {ULTRA})."


def test_model_can_never_approve_a_hard_rule_failure_and_the_trace_says_so():
    from backend import rechecker

    for kind, flags, why in (("open box", {}, "open box, not new"), ("new", {"model": False}, "a different model"),
                             ("monthly or installment price", {}, "a monthly or installment price, not the full price")):
        f, ref = _low(kind, **flags)
        r = skeptic.decide_low_offer(f, ref["match"], "safe", rechecker.labels_for(ref["offer"]), (), _review("approve"))
        assert (r.status, r.decided_by) == ("rejected", "rules (hard rule overrides the model)")
        assert r.reason == f"{why} ({ULTRA} approved it, but a hard rule failed, so the rule decides)"
    f, ref = _low()
    r = skeptic.decide_low_offer(f, ref["match"], "unsafe", {}, (), _review("approve"))  # a store Trust flagged
    assert r.status == "rejected" and r.reason.startswith("store flagged unsafe")


def test_model_unavailable_or_bad_json_falls_back_to_the_rules(monkeypatch):
    from backend.llm import meterless

    f, ref = _low()
    r = skeptic.decide_low_offer(f, ref["match"], "safe", {}, ("90-day Woot warranty",), None)
    assert (r.status, r.decided_by) == ("confirmed", "rules (model unavailable)")
    assert r.reason.endswith("decided by rules (model unavailable)") and r.caveat == "90-day Woot warranty"
    assert skeptic.parse_review("Sure! I approve.", ULTRA) is None
    assert skeptic.parse_review('{"decision": "maybe", "reasons": []}', ULTRA) is None
    assert skeptic.parse_review('<think>x</think>{"decision": "approve", "reasons": ["ok"], "caveats": []}', ULTRA)["decision"] == "approve"
    monkeypatch.setattr(meterless, "configured", lambda lane="fast": True)
    monkeypatch.setattr(meterless, "deep", lambda *a, **k: (_ for _ in ()).throw(TimeoutError()))
    assert skeptic.review_low_offer({"offer": {}}) is None
    monkeypatch.setattr(meterless, "deep", lambda *a, **k: ("not json at all", ULTRA))
    assert skeptic.review_low_offer({"offer": {}}) is None


def test_orchestrator_records_which_model_decided(jbl, monkeypatch):
    from fastapi.testclient import TestClient

    from backend import stub_server

    seen = {}

    def review(ctx):
        seen.update(ctx)
        return _review("approve", ["Woot is an Amazon-owned retailer selling it new"], ["90-day Woot warranty"])

    monkeypatch.setattr(skeptic, "review_low_offer", review)
    monkeypatch.setenv("AGENT_ORCH", "on")
    monkeypatch.setenv("AGENT_PRICE", "real")
    jbl["raw"] = [OPEN_BOX, *RAW]
    client = TestClient(stub_server.app)
    cid = client.post("/check?fresh=1", json={"url": URL, "page": page().model_dump()}).json()["check_id"]
    client.get(f"/check/{cid}/events")
    assert seen["offer"]["store"] == "Woot" and seen["page_product"]["price"] == 114.95
    assert {c["store"] for c in seen["credible_store_prices"]} >= {"Best Buy", "Walmart"} and seen["store_checks"]["verdict"] == "safe"
    rows = [json.loads(x) for x in stub_server.agent_trace.path_for(cid).read_text().splitlines()]
    woot = [r for r in rows if r["agent"] == "skeptic" and "Woot" in r["output_summary"]][-1]
    assert woot["action"] == "confirm" and woot["model"] == ULTRA
    ob = [r for r in rows if r["agent"] == "skeptic" and "$89.97" in r["output_summary"]][-1]
    assert ob["action"] == "reject" and ob["model"] in ("rules", ULTRA)


def test_rule_rejections_are_one_line_with_the_list_on_the_evidence_page(jbl):
    res = price.assess("c1", URL, page())
    [line] = [f for f in res.findings if f.type == "offers_filtered"]
    assert line.summary == ("Rejected 39 offers by rule: 21 not from a credible store, 12 used or refurbished, "
                            "6 marketplace sellers.")
    groups = {d["group"] for d in res.online["dropped"]}
    assert {"Unknown store", "Used or refurbished", "Marketplace seller"} <= groups  # the evidence page's grouped list


# ----------------------------------------------------------------------------- fixes from the live capture (27 Oct 2026)

PAGE_TEXT = ("JBL Flip 6 - Portable Bluetooth Speaker. Bold JBL Original Pro Sound. IP67 waterproof and dustproof: take it to "
             "the pool or the park. 12 hours of playtime. PartyBoost lets you pair multiple speakers.")


def _chat_check():
    pf = page(signals=[{"type": "page_text", "text": PAGE_TEXT}])
    return {"check_id": "c", "url": URL, "page": pf.model_dump(), "findings": [], "final_card": {}, "evidence_data": {}}


def test_chat_never_states_a_rating_as_general_knowledge_and_answers_from_the_page(monkeypatch):
    """The live answer was "Generally, JBL Flip 6 ... is rated IPX7 waterproof"; the page says IP67."""
    from backend.agents import chat
    from backend.llm import meterless

    monkeypatch.setattr(meterless, "complete", lambda *a, **k: "Generally, JBL Flip 6 is rated IPX7 waterproof, so it can "
                                                                "handle splashes and a quick dip.")
    out = chat.answer(_chat_check(), "Is it waterproof?").answer
    assert "IPX7" not in out and out.startswith("According to this page") and "IP67" in out


def test_generally_sentences_keep_no_specs_numbers_prices_or_comparisons():
    from backend.agents import chat

    page_text = PAGE_TEXT
    assert chat.supported("Generally, it lasts about 12 hours.", page_text) == chat.DOESNT_SAY
    assert chat.supported("Generally, it costs $99 at most stores.", page_text) == chat.DOESNT_SAY
    assert chat.supported("Generally, the Flip 6 is louder than the Flip 5.", page_text) == chat.DOESNT_SAY
    assert chat.supported("Generally, portable speakers are easy to carry.", page_text) == \
        "Generally, portable speakers are easy to carry."
    both = "According to this page, it is IP67 waterproof and dustproof. Generally, speakers like this float."
    assert chat.supported(both, page_text) == "According to this page, it is IP67 waterproof and dustproof."


def test_a_discount_real_against_msrp_is_outlined_neutrally_not_as_a_trick(jbl):
    [f] = discount.check_discount("c", URL, page()).findings
    assert (f.type, f.status) == ("claimed_discount", "confirmed")
    [sel] = [e for e in f.evidence if e.kind == "selector"]
    assert sel.label == "Discount: real vs launch price"  # extension/sidepanel/panel.js draws it grey ("neutral"), never red


def test_caveats_are_deduped_and_never_contradict_the_store_checks():
    assert skeptic.merge_caveats(
        ["90-day Woot warranty, not JBL's manufacturer warranty", "colour not named in the listing: choose Blue"],
        ["Check that the warranty is the manufacturer's", "Make sure Blue is selected (colour)",
         "Returns and contact details weren't verified", "Stock may sell out quickly"]) == [
        "90-day Woot warranty, not JBL's manufacturer warranty", "colour not named in the listing: choose Blue",
        "Stock may sell out quickly"]


def test_panel_shows_at_most_two_short_caveats_and_the_evidence_row_has_all(jbl, monkeypatch):
    monkeypatch.setattr(skeptic, "review_low_offer", lambda ctx: _review(
        "approve", ["Woot is Amazon-owned and sells new stock"], ["Deal ends in about 5 days", "Limit 1 per customer"]))
    res = price.assess("c1", URL, page())
    [cav] = [r.text for r in res.best_price.reasons if r.text.startswith("Caveat:")]
    assert cav == ("Caveat: 90-day Woot warranty, not JBL's manufacturer warranty; colour not named in the listing: "
                   "choose Blue")
    assert res.alt["caveats"] == ["90-day Woot warranty, not JBL's manufacturer warranty (Woot's usual terms, check the "
                                  "listing)", "colour not named in the listing: choose Blue", "Deal ends in about 5 days",
                                  "Limit 1 per customer"]


def test_sellers_lookup_uses_the_multi_store_product_not_woots_own_listing():
    p = page().product
    offers = shopping.parse(RAW)
    best = shopping.best_match_token(p, offers)
    assert best == RAW[0]["immersive_product_page_token"] and RAW[0]["source"] == "Walmart"  # not Woot's (cheaper) one


def test_usl_path_adds_the_sellers_list_so_best_buy_and_jbl_show(jbl, monkeypatch):
    """Live capture: the USL had only Woot and Walmart for this product, and the panel said "Best Buy (not found)"."""
    from backend.data import repo

    usl = [o for o in shopping.parse(RAW) if o["source"] in ("Woot", "Walmart")]
    monkeypatch.setattr(repo, "usl_offers", lambda key: (usl, "SELECT ... FROM lightning.metastore.pricise.pricise_usl.safe_price"))
    tokens = {}
    monkeypatch.setattr(shopping, "_sellers_serpapi", lambda t: tokens.setdefault("t", t) and shopping.parse_sellers(SELLERS))
    shopping.LIVE["jblflip6blue"] = shopping.parse(RAW)  # this process's live search (the Deal Agent's) had the tokens
    try:
        res = price.assess("c1", URL, page())
    finally:
        shopping.LIVE.clear()
    assert tokens["t"] == RAW[0]["immersive_product_page_token"]
    assert [s["store"] for s in res.online["shown"]] == ["Woot", "Best Buy", "JBL", "Walmart"]
    assert res.online["also_checked"] == []


def test_also_checked_says_not_found_only_when_true(jbl):
    jbl["raw"] = [r for r in RAW if r["source"] != "Woot"] + [
        {"title": "JBL Flip 6 Speaker - Blue (Refurbished)", "source": "Best Buy", "price": "$89.99", "extracted_price": 89.99,
         "second_hand_condition": "refurbished"}]
    from backend.agents.price import also_checked

    p = page().product
    offers = shopping.parse(jbl["raw"])
    kept, blocked, dropped = shopping.classify_offers(p, offers, p.price, "amazon.com")
    assert also_checked(p, offers, {"walmart.com"}, "amazon.com", dropped, blocked) == [
        "Best Buy (used or refurbished only)", "Target (not found)"]


def test_no_token_anywhere_costs_one_live_search_then_the_sellers_list(jbl, monkeypatch):
    """The re-capture: the cached search had no token ("sellers list (no token)"), so Best Buy and JBL.com never showed."""
    from backend.data import repo

    cached = [{k: v for k, v in o.items() if k not in ("token", "multi")} for o in shopping.parse(RAW)]
    repo.OFFERS_MEMORY["jblflip6blue"] = (__import__("time").time(), cached)  # an older cache: no tokens
    shopping.LIVE.clear()
    res = price.assess("c1", URL, page())
    assert jbl["search"] == 1 and jbl["sellers"] == 1  # one search for the token, one sellers lookup
    assert [s["store"] for s in res.online["shown"]] == ["Woot", "Best Buy", "JBL", "Walmart"]
    assert res.online["sellers_source"] == "serpapi (token from a new search)" and res.online["also_checked"] == []
    assert repo.cached_tokens("jblflip6blue", 0)  # the token is cached with the results now
    price.assess("c2", URL, page())
    assert jbl["search"] == 1 and jbl["sellers"] == 1  # the next check uses the caches: no new SerpApi call
    shopping.LIVE.clear()


def test_not_found_only_when_absent_from_the_search_and_the_sellers_list(jbl):
    jbl["raw"] = [r for r in RAW if r["source"] in ("Woot", "Walmart")]  # Best Buy only in the sellers list
    shopping.LIVE.clear()
    res = price.assess("c1", URL, page())
    assert "Best Buy" in [s["store"] for s in res.online["shown"]]
    assert all(not a.startswith("Best Buy") for a in res.online["also_checked"])
    shopping.LIVE.clear()


def test_sellers_lookup_prefers_a_result_naming_the_pages_colour():
    p = page().product  # "... (Blue)"
    teal = {"title": "JBL Flip 6 Portable Speaker - Teal", "source": "Walmart", "extracted_price": 114.95,
            "immersive_product_page_token": "teal", "multiple_sources": True}
    blue = {"title": "JBL Flip 6 Portable Waterproof Speaker - Blue", "source": "Target", "extracted_price": 114.95,
            "immersive_product_page_token": "blue", "multiple_sources": True}
    plain = {"title": "JBL FLIP 6 Bluetooth Portable Waterproof Speaker", "source": "Walmart", "extracted_price": 114.95,
             "immersive_product_page_token": "plain", "multiple_sources": True}
    assert shopping.best_match_token(p, shopping.parse([plain, teal, blue])) == "blue"  # teal never: other colour
    assert shopping.best_match_token(p, shopping.parse([plain, teal])) == "plain"
