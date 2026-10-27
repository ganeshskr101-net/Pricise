"""Rechecker for the Skeptic's best_price rule (AGENT_ORCH=on with AGENT_PRICE=real), plus the adapter and card choice.

The Price Agent (Josh, backend/agents/price.py) emits ONE confirmed best_price finding, for its winner, with the evidence
ref listing:<store_id>:<product_id>:day<n>. adapt() adds the exact-match ref this module reads:

    store=<url>;model=0|1;variant=1;condition=0|1;warranty=1;price=<true total>

Only model and condition are checked against data. The catalog has no variant or warranty fields, so both are written
as 1 and the finding says "variant and warranty not in data, assumed equal". Any agent may emit best_price findings in
this shape, one per candidate; the card takes the cheapest confirmed one (best_price_on_card).

Rechecker: the flags from the ref plus a fresh store-safety verdict, for skeptic.check_best_price:
- AGENT_TRUST=real: trust.assess_store(url, f"{check_id}:recheck:{store}") (cached 5 min, so a store the Price Agent
  already vetted costs nothing). A store the Trust Agent could not check gives no verdict.
- AGENT_TRUST=stub: the seeded catalog flag (ok -> safe, risky -> caution, scam -> unsafe), like the Price Agent's stub mode.
The Skeptic blocks unsafe and an unchecked store; caution passes the rule, but the Price Agent never proposes a caution
store (with use_trust=True it rejects caution), so in practice only safe stores win. A best_price without the ref gets
no inputs and keeps its status. PriceResult.alt is the winner itself, not a runner-up: with the Price Agent's single
finding, a rejected winner leaves "no runner-up".
"""
from __future__ import annotations

import os
import re
from typing import Optional
from urllib.parse import urlsplit

from backend.agents import skeptic
from backend.contracts import BestPrice, Evidence, Finding, PageFacts, Reason

ASSUMED = "variant and warranty not in data, assumed equal"
REF_RE = re.compile(r"store=(?P<store>[^;]+);model=(?P<model>[01]);variant=(?P<variant>[01]);condition=(?P<condition>[01]);"
                    r"warranty=(?P<warranty>[01]);price=(?P<price>\d+(?:\.\d+)?)(?P<extra>(?:;[a-z_]+=[^;]*)*)")
OFFER_FLAGS = ("seller", "full_price")  # online offers (real pages) also carry these, for the Skeptic's rule
CAVEAT = "caveat:"  # a text evidence ref: what an approval of this offer comes with
LISTING_RE = re.compile(r"listing:(?P<store>[^:]+):(?P<product>[^:]+):day(?P<day>\d+)")
SEEDED = {"ok": "safe", "risky": "caution", "scam": "unsafe"}
NO_RUNNER_UP = "No runner-up: the Price Agent proposes one store only"


def exact_ref(store_url: str, model: bool, condition: bool, price: float) -> str:
    total = f"{price:.2f}".rstrip("0").rstrip(".")
    return f"store={store_url};model={int(model)};variant=1;condition={int(condition)};warranty=1;price={total}"


def offer_ref(url: str, offer: dict, *, model: bool, variant: bool) -> str:
    """The exact-match ref of an online offer on a real page: the usual flags plus seller, full_price, and the offer's
    name, domain and kind (new, open box, refurbished, used, monthly or installment price) for the Skeptic's reasons."""
    kind = offer.get("kind") or "new"
    new = kind not in ("open box", "refurbished", "used", "not new")
    total = f"{offer['total']:.2f}".rstrip("0").rstrip(".")
    clean = lambda v: re.sub(r"[;=]", " ", str(v))  # noqa: E731 - the ref's separators can't appear in a value
    return (f"store={url.replace(';', '%3B')};model={int(model)};variant={int(variant)};condition={int(new)};warranty=1;"
            f"price={total};seller=1;full_price={int(kind != 'monthly or installment price')};low={int(bool(offer.get('low')))};"
            f"kind={clean(kind)};domain={clean(offer['domain'])};name={clean(offer['store'])}"
            + (f";save={offer['save']:.2f}" if offer.get("save") else "")
            + (f";model_name={clean(offer['other_model'][0])};page_model={clean(offer['other_model'][1])}"
               if offer.get("other_model") else ""))


