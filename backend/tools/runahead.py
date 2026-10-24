"""Hidden-browser run-ahead for Store Lab pages: walks checkout to the last step before payment and records every fee.

SAFETY: the run-ahead never pays. Before every click, `is_denied` checks the element's text and attributes against a deny-list.
On a checkout page anything that looks like pay / place order / confirm / submit / complete purchase / buy now is refused.
It only clicks "add to cart / proceed / continue / next" controls, never types, and only runs on Store Lab pages (is_storelab).
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

from backend.contracts import PageFacts
from backend.llm import meterless
from backend.tools.browser import chromium
from backend.tools.common import RULES, ToolResult, finding, is_storelab, money

ROOT = Path(__file__).resolve().parents[2]
TRACES = ROOT / "traces"
MAX_PAGES = 8
NAV_TIMEOUT_MS = 6000

# Everything that could spend money or commit an order. Deliberately broad: "pay" also blocks "payment" and "PayPal".
DENY_RE = re.compile(r"pay|place order|confirm|submit|complete purchase|buy now", re.I)
# Off checkout (the product page) we still never press these.
DENY_ALWAYS_RE = re.compile(r"place order|complete purchase|buy now", re.I)
# Attributes the guard reads. Not `style`, so "display:block" doesn't block a harmless Continue link.
GUARD_ATTRS = ("id", "name", "class", "value", "aria-label", "title", "href", "type", "role", "formaction", "action")
CHECKOUT_URL_RE = re.compile(r"cart|basket|bag|checkout|payment|order", re.I)


def _norm(text: Optional[str]) -> str:
    """Collapse spaces and turn place-order / place_order / placeOrder into "place order" so attributes match too."""
    t = re.sub(r"([a-z])([A-Z])", r"\1 \2", text or "")
    return re.sub(r"[\s_\-]+", " ", t).strip()


def guarded_strings(text: Optional[str], attrs: Optional[Mapping[str, Optional[str]]] = None) -> list[str]:
    """The text and attribute values the guard looks at for one element."""
    out = [_norm(text)]
    for key, value in (attrs or {}).items():
        k = key.lower()
        if value and (k in GUARD_ATTRS or k.startswith("data-")):
            out.append(_norm(value))
    return [s for s in out if s]


def is_denied(text: Optional[str], attrs: Optional[Mapping[str, Optional[str]]] = None, on_checkout: bool = True) -> bool:
    """True when the run-ahead must NOT click this element."""
    regex = DENY_RE if on_checkout else DENY_ALWAYS_RE
    return any(regex.search(s) for s in guarded_strings(text, attrs))


def looks_like_checkout(url: str) -> bool:
    """Cart and checkout pages, by URL. The walker also treats every page after "Add to cart" as checkout."""
    return bool(CHECKOUT_URL_RE.search(url.split("?", 1)[0].split("#", 1)[0].rsplit("/", 1)[-1] or url))


# ----------------------------------------------------------------------------- walking the checkout (Store Lab only)

NEXT_RE = re.compile(r"add to (cart|bag|basket)|proceed|checkout|continue|next|review", re.I)
CONTROLS = "a[href], button, input[type=button], input[type=submit], [role=button]"
CONTROLS_JS = """(sel) => [...document.querySelectorAll(sel)].map((el, i) => {
  const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
  const attrs = {};
  for (const a of el.attributes) attrs[a.name] = a.value;
  return { i, text: (el.innerText || el.value || '').replace(/\\s+/g, ' ').trim().slice(0, 80), attrs,
           visible: r.width > 1 && r.height > 1 && cs.visibility !== 'hidden' && cs.display !== 'none' && !el.disabled };
})"""
LINES_JS = """() => {
  const PR = /[$€£]\\s?\\d|\\bfree\\b/i;
  const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 1 && r.height > 1; };
  let rows = [...document.querySelectorAll('[data-line],[data-total]')];
  if (!rows.length) {
    const cand = new Set();
    document.querySelectorAll('[class*=summary],[id*=summary],[class*=total],[id*=total],[class*=order],[class*=cart],table,aside')
      .forEach((s) => s.querySelectorAll('tr,li,dl>div,[class*=line],[class*=row],[class*=fee]').forEach((e) => cand.add(e)));
    rows = [...cand].filter((e) => ![...cand].some((o) => o !== e && e.contains(o)));
  }
  return rows.filter(vis).map((e) => ({ text: (e.innerText || '').replace(/\\s+/g, ' ').trim(), total: e.hasAttribute('data-total') }))
    .filter((r) => r.text.length < 140 && PR.test(r.text));
}"""

TOTAL_RE = re.compile(r"^(order |grand |estimated |final )?total|amount due|you pay|to pay", re.I)
SUBTOTAL_RE = re.compile(r"sub-?\s?total", re.I)
TAX_RE = re.compile(r"\b(tax|vat|gst)\b", re.I)
SHIP_RE = re.compile(r"ship|deliver|postage", re.I)
DISCOUNT_RE = re.compile(r"discount|coupon|promo|saving", re.I)
ADDON_RE = re.compile(r"protect|warrant|insur|plan\b|cover|add[- ]?on|gift wrap", re.I)
FEE_RE = re.compile(r"fee|charge|\bchg\b|surcharge|levy|service|svc|processing|\bproc\b|handling|convenience|admin|booking|platform", re.I)
FEE_NAMES = [(r"service|svc", "Service fee"), (r"processing|\bproc\b|card", "Processing fee"), (r"convenience|conv\b", "Convenience fee"),
             (r"handling|hdlg", "Handling fee"), (r"booking", "Booking fee"), (r"platform", "Platform fee"),
             (r"admin", "Admin fee"), (r"small order", "Small order fee"), (r"resort|facility", "Facility fee")]


@dataclass
class Line:
    label: str
    amount: float
    step: int
    kind: str = "item"  # item | addon | fee | shipping | tax | discount | subtotal | total


@dataclass
class Walk:
    lines: dict[str, Line] = field(default_factory=dict)
    steps: list[dict] = field(default_factory=list)
    final_total: Optional[float] = None
    stopped: str = ""
    refused: list[str] = field(default_factory=list)


def line_amount(text: str) -> Optional[float]:
    from backend.tools.common import parse_money
    v = parse_money(text)
    if v is None and re.search(r"\bfree\b", text, re.I):
        return 0.0
    return v


def line_label(text: str) -> str:
    t = re.sub(r"(-|−)?\s?[$€£₹]\s?[\d,]+(\.\d{1,2})?|\bfree\b", " ", text, flags=re.I)
    return re.sub(r"\s+", " ", t).strip(" :·-–") or "Charge"


def classify(label: str, amount: float, is_total: bool, first_page: bool) -> str:
    if is_total or TOTAL_RE.search(label):
        return "total"
    for kind, rx in (("subtotal", SUBTOTAL_RE), ("tax", TAX_RE), ("shipping", SHIP_RE)):
        if rx.search(label):
            return kind
    if amount < 0 or DISCOUNT_RE.search(label):
        return "discount"
    if ADDON_RE.search(label):
        return "addon"
    if FEE_RE.search(label):
        return "fee"
    return "item" if first_page else "fee"  # an unexplained cost that shows up late is treated as a fee


def rule_fee_name(label: str) -> Optional[str]:
    for rx, name in FEE_NAMES:
        if re.search(rx, label, re.I):
            return name
    return None


async def fee_name(label: str) -> tuple[str, bool]:
    """Plain name for a fee line. Rules first; the Nemotron fast lane only for labels the rules can't read. -> (name, used_model)"""
    name = rule_fee_name(label)
    if name:
        return name, False
    prompt = ("A checkout page shows this charge label. Reply with a short plain-English fee name (at most 4 words, "
              f"ending in 'fee'), nothing else.\nLabel: {label[:80]}")
    reply = await asyncio.to_thread(meterless.complete, prompt, "fast")
    reply = reply.strip().strip('."\'')
    if 2 < len(reply) <= 40 and re.fullmatch(r"[A-Za-z][A-Za-z \-]*", reply):
        return reply[0].upper() + reply[1:], True
    return label[:40], False


