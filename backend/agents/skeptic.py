"""Skeptic: challenges other agents' findings with six pure rules. No model decides pass or fail.

Each rule takes plain arguments (no database, no I/O) and returns a RuleResult. "passed" means the finding's claim holds.
next_status() applies a result to a finding and stops after 2 challenge rounds. card_wording() builds the card text from
rules only. challenge_reason() is the one optional model call: it only rewords result.reason, never the decision.

Later wiring: call backend.agents.deal.rerun(check_id, finding_type) for a fresh finding, run the matching rule, then
next_status(). Timer frames from backend.tools.timer_test.sample_timer() go through visits_from_frames() first.
"""
from __future__ import annotations

import json
import logging
import re
import statistics
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping, Optional, Sequence

from backend.contracts import Finding, Status
from backend.llm import meterless

log = logging.getLogger("pricise.skeptic")

MAX_ROUNDS = 2
MIN_TIMER_VISITS = 3
MIN_HISTORY_DAYS = 30
PRICE_TOLERANCE = 0.01  # a sale price within 1% of the normal price is the normal price
RESET_TOLERANCE_S = 1.5  # same slack as timer_test.restarted()
SAFE_VERDICTS = ("safe", "caution")
MATCH_FAILS = {"model": "different model", "variant": "different variant", "condition": "refurbished",
               "warranty": "different warranty", "seller": "not sold by the retailer or the brand's official store",
               "full_price": "not a full one-time price"}
OFFER_ONLY = {"seller", "full_price"}  # checked only when the proposal carries them (online offers on real pages)
TYPE_LABEL = {"fake_discount": "this discount", "claimed_discount": "this discount", "inflated_discount": "this discount", "resetting_timer": "this countdown timer",
              "countdown": "this countdown timer", "drip_fees": "the checkout fees", "prechecked_addon": "the pre-ticked add-on",
              "scarcity": "this pressure message", "best_price": "the best price", "domain_age": "the store's age"}
_THINK_RE = re.compile(r"<think>.*?(?:</think>|$)", re.DOTALL | re.IGNORECASE)
_REASON_TIMEOUT_S = 8.0


@dataclass(frozen=True)
class RuleResult:
    passed: bool
    status: Status
    reason: str
    caveat: str = ""  # an approval's condition the shopper should know (e.g. a retailer-only warranty), shown with it
    decided_by: str = "rules"  # "rules", a Nemotron model name, "rules (model unavailable)", "rules (hard rule overrides ...)"


def _fail(status: Status, reason: str) -> RuleResult:
    return RuleResult(False, status, reason)


# Rule 1 ------------------------------------------------------------------------------------------------------------

def check_evidence(finding: Finding) -> RuleResult:
    """A finding with no evidence entries is rejected; otherwise it keeps its status."""
    if not finding.evidence:
        return _fail("rejected", "no evidence attached")
    return RuleResult(True, finding.status, f"{len(finding.evidence)} evidence item(s) attached")


# Rule 2 ------------------------------------------------------------------------------------------------------------

def visits_from_frames(frames: Sequence[Mapping[str, Any]], tolerance_s: float = RESET_TOLERANCE_S) -> list[dict[str, bool]]:
    """Turn timer_test.sample_timer() frames into check_timer() visits.

    Every frame comes from a new browser context, so fresh_session is True. Frames without a readable countdown
    ("remaining_s" or "at" missing or None) are left out, so they can never count as a reset. The first readable frame
    is the baseline and counts as a reset only if every later frame reset; each later frame resets when it shows more
    time left than a real countdown could (the previous value minus the time between visits, plus the tolerance).
    deal.EVIDENCE["timer_frames"] (from timer_test.check_timer) carries "at" and "remaining_s", so it converts directly.
    """
    readable = [f for f in frames if f.get("remaining_s") is not None and f.get("at") is not None]
    later = [b["remaining_s"] > a["remaining_s"] - (b["at"] - a["at"]) + tolerance_s for a, b in zip(readable, readable[1:])]
    if not readable:
        return []
    baseline = bool(later) and all(later)
    return [{"fresh_session": True, "reset": r} for r in [baseline, *later]]


def check_timer(finding: Finding, visits: Sequence[Mapping[str, bool]]) -> RuleResult:
    """A timer is fake only if at least 3 visits, each in a fresh session, ALL show a reset."""
    if len(visits) < MIN_TIMER_VISITS:
        return _fail("unverified", f"only {len(visits)} visit(s); need {MIN_TIMER_VISITS}")
    if not all(v.get("fresh_session", False) for v in visits):
        return _fail("unverified", "a visit was not in a fresh session")
    if not all(v.get("reset", False) for v in visits):
        kept = sum(not v.get("reset", False) for v in visits)
        return _fail("rejected", f"timer kept counting down on {kept} of {len(visits)} visits")
    return RuleResult(True, "confirmed", f"timer reset on all {len(visits)} fresh visits")


# Rule 3 ------------------------------------------------------------------------------------------------------------

