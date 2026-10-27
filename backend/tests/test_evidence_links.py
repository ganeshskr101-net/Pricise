"""Every link from the side panel lands on its own evidence tab and row, for a Store Lab check (AudioDealz) and a real store
check (Kindle): the panel's rowKey + the evidence page's resolve/matchRow, run with Node exactly as the extension runs them.
A link to a tab this page has no data for opens the nearest tab with a one-line note."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import stub_server
from backend.tests.test_evidence_page import online, _kindle  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="needs Node to import the extension's modules")

SCRIPT = """
import { sections, resolve, matchRow } from %(sections)s;
import { rowKey } from %(check)s;
const chk = %(chk)s;
const links = %(links)s;
const secs = sections(chk);
const available = secs.map((s) => s.id);
const text = (html) => html.replace(/<[^>]+>/g, ' ').replace(/\\s+/g, ' ').trim();
const out = links.map(([view, label]) => {
  const { tab, note, signals } = resolve(view, available);
  const html = secs.find((s) => s.id === tab).html;
  const rows = [...html.matchAll(/<(tr|div|li)[^>]*data-row="([^"]*)"[^>]*>([\\s\\S]*?)<\\/\\1>/g)];
  const i = matchRow(rows.map((r) => r[2]), label ? rowKey(label) : null);
  const sig = signals.map((t) => (html.match(new RegExp(`<tr[^>]*data-signal="${t}"[^>]*>([\\\\s\\\\S]*?)</tr>`)) || [])[1]).find(Boolean);
  const dflt = (html.match(/<(tr|div|ul)[^>]*data-row-default[^>]*>([\\s\\S]*?)<\\/\\1>/) || [])[2];
  return { view, tab, note, row: i >= 0 ? text(rows[i][3]) : label ? text(sig || dflt || '') : null };
});
console.log(JSON.stringify({ available, out }));
"""


def _land(chk, links):
    src = SCRIPT % {"sections": json.dumps((ROOT / "extension/evidence/sections.js").as_uri()),
                    "check": json.dumps((ROOT / "extension/shared/check.js").as_uri()), "chk": json.dumps(chk), "links": json.dumps(links)}
    out = subprocess.run([shutil.which("node"), "--input-type=module", "-e", src], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    res = json.loads(out.stdout)
    return res["available"], {(o["view"], o["tab"]): o for o in res["out"]}, res["out"]


def _panel_links(card):
    """What the panel links: every reason with a proof (Proof / Chart / Replay / Fees / Compare), plus See why and All evidence."""
    links = [[r["proof"], r["text"]] for part in ("safe", "honest", "best_price") for r in card[part]["reasons"] if r.get("proof")]
    return links + [["stores", None], ["overview", None]]


def test_audiodealz_links_land_on_their_own_tab_and_row(monkeypatch):
    monkeypatch.setattr(stub_server, "DEMO_DELAY", 0)
    client = TestClient(stub_server.app)
    cid = client.post("/check", json={"url": "http://localhost:8787/storelab/audiodealz/"}).json()["check_id"]
    with client.stream("GET", f"/check/{cid}/events") as r:
        list(r.iter_lines())
    chk = client.get(f"/check/{cid}").json()
    available, _, out = _land(chk, _panel_links(chk["final_card"]))
    assert available == ["overview", "page", "whois", "stores", "chart", "fees", "replay", "trace"]
    got = {o["row"] if o["row"] is None else o["row"][:40]: o["tab"] for o in out}
    by_label = {(o["view"]): o for o in out}
    rows = [(o["view"], o["tab"], o["note"], o["row"]) for o in out]
    # Shop safe? Proof -> Store checks, on the matching finding
    assert ("whois", "whois", None, "The domain was registered 6 days ago. proposed") in rows or \
        any(v == "whois" and t == "whois" and r.startswith("The domain was registered 6 days ago") for v, t, _, r in rows)
    assert any(v == "whois" and r.startswith("Product photos are copied from another site") for v, t, _, r in rows)
    # Honest deal? Chart / Replay / Fees -> their own tabs; Best safe price -> the chosen store
    assert {o["view"]: o["tab"] for o in out if o["view"] in ("chart", "replay", "fees", "stores")} == \
        {"chart": "chart", "replay": "replay", "fees": "fees", "stores": "stores"}
    assert by_label["replay"]["row"].startswith("Visit 1") and by_label["fees"]["row"].startswith("Real total")
    assert any(o["view"] == "stores" and o["row"] and o["row"].startswith("Store C") for o in out)
    assert by_label["overview"]["tab"] == "overview" and not any(o["note"] for o in out)
    assert got  # every link resolved


def test_kindle_links_land_on_their_own_tab_and_row(online):  # noqa: F811
    res = _kindle()
    checks = [{"check": "no_https", "result": "Secure connection (HTTPS).", "status": "ok", "evidence": []},
              {"check": "domain_age", "result": "Domain registered 32 years ago.", "status": "ok",
               "evidence": [{"kind": "url", "ref": "https://rdap.org/domain/amazon.com"}]},
              {"check": "store_records", "result": "Company record and reviews for www.amazon.com aren't checked.",
               "status": "unchecked", "evidence": []}]
    page = {"url": "https://www.amazon.com/dp/B0C8RR4WN3", "host": "www.amazon.com",
            "product": {"name": "Amazon Kindle Paperwhite Signature Edition 32GB", "price": 249.99},
            "signals": [{"type": "scarcity", "text": "Only 1 left in stock - order soon."},
                        {"type": "store_info", "text": "returns link: yes · contact link: yes"}]}
    card = {"title": "No tricks spotted", "subtitle": "0 tricks",
            "safe": {"reasons": [{"text": "Domain registered 32 years ago", "proof": "whois"},
                                 {"text": "Secure connection (HTTPS)", "proof": "whois"}]},
            "honest": {"reasons": [{"text": "Timer resets on every visit", "proof": "replay"},
                                   {"text": "$12 in extra fees", "proof": "fees"}]},
            "best_price": {"headline": "As good as it gets", "reasons": [{"text": "Credible stores: Best Buy $249.99", "proof": "stores"}]}}
    chk = {"url": page["url"], "page": page, "findings": [f.model_dump() for f in res.findings], "final_card": card,
           "evidence_data": {"fees": [{"label": "Item", "amount": 249.99}], "timer_frames": [], "price_history": None,
                             "stores": res.stores, "online": res.online, "store_checks": checks}}
    available, _, out = _land(chk, _panel_links(card))
    assert available == ["overview", "page", "whois", "stores", "chart", "trace"]
    whois = [o for o in out if o["view"] == "whois"]
    assert [(o["tab"], o["note"]) for o in whois] == [("whois", None), ("whois", None)]
    assert whois[0]["row"].startswith("Domain registered 32 years ago") and whois[1]["row"].startswith("Secure connection (HTTPS)")
    replay = next(o for o in out if o["view"] == "replay")
    assert (replay["tab"], replay["note"]) == ("page", "No timer on this page")
    fees = next(o for o in out if o["view"] == "fees")
    assert (fees["tab"], fees["note"]) == ("page", "No checkout fee table on this page")
    stores = [o for o in out if o["view"] == "stores"]
    assert all(o["tab"] == "stores" and o["note"] is None for o in stores) and stores[0]["row"].startswith("Best Buy")
    assert next(o for o in out if o["view"] == "overview")["tab"] == "overview"


def test_evidence_page_scrolls_after_render_and_marks_finding_rows():
    js = (ROOT / "extension/evidence/evidence.js").read_text()
    assert "requestAnimationFrame(() => requestAnimationFrame(() => focusRow(" in js  # after layout, not before load
    assert 'classList.add(\'flash\')' in js
    sections = (ROOT / "extension/evidence/sections.js").read_text()
    assert 'id="finding-${i}"' in sections
    panel = (ROOT / "extension/sidepanel/panel.js").read_text()
    assert "data-row=\"${esc(rowKey(r.text))}\"" in panel and "tab: el.dataset.view || 'overview'" in panel