async def _step_number(page, count: int) -> int:
    raw = await page.evaluate("() => document.body && document.body.dataset.step")
    return int(raw) if raw and str(raw).isdigit() else count


def choose_next(controls: list[dict], on_checkout: bool) -> tuple[Optional[dict], list[str]]:
    """The first visible forward control the guard allows, plus the forward-looking controls it refused."""
    refused, allowed = [], []
    for c in controls:
        if not c["visible"]:
            continue
        forward = NEXT_RE.search(c["text"]) or DENY_RE.search(c["text"])
        if is_denied(c["text"], c["attrs"], on_checkout):
            if forward:
                refused.append(c["text"] or c["attrs"].get("id", "?"))
            continue
        if NEXT_RE.search(c["text"]):
            allowed.append(c)
    priority = (r"add to (cart|bag|basket)", r"proceed|checkout", r"continue|next|review")
    for rx in priority:
        for c in allowed:
            if re.search(rx, c["text"], re.I):
                return c, refused
    return None, refused


async def walk_checkout(url: str, check_id: str, max_pages: int = MAX_PAGES) -> Walk:
    """Adds the item to the cart and clicks forward until the guard refuses everything (the payment step). Store Lab only."""
    if not is_storelab(url):
        raise PermissionError("run-ahead only runs on Store Lab pages")
    walk = Walk()
    out_dir = TRACES / (re.sub(r"[^A-Za-z0-9_-]", "", check_id) or "check")
    async with chromium() as browser:
        ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()
        page.set_default_timeout(NAV_TIMEOUT_MS)
        await page.goto(url, wait_until="load")
        on_checkout, count = looks_like_checkout(url), 0
        for _ in range(max_pages):
            if on_checkout:
                count += 1
                await _record(page, walk, await _step_number(page, count), out_dir)
            controls = await page.evaluate(CONTROLS_JS, CONTROLS)
            nxt, refused = choose_next(controls, on_checkout)
            walk.refused += refused
            if not nxt:
                walk.stopped = f"stopped before payment: refused \"{refused[0]}\"" if refused else "no next step"
                break
            if is_denied(nxt["text"], nxt["attrs"], on_checkout):  # belt and braces: re-check the exact element
                raise RuntimeError(f"guard refused {nxt['text']!r}")
            before = page.url
            await page.locator(CONTROLS).nth(nxt["i"]).click()
            await page.wait_for_load_state("load")
            await page.wait_for_timeout(50)  # let the step's own script fill in the order summary
            if page.url == before:
                walk.stopped = f"\"{nxt['text']}\" didn't lead anywhere"
                break
            on_checkout = True
        else:
            walk.stopped = f"stopped after {max_pages} pages"
        if walk.steps and not walk.steps[-1]["screenshot"]:  # the evidence always shows the last step reached
            walk.steps[-1]["screenshot"] = await _shot(page, out_dir, walk.steps[-1]["step"])
        await ctx.close()
    return walk