def check_best_price(finding: Finding, match: Mapping[str, bool], trust_verdict: Optional[str],
                     labels: Optional[Mapping[str, str]] = None, caveats: Sequence[str] = ()) -> RuleResult:
    """Pass only if model, variant, condition and warranty all match AND the store is "safe" or "caution".
    Online offers also carry seller (the retailer or the brand's official store) and full_price (not monthly/installment).
    labels: clearer fail texts for this proposal ({"condition": "open box, not new"}). caveats: what a pass comes with
    (a retailer-only warranty, an unusually low price): they never decide, they are shown next to the approval.
    A failed condition or full_price can never pass, whatever else holds."""
    labels = labels or {}
    fails = [labels.get(key, why) for key, why in MATCH_FAILS.items()
             if (key in match or key not in OFFER_ONLY) and not match.get(key, False)]
    if trust_verdict == "unsafe":
        fails.append("store flagged unsafe")
    elif trust_verdict not in SAFE_VERDICTS:
        fails.append("store safety not checked")
    if fails:
        return _fail("rejected", ", ".join(dict.fromkeys(fails)))
    extra = "; ".join(c for c in caveats if c)
    return RuleResult(True, "confirmed", f"same product and store is {trust_verdict}", extra)


# Rule 4 ------------------------------------------------------------------------------------------------------------

def _as_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _span_days(dates: Sequence[date]) -> int:
    """Days covered, counting the first and last day: day 0 to day 29 is 30 days."""
    return (max(dates) - min(dates)).days + 1 if dates else 0


def check_discount(finding: Finding, history_rows: Sequence[tuple[date | datetime | str, float]],
                   sale_price: Optional[float] = None) -> RuleResult:
    """A discount is fake if the sale price was the normal price (median of the rows before the sale, 1% tolerance).

    Without sale_price, the latest row is the sale and only rows dated before it count as history. With sale_price,
    every row is history from before the sale. The history must cover at least 30 days, otherwise unverified.
    """
    rows = sorted(((_as_date(d), float(p)) for d, p in history_rows), key=lambda r: r[0])
    if sale_price is None:
        if not rows:
            return _fail("unverified", "no price history")
        sale_day, sale_price = rows[-1]
        rows = [r for r in rows if r[0] < sale_day]
    days = _span_days([d for d, _ in rows])
    if days < MIN_HISTORY_DAYS:
        return _fail("unverified", f"only {days} day(s) of history before the sale; need {MIN_HISTORY_DAYS}")
    normal = statistics.median(p for _, p in rows)
    if sale_price >= normal * (1 - PRICE_TOLERANCE):
        return RuleResult(True, "confirmed", f"sale price {sale_price:.2f} was the normal price {normal:.2f} over {days} days")
    return _fail("rejected", f"sale price {sale_price:.2f} is below the normal price {normal:.2f} over {days} days")


# Rule 5 ------------------------------------------------------------------------------------------------------------

def next_status(finding: Finding, result: RuleResult) -> Finding:
    """A NEW finding with the challenge applied. After 2 rounds it becomes "unverified" instead of looping."""
    rounds = finding.rounds + 1
    if rounds > MAX_ROUNDS:
        update: dict[str, Any] = {"status": "unverified", "rounds": MAX_ROUNDS}
    else:
        update = {"status": result.status, "rounds": rounds}
    return finding.model_copy(update={**update, "challenged_by": "skeptic"}, deep=True)


# Rule 6 ------------------------------------------------------------------------------------------------------------

def _label(finding: Finding) -> str:
    return TYPE_LABEL.get(finding.type, f"this {finding.type.replace('_', ' ')}")


def card_wording(finding: Finding) -> str:
    """Card text from rules only. Unverified or no evidence -> "couldn't verify", never the finding's own guess."""
    label = _label(finding)
    if finding.status == "unverified" or not finding.evidence:
        return f"We couldn't verify {label}."
    if finding.status == "confirmed":
        return finding.summary.strip() or f"Confirmed: {label}."
    if finding.status == "rejected":
        return f"Checked {label}: no problem found."
    return f"Still checking {label}."


# Optional wording helper -------------------------------------------------------------------------------------------

def _clean_reply(text: str) -> str:
    return " ".join(meterless.strip_reasoning(_THINK_RE.sub("", text or "")).split())


def challenge_reason(finding: Finding, result: RuleResult) -> str:
    """One plain-English sentence for the challenge, from meterless.deep. Falls back to result.reason.

    Returns only text: it can never change result.passed or result.status.
    """
    prompt = ("In one short plain-English sentence for a shopper, explain this check result. Do not add facts.\n"
              f"Finding type: {finding.type}\nFinding: {finding.summary}\n"
              f"Result: {result.status}\nRule reason: {result.reason}"
              + (f"\nCaveat: {result.caveat}" if result.caveat else ""))
    try:
        text, _ = meterless.deep([{"role": "user", "content": prompt}], timeout=_REASON_TIMEOUT_S,
                                 temperature=0, max_tokens=200)
    except Exception as err:  # any failure: keep the rule's own reason
        log.info("skeptic challenge_reason fell back (%s)", type(err).__name__)
        return result.reason
    return _clean_reply(text) or result.reason