def parse_ref(f: Finding) -> Optional[dict]:
    """{"store", "match": {model, variant, condition, warranty[, seller, full_price]}, "price", "ref", "offer"} from the
    exact-match ref, or None. "offer" holds an online offer's extra fields ({} for Store Lab listings)."""
    for e in f.evidence:
        m = REF_RE.fullmatch(e.ref) if e.kind == "price_row" else None
        if m:
            extra = dict(kv.split("=", 1) for kv in m["extra"].split(";") if "=" in kv)
            match = {k: m[k] == "1" for k in ("model", "variant", "condition", "warranty")}
            match.update({k: extra[k] == "1" for k in OFFER_FLAGS if k in extra})
            return {"store": m["store"], "price": float(m["price"]), "ref": e.ref, "match": match, "offer": extra}
    return None


def caveats_of(f: Finding) -> list[str]:
    return [e.ref.removeprefix(CAVEAT).strip() for e in f.evidence if e.kind == "text" and e.ref.startswith(CAVEAT)]


def labels_for(offer: dict) -> dict[str, str]:
    """Clear fail texts for an online offer: "open box, not new", "monthly or installment price, not the full price"."""
    kind = offer.get("kind") or ""
    model = (f"a different model ({offer['model_name']}, not {offer['page_model']})" if offer.get("model_name")
             else "a different model")
    out = {"model": model, "variant": "a different colour or version"}
    if kind in ("open box", "refurbished", "used", "not new"):
        out["condition"] = f"{kind}, not new" if kind != "not new" else "not new"
    if kind == "monthly or installment price":
        out["full_price"] = "a monthly or installment price, not the full price"
    return out


def offer_summary(f: Finding, result) -> Optional[str]:
    """"Skeptic approved Woot $79.95: same item, new, full price, sold by Woot. Caveat: ..." for an online offer's
    decision (None for anything else). The rule's reason and caveat, word for word."""
    ref = parse_ref(f)
    if not ref or not ref["offer"].get("name"):
        return None
    o, name = ref["offer"], ref["offer"]["name"]
    head = f"{name} {_money(ref['price'])}"
    by = getattr(result, "decided_by", "rules")
    model = by if "nemotron" in by.lower() else None
    tail = f" (decided by {model})" if model else ""
    if result.status == "confirmed":
        save = f", {_money(float(o['save']))} less than here" if o.get("save") else ""
        low = ", unusually low and reviewed" if o.get("low") == "1" else ""
        why = (result.reason.rstrip(".") if model else
               f"same item, new, full price, sold by {name}; store checks passed"
               + ("; decided by rules (model unavailable)" if by == "rules (model unavailable)" else ""))
        text = f"Skeptic approved {head}{save}{low}: {why}{tail}."
        return text + (f" Caveat: {result.caveat.rstrip('.')}." if result.caveat else "")
    if result.status == "rejected":
        return f"Skeptic rejected {head}: {result.reason.rstrip('.')}{tail}."
    return f"Skeptic couldn't verify {head}: {result.reason}."


# ----------------------------------------------------------------------------- adapter (Price Agent -> exact-match ref)


def _rows(source: str, day: int) -> Optional[list[dict]]:
    """The listings again, from the same source as the Price Agent's run; None if that source can't answer now."""
    from backend.data import repo
    from backend.data.zetaris import ZetarisError

    if source == "zetaris":
        try:
            rows, _, now = repo.listings(day)
        except (ZetarisError, ValueError):
            return None
        return rows if now == "zetaris" else None
    return repo._catalog_listings(day) if source.startswith("fixture") else None


