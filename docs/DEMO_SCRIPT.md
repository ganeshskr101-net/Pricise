# Pricise demo script, about 3:30 (limit 4:00)

**File name:** `OAH2026-Pricise-ReasoningArchitecture-Demo.mp4`. Record at 1080p, 16:9, with your microphone on.

## Before you record
- Run `scripts/run.sh --real` with Docker and Nightshift running, then do one test check on the Amazon page so everything is warm.
- **Personal info:**
  - Change Amazon's delivery ZIP to one that isn't yours, or blur it when you edit.
  - Hide your Chrome profile picture, or use a clean Chrome profile.
  - Close `.env` and clear Terminal (`clear`).
- **Screen:**
  - Chrome zoom at 110–125%.
  - Bookmarks bar hidden, extra tabs closed.
  - Terminal font made bigger.
- **Open these tabs, in this order:**
  1. The README on GitHub
  2. The architecture diagram
  3. The Amazon page, https://www.amazon.com/dp/B09GJZKQ3K
  4. The Woot page
  5. Zetaris at localhost:3000, with the safe_price query typed in
  6. Nightshift on the Lanes screen
  7. VS Code with the newest `logs/` trace file open

## Script

| Time | On screen | Say |
|---|---|---|
| **0:00–0:20** Problem | README header, then the Amazon page | “Our entry is for **Reasoning Architecture**. Online shoppers can’t check, on every page, whether a store is safe, whether a discount is real, or whether a ‘cheaper’ offer is even the same item. This JBL speaker is $114.95 on Amazon, showing 12% off.” |
| **0:20–0:45** Solution | The quick card appearing on Amazon | “Pricise is a Chrome extension. It reads the page you’re on, read-only, and three agents answer: shop safe, honest deal, best safe price. Every claim links to proof. These are real store pages.” |
| **0:45–1:25** Agents working together | The architecture diagram, then the side panel | “Trust checks the store, Deal checks the discount, and Price searches credible stores. It isn’t a straight pipeline. The Price Agent found Woot at $79.95, 30% cheaper, and flagged it as unusually low, so it went to the **Skeptic**. The Skeptic runs on **NVIDIA Nemotron Ultra**. It approved Woot, but added an honest caveat: a 90-day Woot warranty, not JBL’s. Under the Skeptic are hard rules it can’t overrule: 72 offers were rejected for being used, open box, from marketplace sellers, from unknown stores or in another colour.” |
| **1:25–2:15** Live run | Refresh the Amazon tab; the side panel fills in. Point to: the **grey** “Discount: real vs launch price” outline, the Woot card with “fetched 3:36 AM today”, the Shop safe checks with Proof links. Ask the chat “Is this waterproof?” | “Here’s a real run. The discount is real against JBL’s $129.95 launch price, so it’s outlined neutrally, not as a trick. Amazon is safe: HTTPS and a domain registered 32 years ago. Pricise also says this listing is sold by a third-party seller, not Amazon itself. And the chat answers from the page: it’s waterproof.” |
| **2:15–2:45** Sponsor technology | Evidence page, Price comparison tab, the “(USL safe_price)” query opened; then Zetaris showing the same query; then Nightshift Lanes; then the trace file | “**Zetaris** is our data layer. One USL table, safe_price, joins our Postgres price history, RDAP domain records and Google Shopping, and the Price Agent reads through it. Here’s the query, and a data-quality rule that flagged 79 offers that weren’t new at a full price. Every model call runs through **Meterless Nightshift**: Ultra on the judge lane, Lightning for chat. The **Scout Intent** engine screens page text before any model sees it, and the **World Model** engine holds the shared findings. The trace shows which model made each decision.” |
| **2:45–3:30** Output and impact | Click **Go to Woot** and show the Woot listing at $79.95; end on the side panel | “The result: the same speaker, new, $35 cheaper from a credible store, with the trade-off spelled out. And Pricise never clicked or bought anything. It just told the shopper the truth before they paid. That’s Pricise. Thanks for watching.” |

## Checklist before uploading
- [ ] The video is 1 to 4 minutes long, and the track is named in the first 20 seconds.
- [ ] It shows at least two agents and the Skeptic changing the outcome.
- [ ] It shows Zetaris and Nightshift doing real work, plus the trace.
- [ ] No keys, tokens, `.env`, personal names, addresses or ZIP codes appear on screen.
- [ ] It plays start to finish with clear audio.
- [ ] It's uploaded to HackOS, or shared as a link that opens in a private window.
- [ ] A backup copy is saved somewhere else, and the link is in the README (`## Demo Video`).
