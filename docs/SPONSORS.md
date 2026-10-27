# Sponsor technology

**Pricise** · Open Agent Hackathon 2026 · Track 3: Reasoning Architecture

Pricise is a Chrome extension. On any product page, three agents (Trust, Deal, Price) answer "Shop safe? Honest deal? Best safe price?". A Skeptic agent then challenges their findings before the shopper sees them. Every sponsor technology below does real work in the submitted build; none is only listed as a dependency.

## Zetaris: the data layer

- **Three sources, registered in Zetaris and queried with SQL through its REST API:**
  1. Pricise's Postgres, as the Lightning datasource `pricise_pg`. It holds stores, listings, cached shopping offers, the price observations saved on every real page visit, findings and the wishlist.
  2. Public RDAP domain records for six retailers (amazon.com, woot.com, walmart.com, bestbuy.com, target.com, jbl.com), as Lightning REST tables.
  3. A Google Shopping search through SerpApi, as a Lightning REST table. The API key is held only inside Zetaris and never in the repository.
- **A Unified Semantic Layer, `pricise_usl`.** Its `safe_price` table joins all three sources into one row per offer: price, condition, seller, warranty note, the store's domain age from RDAP, Pricise's own observed minimum and median prices, and `is_safe`.
- **In the product:** the Price Agent reads offers through `safe_price`. The evidence page shows the exact USL query that produced each comparison. If Zetaris doesn't answer within 15 seconds, the agent falls back to its own search and says so on the evidence page.
- **Data quality:** the rule `new_full_price` (`price > 0 AND item_condition = 'new' AND full_price = 1`) flagged 79 offers in the latest run, offers that weren't new or weren't a full price, such as pre-owned listings from eBay and resale stores.
- **Effect:** about 218 lines of hand-written Python (28 for domain age, 190 for safe price) do the same work as 91 lines of SQL plus one data-quality rule. The Python stays only as the fallback. Details: `docs/zetaris/USL.md`.

## Meterless: the AI runtime and engines

- **Meterless Nightshift is Pricise's primary and only AI provider.**
  - Every model call goes through its local runtime, from a single client file (`backend/llm/meterless.py`), so no agent calls a model directly.
  - Models are pinned by name.
  - The NVIDIA key is stored in Nightshift's Providers tab, not in our code or `.env`.
- **Meterless engines in working features:**

| Engine | Feature it powers | Test result |
|---|---|---|
| **Scout Intent** (`backend/engines/scout.py`) | Screens untrusted store-page text before every Nemotron call (chat, Skeptic, fee labels) and drops instructions hidden in it. With the orchestrator on, it also cleans page text before any agent reads it. | `test_scout_guard.py` 54 passed, `test_scout_sidecar.py` 7 passed |
| **World Model** (`backend/engines/world_model.py` + sidecar) | A shared, versioned store of findings that the orchestrator and agents read and write during a check | `test_world_model_dedupe.py` 13 passed, `test_orchestrator.py` 25 passed |

H-MEM and Markovian are implemented and tested, but they don't run in the product yet, so we don't claim them as features.

## NVIDIA: the models

- **Nemotron 3 Ultra 550B** (`nvidia/nemotron-3-ultra-550b-a55b`, on Nightshift's Judge lane) is the Skeptic.
  - For offers marked "unusually low, needs review", it makes the approve or reject decision, returning JSON with its reasons and caveats.
  - Example: on the demo page it approved Woot's $79.95 offer, with the caveat that Woot gives a 90-day warranty instead of JBL's manufacturer warranty.
- **Nemotron 3.5 Lightning 30B** (`nvidia/nemotron-3.5-lightning-30b-a3b`, on Nightshift's Verify lane) answers shopper questions from the page text and reads messy fee labels.
- The agent trace records which model made every decision, or "rules" when no model was involved.

## Cursor

- **Raj and Abel** used Cursor to build the Trust Agent and to connect Pricise to NVIDIA Nemotron's two models, Lightning and Ultra, through Meterless Nightshift. They found Cursor easy to use, even when building a complex multi-agent product like this.
- **Shriganesh** built the extension, the Deal and Price agents and the integration with Claude Code in VS Code.

## What was unique

1. **Guarded judgement.** Nemotron Ultra makes the borderline calls, but hard rules sit underneath. The model can never approve a used, open-box, marketplace, installment or look-alike offer. If it tries, the rule wins and the trace says so. If Nightshift is unavailable, the rules decide and say "decided by rules (model unavailable)".
2. **A USL that knows safety, not just price.** Zetaris joins live offers with Pricise's own price history and RDAP domain age, so "cheapest" and "safe" are answered by the same query, and a data-quality rule keeps pre-owned offers out.
3. **Store pages are untrusted input.** Scout Intent removes instructions hidden in page text before any agent or model reads it. On real stores Pricise is read-only: it never clicks, adds to cart or buys.