def _josh_ref(f: Finding, source: str, page: PageFacts, url: str) -> Optional[str]:
    listing = next((m for e in f.evidence if e.kind == "price_row" and (m := LISTING_RE.fullmatch(e.ref))), None)
    store_url = next((e.ref for e in f.evidence if e.kind == "url"), None)
    rows = _rows(source, int(listing["day"])) if listing and store_url else None
    if not rows:
        return None
    from backend.agents import price
    from backend.tools.discount import store_key

    _own, item = price._identify(rows, store_key(url, page).removeprefix("storelab/"), page)
    row = next((r for r in rows if (r["store_id"], r["product_id"]) == (listing["store"], listing["product"])), None)
    if not item or not row:
        return None
    return exact_ref(store_url, row["model_key"] == item["model_key"], row["condition"] == item["condition"], row["true_total"])


def adapt(res, page: PageFacts, url: str) -> list[Finding]:
    """The Price Agent's findings as it made them, except a confirmed best_price without the exact-match ref: that one
    gets the ref (and the ASSUMED note) when its listing can be read again. Blocking in Zetaris mode (one query)."""
    out = []
    for f in res.findings:
        ref = None
        if f.type == "best_price" and f.status == "confirmed" and parse_ref(f) is None:
            ref = _josh_ref(f, res.source, page, url)
        if ref:
            f = f.model_copy(update={"summary": f"{f.summary} Model and condition checked against data; {ASSUMED}.",
                                     "evidence": [*f.evidence, Evidence(kind="price_row", ref=ref, label="Exact match")]})
        out.append(f)
    return out


# ----------------------------------------------------------------------------- rechecker


def store_id(store_url: str) -> str:
    m = re.search(r"/(?:storelab|bench)/([^/]+)", urlsplit(store_url).path)
    return m[1] if m else (urlsplit(store_url).hostname or "store")


def _seeded(store_url: str) -> Optional[str]:
    from backend.data import catalog
    from backend.data.trusted_retailers import BRAND_NAMES, RETAILERS

    host = (urlsplit(store_url).hostname or "").removeprefix("www.")
    if host in RETAILERS or host in BRAND_NAMES:  # a real allowlisted retailer or brand store (Trust stub mode)
        return "safe"
    flag = next((s["trust"] for s in catalog.rows()["stores"] if s["store_id"] == store_id(store_url)), None)
    return SEEDED.get(flag)


async def store_verdict(store_url: str, check_id: str) -> Optional[str]:
    """safe | caution | unsafe, or None when nobody could check the store."""
    return (await store_check(store_url, check_id))[0]


async def store_check(store_url: str, check_id: str) -> tuple[Optional[str], list[str]]:
    """(verdict or None, the Trust Agent's reasons): what the Skeptic and its model review see about a store."""
    if os.environ.get("AGENT_TRUST", "stub").lower() != "real":
        v = _seeded(store_url)
        return v, ["allowlisted retailer (Trust Agent not run)"] if v else []
    from backend.agents import trust

    tv = await trust.assess_store(store_url, f"{check_id}:recheck:{store_id(store_url)}")
    return (tv["verdict"] if tv["checked"] else None), list(tv.get("reasons") or [])[:4]


def review_context(f: Finding, ref: dict, page: dict, credible: list[dict], verdict: Optional[str],
                   reasons: list[str]) -> dict:
    """What the deep lane sees for an unusually low offer: the offer, this page's product, the credible stores' prices and
    the store checks. Facts only, no instructions."""
    o = ref["offer"]
    p = (page or {}).get("product") or {}
    return {"offer": {"store": o.get("name"), "domain": o.get("domain"), "price": ref["price"], "condition": o.get("kind"),
                      "listing": f.summary.split(": ", 1)[-1][:160], "caveats_found": caveats_of(f)},
            "page_product": {"name": (p.get("name") or "")[:160], "brand": p.get("brand"), "price": p.get("price")},
            "credible_store_prices": [{"store": c.get("store"), "price": c.get("total")} for c in credible[:6]],
            "store_checks": {"verdict": verdict, "reasons": reasons}}