async def _shot(page, out_dir: Path, step: int) -> str:
    out_dir.mkdir(parents=True, exist_ok=True)
    shot = out_dir / f"runahead-step{step}.png"
    await page.screenshot(path=str(shot))
    return f"/traces/{out_dir.name}/{shot.name}"


async def _record(page, walk: Walk, step: int, out_dir: Path) -> None:
    """Reads the order summary. Screenshots only the steps where a new charge appears (plus the last step, in walk_checkout)."""
    walk.steps.append({"step": step, "url": page.url, "title": await page.title(), "screenshot": None})
    first_page = len(walk.steps) == 1
    new_line = False
    for row in await page.evaluate(LINES_JS):
        amount = line_amount(row["text"])
        if amount is None:
            continue
        label = line_label(row["text"])
        kind = classify(label, amount, row["total"], first_page)
        if kind == "total":
            walk.final_total = amount
            continue
        key = re.sub(r"[^a-z0-9]", "", label.lower())
        if key not in walk.lines:
            walk.lines[key] = Line(label=label, amount=amount, step=max(1, step), kind=kind)
            new_line = True
    if new_line:
        walk.steps[-1]["screenshot"] = await _shot(page, out_dir, step)


# ----------------------------------------------------------------------------- the tool

async def check_runahead(check_id: str, url: str, page: PageFacts) -> ToolResult:
    if not is_storelab(url):
        return visible_fees(check_id, page)  # real store: read only, never open the site from here
    walk = await walk_checkout(url, check_id)
    out = ToolResult(evidence_data={"runahead": {"steps": walk.steps, "stopped": walk.stopped}})
    if not walk.steps:
        return out  # no checkout to walk (e.g. Store C): nothing to report
    lines = list(walk.lines.values())
    fees = [l for l in lines if l.kind == "fee" and l.amount > 0]
    used_model = False
    for l in fees:
        l.label, used = await fee_name(l.label)
        used_model |= used
    items = [l for l in lines if l.kind == "item"]
    listed = page.product.price if page.product.price is not None else (items[0].amount if items else None)
    real_total = walk.final_total if walk.final_total is not None else round(sum(l.amount for l in lines if l.kind != "subtotal"), 2)
    table = [{"label": "Item" if l.kind == "item" and len(items) == 1 else l.label, "amount": l.amount, "step": l.step}
             for l in lines if l.kind in {"item", "addon", "fee", "tax", "discount"} or (l.kind == "shipping" and l.amount)]
    out.evidence_data.update({"fees": table, "total": real_total})
    if not fees or listed is None or real_total <= listed + 0.005:
        return out
    total_fees = round(sum(l.amount for l in fees), 2)
    steps = sorted({l.step for l in fees})
    where = f"only at checkout step {steps[0]}" if len(steps) == 1 else f"during checkout (steps {', '.join(map(str, steps))})"
    parts = " + ".join(f"{l.label.lower().replace(' fee', '')} {money(l.amount)}" for l in fees)
    if len(fees) == 1:
        summary = f"A {fees[0].label[0].lower() + fees[0].label[1:]} of {money(total_fees)} appears {where}. Real total: {money(real_total)}."
    else:
        summary = f"{money(total_fees)} in fees appear {where} ({parts}). Real total: {money(real_total)}."
    last = walk.steps[-1]
    evidence = [{"kind": "screenshot", "ref": last["screenshot"], "label": f"Checkout step {last['step']}"},
                {"kind": "log", "ref": f"runahead:{check_id}", "label": f"{len(walk.steps)} checkout pages walked; {walk.stopped}"},
                {"kind": "text", "ref": " · ".join(f"{r['label']} {money(r['amount'])} (step {r['step']})" for r in table)
                 + f" · Real total {money(real_total)}"}]
    model = f"{RULES} + {meterless.model_name('fast')} (fee labels)" if used_model else "hidden browser run-ahead + rules"
    out.findings.append(finding(check_id, "drip_fees", "confirmed", summary, evidence, confidence=0.97, model=model))
    return out


