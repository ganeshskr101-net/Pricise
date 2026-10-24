# Pricise: project context (handoff from planning session, 23 Oct 2026)

## The competition
- **Open Agent Hackathon 2026** (GenAI Works), **Track 3: Reasoning Architecture**. Also reused for Challenge X (Gen AI Everyday Operations).
- Build window: Wed 21 Oct 8:00 PM ET to **Tue 27 Oct 7:45 PM ET** (23:45 UTC). Internal deadline: Tue 6:00 PM ET.
- Required: **Cursor** as dev environment; **Zetaris** and **Meterless** integrated (Nightshift as primary AI provider + at least one Meterless Engine in a working feature); **NVIDIA Nemotron** via Nightshift (needed for the DGX Spark award).
- Submit: 1-4 min demo video, slide deck, GitHub repo with README, sponsor-tech explanation. No committed secrets; declare pre-existing code.
- Scoring: Impact 30, Technical 20, Innovation 15, Demo 15, Product/UX 10, Sponsor tech 10. Ties go to Impact.
- Prizes (Track 3): 1st $2,000, 2nd $1,300, 3rd $1,000; NVIDIA Nemotron Award = DGX Spark. Split equally unless agreed otherwise.

## The product (one paragraph)
Pricise is a Chrome extension that checks any product page before you buy. It answers three questions:
**Shop safe?** (store trust), **Honest deal?** (fake discounts, resetting timers, fees revealed at a late checkout step, pre-ticked add-ons),
**Best safe price?** (the exact same item at a trusted store, true total after shipping and card offers). Every claim has proof.
Four agents (Trust, Deal, Price, Skeptic) check each other; nothing is bought without approval; personal data stays on the laptop.
It also keeps a wishlist and sends a desktop notification (laptop, not phone) when a genuine deal appears.

## Architecture
- Extension (Manifest V3): side panel + content script outlines + evidence page + wishlist page + chrome.notifications. Holds no keys.
- Local backend (FastAPI, http://localhost:8787): orchestrator + agents; SSE stream of findings; Playwright hidden browser for run-ahead (never pays).
- Zetaris (Docker, local): stores, products, listings, price_history (synthetic, labelled), card_offers, wishlist, purchases, findings.
- Meterless Nightshift: all model calls; fast Nemotron lane (agents) and deep lane (Skeptic). Engines: World Model (shared findings), H-MEM (preferences),
  Scout Intent (prompt-injection guard + approvals), Markovian (long watches, resume after sleep).
- Store Lab: 10 made-up stores (AudioDealz hero, Stores A-I) with switchable tricks. Never imitate a real brand.
- **Decision (23 Oct): Pricise must work on real store pages.** On real pages it reads only (no clicks, no run-ahead), uses careful
  evidence-backed wording, and marks anything it can't verify as unverified. Store Lab stays the scripted demo and the backup take.

## Demo numbers (must stay consistent)
- AudioDealz: listed $149, "was $249", "-40%". Real total $191.00 = 149 + 4.99 add-on + 22.00 service fee + 15.01 processing fee.
- Store A $239 + $13 shipping = $252. Store B $199 refurbished (rejected by Skeptic). Store C $255 - $26 card offer = **$229** (winner).
  Store D $189 scam (blocked by Trust). Store E outage. Later Store C drops to $235 - $26 = **$209** (wishlist alert, Day 9).
- Wishlist week: 340 price changes, 2 alerts, 11 tricks caught, 10 stores.

## Side panel, top to bottom (see docs/mockups/ui_panel.png)
1. Header: logo, "Pricise", "Checked this page in 6.2s", "4 agents" pill.
2. Verdict card: "Don't buy here" + "Risky store · 3 tricks · real total $191, not $149" + green "Safer choice: Store C · $229 [Go]".
3. Shop safe? (Risky): domain 6 days old · no address/company record · photos copied, each with a Proof link.
4. Honest deal? (3 tricks): fake 40% off [Chart] · timer resets [Replay] · $37 fees at step 4 [Fees]; buttons Untick $4.99 add-on, See all evidence.
5. Best safe price (Store C · $229): exact match, $255 - $26 card offer, skipped B and D; buttons Go to Store C, Watch price.
6. Agent chips: Trust, Deal, Price, Skeptic · 2. 7. Footer: "Analysed on this laptop · no personal data sent".
On the page: red dashed outlines with labels on the deal badge, the timer and the pre-ticked add-on; toolbar badge "3".

## Demo script (under 4 minutes)
0:00 fake deal page · 0:15 panel opens, outlines appear · 0:30 timer replay + real total · 1:00 fake-discount chart · 1:20 untrusted store ·
1:35 Skeptic rejects Store B, Trust blocks Store D, Store C wins · **preference flip** ("refurbished OK" -> Store B wins) ·
**hidden-instruction attack blocked** by Scout Intent · **shuffle button** (randomise tricks, still correct) · wishlist + desktop notification ·
checkout stops at payment for approval · proof screen (accuracy on 50 unseen pages, privacy, Nightshift log with Nemotron) · close.
Record a real run; label any speed-up; say stores are made up and histories synthetic.

## Schedule (gates)
Thu: setup + skeleton · Fri: one real check end to end through Nemotron · Sat: all four agents catch their tricks · Sun: full demo in UI ·
Mon: numbers final, fresh-install test, 10 rehearsals, freeze 8 PM · Tue: video, deck, submit by 6 PM ET.

## Working agreements
Stand-ups 10 AM and 9 PM ET. Branch per person, PRs to main, main always runs. Tag PRs [cursor]/[claude-code]/[codex].
Prompt logs in docs/prompts_log/<name>.md. Stubs first (AGENT_TRUST=stub|real etc.), swap to real agents one at a time.

## Open items
- Claude Code allowed (confirmed by Shriganesh). Shriganesh builds the UI in VS Code + Claude Code; everyone else in Cursor.
- Check "Pricise" name availability (Chrome Web Store, domains).
- Check which Nemotron models are in your NVIDIA catalog and pin them in Nightshift lanes.