class Rechecker:
    """orchestrator.Rechecker for one check, best_price only. What it saw goes to chk["rechecks"] (by ref) for the card."""
    types = frozenset({"best_price"})

    def __init__(self, chk: dict) -> None:
        self.chk = chk

    async def __call__(self, f: Finding) -> Optional[dict]:
        ref = parse_ref(f)
        if ref is None:
            return None
        domain = ref["offer"].get("domain")  # an online offer: Trust checks the store's site, not the listing link
        verdict, reasons = await store_check(f"https://www.{domain}/" if domain else ref["store"], f.check_id)
        inputs = {"match": ref["match"], "trust_verdict": verdict}
        if ref["offer"]:
            inputs.update(labels=labels_for(ref["offer"]), caveats=caveats_of(f))
        if ref["offer"].get("low") == "1":  # unusually low: the deep lane decides, the hard rules validate
            import asyncio

            online = (self.chk.get("evidence_data") or {}).get("online") or {}
            ctx = review_context(f, ref, self.chk.get("page") or {}, online.get("shown") or [], verdict, reasons)
            inputs.update(low_review=True, review=await asyncio.to_thread(skeptic.review_low_offer, ctx))
            result = skeptic.decide_low_offer(f, ref["match"], verdict, inputs["labels"], inputs["caveats"], inputs["review"])
        else:
            result = skeptic.check_best_price(f, ref["match"], verdict, inputs.get("labels"), inputs.get("caveats") or ())
        self.chk.setdefault("rechecks", {})[ref["ref"]] = {**inputs, "reason": result.reason, "caveat": result.caveat,
                                                            "decided_by": result.decided_by}
        return inputs


# ----------------------------------------------------------------------------- the card's best price


def _name(f: Finding, ref: dict) -> str:
    """The Price Agent's wording "<Store>, $<total>: ..." gives the store name; else the store id from the URL."""
    head = f.summary.split(", $", 1)
    return head[0] if len(head) == 2 and head[0] else store_id(ref["store"])


def _money(v: float) -> str:
    return f"${v:,.0f}" if float(v).is_integer() else f"${v:,.2f}"


def best_price_on_card(chk: dict, findings: list[Finding], best: BestPrice, alt: Optional[dict]
                       ) -> tuple[BestPrice, Optional[dict]]:
    """The card's best price from best_price findings with the exact-match ref that the Skeptic confirmed, cheapest true
    total ("Checking…" while any is undecided: they arrive "confirmed" by their agent). The Price Agent's own verdict
    stays when it names the same store and total (its reasons are richer). Without such findings (real site, not in
    catalog, couldn't compare) the Price Agent's verdict stays as it is."""
    cands = [(f, r) for f in findings if f.type == "best_price" and (r := parse_ref(f))]
    if not cands:
        return best, alt
    decided = [(f, r) for f, r in cands if f.challenged_by == "skeptic" and f.status != "challenged"]
    if len(decided) < len(cands):
        return BestPrice(status="unknown", headline="Checking the best price…"), None
    if any(r["offer"] for _, r in cands):
        return _offers_on_card(chk, decided, best, alt)
    ok = [(f, r) for f, r in decided if f.status == "confirmed" and f.rounds >= 1]  # rounds 0: recheck failed, not checked
    if ok:
        f, r = min(ok, key=lambda x: (x[1]["price"], x[1]["store"]))
        if best.status == "good" and best.url == r["store"] and best.total == r["price"]:
            return best, alt
        name = _name(f, r)
        here = store_id(r["store"]) == store_id(chk.get("url") or "")
        reasons = [Reason(text="Same model and condition (variant and warranty not in data)", tone="good", proof="stores"),
                   Reason(text=f"{_money(r['price'])} true total, checked by the Skeptic", tone="good", proof="stores")]
        return (BestPrice(status="good", headline=f"{name} · {_money(r['price'])}", store=name, total=r["price"],
                          url=r["store"], reasons=reasons),
                None if here else {"store": name, "total": r["price"], "url": r["store"]})
    seen = chk.get("rechecks") or {}
    reasons = []
    for f, r in cands:
        why = seen.get(r["ref"], {}).get("reason", "rejected") if f.status == "rejected" else "couldn't verify"
        reasons.append(Reason(text=f"{_name(f, r)}, {_money(r['price'])}: not accepted by the Skeptic ({why})", tone="caution",
                              proof="stores"))
    reasons.append(Reason(text=NO_RUNNER_UP if len(cands) == 1 else "No runner-up left", tone="unknown"))
    return BestPrice(status="unknown", headline="No safe match", reasons=reasons), None