# Unusually low offers: the deep lane decides, the hard rules validate -------------------------------------------------

_REVIEW_TIMEOUT_S = 20.0
REVIEW_PROMPT = (
    "You are the Skeptic in a shopping assistant. A credible store offers the item on this page for an unusually low price. "
    "Decide whether a shopper can safely be pointed to this offer. Use only the facts given; never invent store policies or "
    "prices. Reply with JSON only, exactly: "
    '{"decision": "approve" or "reject", "reasons": ["short reason", ...], "caveats": ["what the shopper should check", ...]}')


def review_low_offer(context: Mapping[str, Any]) -> Optional[dict]:
    """The deep lane's decision on one unusually low offer: {"decision", "reasons", "caveats", "model"}, or None when the
    model isn't configured, fails, or returns anything but that JSON. context: the offer, the page's product, the credible
    stores' prices and the store checks. Offer titles come from Google Shopping, so they go in as untrusted text."""
    if not meterless.configured("deep"):
        return None
    facts = json.dumps(context, ensure_ascii=False)[:3000]
    messages = [{"role": "system", "content": meterless.trusted_prefix(REVIEW_PROMPT)},
                {"role": "user", "content": meterless.untrusted(facts)}]
    try:
        text, model = meterless.deep(messages, timeout=_REVIEW_TIMEOUT_S, temperature=0, max_tokens=300)
    except Exception as err:  # unavailable: the rules decide
        log.info("skeptic review fell back (%s)", type(err).__name__)
        return None
    return parse_review(_clean_reply(text), model or meterless.model_name("deep"))


def parse_review(text: str, model: str) -> Optional[dict]:
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    try:
        out = json.loads(m.group(0)) if m else None
    except ValueError:
        return None
    if not isinstance(out, dict) or out.get("decision") not in ("approve", "reject"):
        return None
    lines = lambda k: [" ".join(str(x).split())[:160] for x in (out.get(k) or []) if str(x).strip()][:3]  # noqa: E731
    if not isinstance(out.get("reasons", []), list) or not isinstance(out.get("caveats", []), list):
        return None
    return {"decision": out["decision"], "reasons": lines("reasons"), "caveats": lines("caveats"), "model": model}


def decide_low_offer(finding: Finding, match: Mapping[str, bool], trust_verdict: Optional[str],
                     labels: Optional[Mapping[str, str]], caveats: Sequence[str], review: Optional[dict]) -> RuleResult:
    """An unusually low offer: the hard rules first (used/open box/refurbished, marketplace seller, installment, different
    model or colour, store safety; under 50% never gets here). A hard-rule failure always rejects, whatever the model said,
    and says so when the model had approved. Otherwise the deep lane's decision stands, with its reasons and caveats; without
    a usable answer the rules decide and say "decided by rules (model unavailable)"."""
    rule = check_best_price(finding, match, trust_verdict, labels, caveats)
    if not rule.passed:
        if review and review["decision"] == "approve":
            return RuleResult(False, "rejected", f"{rule.reason} ({review['model']} approved it, but a hard rule failed, so "
                                                 "the rule decides)", decided_by="rules (hard rule overrides the model)")
        return rule
    if review is None:
        return RuleResult(True, "confirmed", f"{rule.reason}; decided by rules (model unavailable)", rule.caveat,
                          decided_by="rules (model unavailable)")
    why = "; ".join(review["reasons"]) or ("approved" if review["decision"] == "approve" else "rejected")
    if review["decision"] == "reject":
        return RuleResult(False, "rejected", why, decided_by=review["model"])
    extra = "; ".join(merge_caveats(caveats, review["caveats"]))
    return RuleResult(True, "confirmed", why, extra, decided_by=review["model"])


CAVEAT_TOPICS = {"warranty": r"warrant", "colour": r"colou?r", "condition": r"\bnew\b|sealed|open box|refurb",
                 "price": r"price|cheap|low"}
STORE_CHECK_RE = re.compile(r"return|refund|contact|customer service|https|domain|registered|scam|legit|trust|verif",
                            re.I)  # the Trust Agent's store checks: shown with their own proof, never second-guessed here


def _topic(text: str) -> str:
    low = text.lower()
    return next((t for t, rx in CAVEAT_TOPICS.items() if re.search(rx, low)), low.strip(" .")[:40])


def merge_caveats(rule: Sequence[str], model: Sequence[str]) -> list[str]:
    """The rules' caveats first, then the model's on a topic not covered yet (one warranty caveat, one colour caveat...).
    A model caveat about the store checks (returns, contact, HTTPS, domain age, "not verified") is dropped: the Trust
    Agent's checks decide those and show their proof, so a caveat must never contradict them."""
    out, seen = [], set()
    for c in [*(c for c in rule if c), *(c for c in model if c and not STORE_CHECK_RE.search(c))]:
        t = _topic(c)
        if t not in seen:
            seen.add(t)
            out.append(c.strip().rstrip("."))
    return out