def visible_fees(check_id: str, page: PageFacts) -> ToolResult:
    """Real pages: fees the extension saw on the page the user is looking at, grouped into one finding."""
    out = ToolResult()
    fees, seen = [], set()
    for s in page.signals:
        if s.type == "drip_fees" and s.amount and (s.selector or s.text) not in seen:
            seen.add(s.selector or s.text)
            fees.append(s)
    if not fees:
        return out
    total_fees = round(sum(s.amount for s in fees), 2)
    names = ", ".join((rule_fee_name(s.text) or "fee").lower() for s in fees)
    p = page.product.price
    base = f" on top of {money(p)}" if p is not None else ""
    ev = [{"kind": "selector", "ref": s.selector, "label": "Extra fees" if i == 0 else "", "placement": "right"}
          for i, s in enumerate(fees) if s.selector]
    out.findings.append(finding(check_id, "drip_fees", "confirmed", f"{money(total_fees)} in extra fees{base} ({names}).", ev, confidence=0.8))
    table = [{"label": "Item", "amount": p, "step": None}] if p is not None else []
    table += [{"label": " ".join(s.text.split())[:60], "amount": s.amount, "step": None} for s in fees]
    out.evidence_data.update({"fees": table, "total": round(sum(r["amount"] for r in table), 2)})
    return out