def _offers_on_card(chk: dict, decided: list, best: BestPrice, alt: Optional[dict]) -> tuple[BestPrice, Optional[dict]]:
    """Real pages: the cheapest online offer the Skeptic approved, with its caveat; with none approved, the Price Agent's
    own verdict (this page, or the same price elsewhere) plus the Skeptic's rejections."""
    seen = chk.get("rechecks") or {}
    ok = [(f, r) for f, r in decided if f.status == "confirmed" and f.rounds >= 1]
    no = [(f, r) for f, r in decided if f.status != "confirmed" or f.rounds < 1]
    rejected = [Reason(text=f"Skeptic rejected {r['offer'].get('name')} {_money(r['price'])}: "
                            f"{seen.get(r['ref'], {}).get('reason') or 'not accepted'}", tone="caution", proof="stores")
                for f, r in no if f.status == "rejected"]
    online = (chk.get("evidence_data") or {}).get("online") or {}
    when = fetched_reason(online)
    if not ok:
        return best.model_copy(update={"reasons": [*best.reasons, *rejected][:5 - len(when)] + when}), alt
    f, r = min(ok, key=lambda x: (x[1]["price"], x[1]["offer"].get("name", "")))
    name, caveat = r["offer"].get("name") or store_id(r["store"]), seen.get(r["ref"], {}).get("caveat")
    price = ((chk.get("page") or {}).get("product") or {}).get("price")
    save = round(price - r["price"], 2) if price else None
    reasons = [Reason(text=f"Same item, new, full price, sold by {name}: approved by the Skeptic", tone="good", proof="stores")]
    if save:
        reasons.append(Reason(text=f"{_money(r['price'])} vs {_money(price)} here: save {_money(save)}", tone="good", proof="stores"))
    full = [c.strip() for c in (caveat or "").split("; ") if c.strip()]
    if full:  # at most 2 short caveats here; the full list is on the evidence page (the store's row)
        reasons.append(Reason(text="Caveat: " + "; ".join(short_caveat(c) for c in full[:2]), tone="caution", proof="stores"))
        online = (chk.setdefault("evidence_data", {}).get("online") or {})
        for row in online.get("shown") or []:
            if row.get("store") == name:
                row["caveats"] = full
    reasons += when + rejected
    return (BestPrice(status="caution" if caveat else "good", headline=f"{name} · {_money(r['price'])}", store=name,
                      total=r["price"], url=r["store"], reasons=reasons[:5]),
            {"kind": "cheaper", "store": name, "total": r["price"], "url": r["store"], "save": save,
             **({"caveat": short_caveat(full[0]), "caveats": full} if full else {})})


def fetched_reason(online: dict) -> list[Reason]:
    """Only for a snapshot (the Zetaris USL): one line saying when its prices were fetched. A live search says nothing."""
    from backend.data import repo

    stamps = [x for x in online.get("fetched") or [] if "live search" not in x.get("what", "")]
    return [Reason(text=f"Prices from {repo.fetched_text(stamps)}", tone="unknown", proof="stores")] if stamps else []


def short_caveat(text: str) -> str:
    """"90-day Woot warranty, not JBL's manufacturer warranty (Woot's usual terms; ...)" -> the part before the brackets."""
    return re.split(r" \(", text, maxsplit=1)[0].strip().rstrip(".")
