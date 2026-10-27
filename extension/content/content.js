// Pricise content script. Runs on every page, but only READS the page.
// It never clicks, submits or buys anything. The only change it makes is when the user presses "Untick" in the panel.
(() => {
  if (window.__pricise) return;
  window.__pricise = true;

  const MAX_PER_TYPE = 4;
  const PRICE_RE = /(?:US\s?)?([$€£₹])\s?(\d{1,3}(?:[,.\s]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)/;

  // Parts of a page Pricise never reads: cart sidebars, mini-carts and cart drawers (Amazon's "Subtotal" flyout, Shopify cart
  // drawers). Their prices and fees belong to the cart, not to the product on the page.
  const IGNORE_SEL = ['#nav-flyout-ewc', '#ewc-content', '[id^="ewc-"]', '.ewc-sidebar', '#sw-atc-details-single-container',
    '#attach-desktop-sideSheet', '[id*="mini-cart"]', '[class*="mini-cart"]', '[id*="minicart"]', '[class*="minicart"]',
    '[id*="cart-drawer"]', '[class*="cart-drawer"]', '[class*="side-cart"]', '[class*="cart-flyout"]'].join(',');
  // (Not every "subtotal" box: on a checkout page the fee rows Pricise must read often sit inside one.)
  const ignored = (el) => { try { return !!el?.closest?.(IGNORE_SEL); } catch (_) { return false; } };
  // Other items' cards: carousels, "similar items", sponsored and recommended rows. Their prices and "was" claims are
  // not about this item (Walmart's #grid-cell-N cards showed "current price $429.95, Was $563.70" on a $68.53 page).
  const OTHER_ITEMS_SEL = ['[id^="grid-cell-"]', '[class*="carousel"]', '[data-testid*="carousel"]',
    '[aria-roledescription="carousel"]', '[class*="recommend"]', '[data-testid*="recommend"]', '[class*="sponsored"]',
    '[data-testid*="sponsored"]', '[class*="similar-items"]', '[data-testid*="item-stack"]'].join(',');
  const otherItem = (el) => { try { return !!el?.closest?.(OTHER_ITEMS_SEL); } catch (_) { return false; } };
  // Payment-card and membership offers are not discounts on the item ("Prime Visa", reward points, "pay $0.00 at checkout",
  // Subscribe & Save, cash back): never read them as a discount claim.
  const PROMO_RE = /prime visa|\bvisa\b|mastercard|credit card|card ?member|rewards?( points?| card| visa)?\b.*\bpoints?\b|\bpoints\b|pay \$?0(\.00)? (at|upon)|\$0\.00 (at checkout|upon approval)|subscribe ?(&|and) ?save|cash ?back|% back\b|gift card|apply now|upon approval|trade-?in|with activation/i;

  // ---------------------------------------------------------------- helpers
  function parseAmount(text) {
    const m = (text || '').match(PRICE_RE);
    if (!m) return null;
    let n = m[2].replace(/\s/g, '');
    if (/,\d{1,2}$/.test(n) && !/\.\d/.test(n)) n = n.replace(/\./g, '').replace(',', '.'); // 1.299,00 -> 1299.00
    else n = n.replace(/,/g, '');
    const v = parseFloat(n);
    return Number.isFinite(v) ? v : null;
  }
  const currencyOf = (t) => ({ '$': 'USD', '€': 'EUR', '£': 'GBP', '₹': 'INR' }[(t || '').match(PRICE_RE)?.[1]] || null);

  function visible(el) {
    if (!el || !el.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const cs = getComputedStyle(el);
    return cs.visibility !== 'hidden' && cs.display !== 'none' && parseFloat(cs.opacity) > 0.05;
  }

  function cssPath(el) {
    if (!(el instanceof Element)) return null;
    if (el.id && /^[A-Za-z][\w-]*$/.test(el.id) && document.querySelectorAll('#' + el.id).length === 1) return '#' + el.id;
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && node !== document.body && parts.length < 8) {
      let part = node.tagName.toLowerCase();
      if (node.id && /^[A-Za-z][\w-]*$/.test(node.id)) { parts.unshift('#' + node.id); break; }
      const sibs = node.parentElement ? [...node.parentElement.children].filter((c) => c.tagName === node.tagName) : [];
      if (sibs.length > 1) part += `:nth-of-type(${sibs.indexOf(node) + 1})`;
      parts.unshift(part);
      node = node.parentElement;
    }
    if (!parts[0]?.startsWith('#')) parts.unshift('body');
    return parts.join(' > ');
  }

  // Walk visible text nodes and return the element that holds each match.
  function findText(regex, limit = MAX_PER_TYPE) {
    const out = [];
    const seen = new Set();
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => {
        const p = n.parentElement;
        if (!p || ['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE'].includes(p.tagName)) return NodeFilter.FILTER_REJECT;
        if (p.closest('.pricise-label') || ignored(p)) return NodeFilter.FILTER_REJECT;
        return regex.test(n.nodeValue) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
      },
    });
    let n;
    while ((n = walker.nextNode()) && out.length < limit) {
      const el = n.parentElement;
      if (seen.has(el) || !visible(el)) continue;
      seen.add(el);
      out.push(el);
    }
    return out;
  }

  // Use the smallest sensible "block" around a text node, so outlines frame the thing a person sees.
  function block(el) {
    let node = el;
    for (let i = 0; i < 3 && node.parentElement; i++) {
      const r = node.getBoundingClientRect();
      if (r.width > 120 && r.height > 18) break;
      node = node.parentElement;
    }
    return node;
  }

  // ---------------------------------------------------------------- product
  function readJsonLd() {
    const items = [];
    document.querySelectorAll('script[type="application/ld+json"]').forEach((s) => {
      try {
        const data = JSON.parse(s.textContent);
        const stack = Array.isArray(data) ? [...data] : [data];
        while (stack.length) {
          const d = stack.pop();
          if (!d || typeof d !== 'object') continue;
          if (Array.isArray(d['@graph'])) stack.push(...d['@graph']);
          const t = [].concat(d['@type'] || []);
          if (t.includes('Product') || t.includes('ProductGroup')) items.push(d);
        }
      } catch (_) { /* ignore broken JSON-LD */ }
    });
    const p = items[0];
    if (!p) return null;
    let offer = [].concat(p.offers || [])[0] || {};
    if (offer['@type'] === 'AggregateOffer') offer = { price: offer.lowPrice ?? offer.price, priceCurrency: offer.priceCurrency };
    const price = offer.price != null ? parseFloat(String(offer.price).replace(/,/g, '')) : null;
    return {
      name: p.name || null,
      price: Number.isFinite(price) ? price : null,
      currency: offer.priceCurrency || null,
      brand: typeof p.brand === 'string' ? p.brand : p.brand?.name || null,
      model: p.model || p.mpn || null,
      sku: p.sku || p.gtin13 || p.gtin12 || null,
      image: [].concat(p.image || [])[0]?.url || [].concat(p.image || [])[0] || null,
      source: 'json-ld',
    };
  }

  function meta(name) {
    return document.querySelector(`meta[property="${name}"],meta[name="${name}"],meta[itemprop="${name}"]`)?.content || null;
  }

  const BUY_BOX = ['#corePriceDisplay_desktop_feature_div', '#corePrice_feature_div', '#corePrice_desktop', '#apex_desktop',
    '#buybox', '#desktop_buybox'];
  function buyBoxPrice(which, extra = '') {
    const sel = BUY_BOX.map((b) => `${b} .a-price${which} .a-offscreen`).join(',') + (extra ? `,${extra}` : '');
    for (const el of document.querySelectorAll(sel)) {
      const t = (el.textContent || '').trim();
      const v = parseAmount(t);
      if (v != null && !ignored(el)) return { v, t };
    }
    return null;
  }

  function itemprop(name) {
    const el = document.querySelector(`[itemprop="${name}"]`);
    if (!el) return null;
    const v = el.getAttribute('content') || el.querySelector?.('[itemprop=name]')?.textContent || el.textContent;
    return (v || '').replace(/\s+/g, ' ').trim().slice(0, 80) || null;
  }

  function bylineBrand() {
    const t = (document.querySelector('#bylineInfo, [data-testid=brand-link], .product-brand, .product__vendor')?.innerText || '').trim();
    const m = t.match(/^(?:Brand:\s*|Visit the\s+)?(.+?)(?:\s+Store)?$/i);
    return m ? m[1].slice(0, 60) : null;
  }

  function urlSku() {
    const u = location.pathname + location.search;
    const m = u.match(/\/(?:dp|gp\/product)\/([A-Z0-9]{10})/) || u.match(/[?&]skuId=(\d+)/) || u.match(/\/(\d{6,8})\.p\b/)
      || u.match(/\/ip\/(?:[^/]+\/)?(\d{5,})/) || u.match(/\/A-(\d{5,})/) || u.match(/\/itm\/(?:[^/]+\/)?(\d{9,})/);
    return m ? m[1] : null;
  }

  // A spec row's value: "Model: WH-1000XM5", "Item model number WH1000XM5/B", "Brand: Sony", or a label and its value
  // in separate cells (<th>Model</th><td>…</td>, <dt>Brand</dt><dd>…</dd>).
  const SPEC = {
    model: /\b(?:item\s+)?model(?:\s+(?:number|no\.?|#))?\s*[:#]?\s+([A-Z0-9][A-Z0-9._\/-]{2,30})\b/i,
    brand: /\b[Bb]rand\s*[:#]?\s+([A-Z][\w&.'-]{1,30}(?:\s[A-Z][\w&.'-]{1,20})?)\b/,
  };
  function specValue(kind) {
    const RE = SPEC[kind];
    for (const el of findText(new RegExp(`\\b${kind}\\b`, 'i'), 6)) {
      const row = el.closest('tr, li, dl, div') || el;
      const m = (row.innerText || '').replace(/\s+/g, ' ').match(RE);
      if (m && (kind !== 'model' || /\d/.test(m[1]))) return m[1];
    }
    return null;
  }

  function readProduct() {
    const ld = readJsonLd() || {};
    const p = { ...ld };
    p.name = p.name || document.querySelector('#productTitle')?.textContent?.trim() || meta('og:title')
      || [...document.querySelectorAll('h1')].find((h) => !ignored(h))?.innerText?.trim() || document.title;
    // Shops without JSON-LD (or with a thin one): brand, model and SKU from meta, microdata, the URL or a spec row.
    p.brand = p.brand || meta('product:brand') || meta('og:brand') || itemprop('brand') || bylineBrand() || specValue('brand');
    p.sku = p.sku || meta('product:retailer_item_id') || itemprop('sku') || urlSku();
    p.model = p.model || itemprop('model') || itemprop('mpn') || specValue('model');
    if (p.price == null) {
      const m = meta('product:price:amount') || meta('og:price:amount') || document.querySelector('[itemprop=price]')?.getAttribute('content');
      if (m) { p.price = parseFloat(m); p.source = p.source || 'meta'; p.currency = p.currency || meta('product:price:currency'); }
    }
    if (p.price == null) {
      // Amazon's buy box: the screen-reader copy of the price keeps the cents ("$15.99"); the visible one is split in spans.
      const buy = buyBoxPrice(':not([data-a-strike])');
      if (buy) { p.price = buy.v; p.currency = currencyOf(buy.t); p.source = 'buy-box'; }
    }
    if (p.was_price == null) {
      const was = buyBoxPrice('[data-a-strike="true"]', BUY_BOX.map((b) => `${b} .basisPrice .a-offscreen`).join(','));
      if (was && (p.price == null || was.v > p.price)) p.was_price = was.v;
    }
    if (p.price == null) {
      // Visible fallback: the biggest price-looking text that is not struck through.
      let best = null;
      document.querySelectorAll(`[class*=price],[id*=price],[data-testid*=price],[itemprop=price],${PRICE_SELECTORS}`).forEach((el) => {
        if (!visible(el) || el.closest('s,del,strike,[data-a-strike="true"]') || ignored(el) || otherItem(el)) return;
        const t = el.innerText || '';
        const v = parseAmount(t);
        if (v == null) return;
        const size = parseFloat(getComputedStyle(el).fontSize) || 0;
        if (!best || size > best.size) best = { v, size, t };
      });
      if (best) { p.price = best.v; p.currency = currencyOf(best.t); p.source = 'visible'; }
    }
    if (p.was_price == null && p.price != null) {
      const was = struckNear(mainPriceEl(p.price), p.price);
      if (was != null) p.was_price = was;
    }
    p.name = (p.name || '').replace(/\s+/g, ' ').trim().slice(0, 160) || null;
    return p;
  }

  // The element showing this item's price: the largest visible one with that amount, outside other items' cards.
  function mainPriceEl(price) {
    if (price == null) return null;
    let best = null;
    document.querySelectorAll(`[class*=price],[id*=price],[data-testid*=price],[itemprop=price],${PRICE_SELECTORS}`).forEach((el) => {
      if (!visible(el) || el.closest('s,del,strike,[data-a-strike="true"]') || ignored(el) || otherItem(el)) return;
      const v = parseAmount(el.innerText || '');
      if (v == null || Math.abs(v - price) > 0.005) return;
      const size = parseFloat(getComputedStyle(el).fontSize) || 0;
      if (!best || size > best.size) best = { el, size };
    });
    return best?.el || null;
  }

  const STRUCK_SEL = 's,del,strike,[class*=strike],[class*=was-price],[class*=list-price],[class*=original-price],[data-a-strike="true"]';
  // The main price's block: the price element, its parent and grandparent ("Now $68.53 $77.00 You save $8.47"),
  // never the whole page.
  function priceBlocks(main) {
    const out = [];
    for (let node = main; node && out.length < 3 && node !== document.body && node !== document.documentElement; node = node.parentElement) out.push(node);
    return out;
  }
  const nearMainPrice = (el, main) => priceBlocks(main).some((node) => node.contains(el));
  // The struck-through price in the main price's block, when it is higher than today's price.
  function struckNear(main, price) {
    for (const node of priceBlocks(main)) {
      for (const el of node.querySelectorAll(STRUCK_SEL)) {
        const v = parseAmount(cleanText(el));
        if (visible(el) && !otherItem(el) && v != null && v > price + 0.005) return v;
      }
    }
    return null;
  }

  // ---------------------------------------------------------------- signals (what a careful shopper would notice)
  // Text as a person reads it: screen-reader duplicates (aria-hidden copies of a price) left out, so "$15.99 $15.99" or
  // "$37 . 99" never reaches a claim.
  // Text nodes are joined with a space where they would glue two words, or a word to a price ("USDNow", "$68.53You save");
  // a price split across nodes ("214" + ".99", "$" + "214") stays whole. Screen-reader boilerplate ("Current price is USD")
  // is dropped, and " · " separates a price from the words after it: "Now $68.53 · You save $8.47 · was $77.00".
  function cleanText(el) {
    const copy = el.cloneNode(true);
    copy.querySelectorAll('[aria-hidden="true"], script, style').forEach((n) => n.remove());
    let text = '';
    const walker = document.createTreeWalker(copy, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const t = n.nodeValue || '';
      if (text && /[A-Za-z0-9)]$/.test(text) && /^[A-Za-z(]/.test(t)) text += ' ';
      else if (text && /[A-Za-z]$/.test(text) && /^[$€£₹]/.test(t)) text += ' ';
      text += t;
    }
    return text.replace(/\s+/g, ' ').replace(/\$\s+(\d)/g, '$$$1')
      .replace(/\bcurrent price(?: is)?(?: [A-Z]{3}\b)?\s*/gi, '')
      .replace(/([$€£₹]\d[\d,]*(?:\.\d{1,2})?)\s+(?=[A-Za-z])/g, '$1 · ').trim();
  }

  function claimedDiscounts() {
    const out = [];
    const els = new Set();
    document.querySelectorAll('s,del,strike,[class*=strike],[class*=was-price],[class*=list-price],[class*=original-price],[class*=compare],[data-a-strike="true"],.basisPrice')
      .forEach((el) => { if (visible(el) && !ignored(el) && PRICE_RE.test(el.innerText || '')) els.add(el); });
    findText(/\b(was|list price|list:|typical price|reg\.?|regular|rrp|compare at)\b[^\n]{0,20}[$€£₹]\s?\d/i).forEach((el) => els.add(el));
    findText(/-\s?\d{1,2}\s?%|\b\d{1,2}\s?%\s?off\b|save\s+\d{1,2}\s?%/i).forEach((el) => els.add(el));
    // A shop with a buy box (Amazon): only claims inside it are about this item. Elsewhere on the page they belong to
    // sponsored or "compare with similar items" products.
    const boxes = [...document.querySelectorAll(BUY_BOX.join(','))].filter(visible);
    const price = readProduct().price;
    // Without a buy box: a claim is about this item when it sits in the main price's block, or its block shows no price
    // (a "-20%" badge) or this item's price. A block showing another current price belongs to another item.
    const main = boxes.length ? null : mainPriceEl(price);
    const aboutThisItem = (e) => {
      if (otherItem(e)) return false;
      if (!main || nearMainPrice(e, main)) return true;
      const amounts = (cleanText(block(e)).match(new RegExp(PRICE_RE.source, 'g')) || []).map(parseAmount).filter((v) => v != null);
      return !amounts.length || price == null || amounts.some((v) => Math.abs(v - price) < 0.005);
    };
    let cands = [...els].filter((e) => !PROMO_RE.test(cleanText(block(e)))
      && (boxes.length ? boxes.some((b) => b.contains(e)) : aboutThisItem(e)));
    cands = cands.filter((e) => {
      const amount = parseAmount(cleanText(e));
      return amount == null || price == null || amount > price + 0.005; // a list price at or below today's price is no discount
    });
    for (const el of cands.slice(0, MAX_PER_TYPE)) {
      const b = block(el);
      out.push({ type: 'claimed_discount', selector: cssPath(b), text: cleanText(b).slice(0, 100), amount: parseAmount(cleanText(el)) });
    }
    return dedupeByContainment(out);
  }

  const TIMER_RE = /\b(\d{1,2}):([0-5]\d)(?::([0-5]\d))?\b(?!\s?(am|pm))/i;
  function toSeconds(t) {
    const m = (t || '').match(TIMER_RE);
    if (!m) return null;
    return m[3] != null ? (+m[1]) * 3600 + (+m[2]) * 60 + (+m[3]) : (+m[1]) * 60 + (+m[2]);
  }

  async function countdowns() {
    const cands = findText(TIMER_RE, 12).filter((el) => (el.innerText || '').length < 60);
    if (!cands.length) return [];
    const first = cands.map((el) => toSeconds(el.innerText));
    await new Promise((r) => setTimeout(r, 1300));
    const out = [];
    const key = 'pricise:timer:' + location.host + location.pathname;
    const prev = (await chrome.storage.local.get(key))[key];
    cands.forEach((el, i) => {
      const now = toSeconds(el.innerText);
      if (now == null || first[i] == null || !(now < first[i])) return; // must be ticking down
      const b = block(el);
      const sig = { type: 'countdown', selector: cssPath(b), text: (b.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 60), detail: { remaining_s: now } };
      if (prev && out.length === 0) {
        const elapsed = (Date.now() - prev.at) / 1000;
        if (elapsed > 45 && now > prev.remaining - elapsed + 20) {
          sig.type = 'resetting_timer';
          sig.detail = { ...sig.detail, previous_s: prev.remaining, seconds_since_last_visit: Math.round(elapsed) };
        }
      }
      if (out.length < 2) out.push(sig);
    });
    if (out[0]) await chrome.storage.local.set({ [key]: { remaining: out[0].detail.remaining_s, at: Date.now() } });
    return out;
  }

  function precheckedAddons() {
    const out = [];
    const RE = /protect|warrant|insur|plan|cover|add[- ]?on|donat|tip|gift wrap|priority|express|membership|subscri|newsletter|offers/i;
    document.querySelectorAll('input[type=checkbox]').forEach((cb) => {
      if (!cb.checked || out.length >= MAX_PER_TYPE) return;
      const label = cb.closest('label') || (cb.id && document.querySelector(`label[for="${CSS.escape(cb.id)}"]`)) || cb.parentElement;
      const text = (label?.innerText || '').replace(/\s+/g, ' ').trim();
      if (!text || /remember me|keep me signed|stay signed|terms/i.test(text) || !RE.test(text)) return;
      const target = label && visible(label) ? label : cb;
      if (!visible(target)) return;
      out.push({ type: 'prechecked_addon', selector: cssPath(target), text: text.slice(0, 100), amount: parseAmount(text), detail: { checkbox: cssPath(cb) } });
    });
    return out;
  }

  function scarcity() {
    const RE = /only \d+ left|\d+ (people|others|shoppers) (are )?(viewing|looking|watching)|selling fast|almost gone|low stock|in \d+ (carts|baskets)|high demand|last chance|hurry/i;
    return findText(RE).map((el) => ({ type: 'scarcity', selector: cssPath(block(el)), text: (el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 100) }));
  }

  function dripFees() {
    const RE = /\b(service|processing|convenience|booking|handling|resort|facility|admin(istration)?|platform|order|small order|payment)\s+fee/i;
    const out = [];
    findText(RE, 8).forEach((el) => {
      let row = el;
      for (let i = 0; i < 3 && row.parentElement && parseAmount(row.innerText) == null; i++) row = row.parentElement;
      const amt = parseAmount(row.innerText);
      if (amt == null || amt <= 0 || (row.innerText || '').length > 160) return;
      out.push({ type: 'drip_fees', selector: cssPath(row), text: (row.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 100), amount: amt });
    });
    return out.slice(0, MAX_PER_TYPE);
  }

  function dedupeByContainment(list) {
    const els = list.map((s) => { try { return document.querySelector(s.selector); } catch (_) { return null; } });
    return list.filter((s, i) => !els.some((other, j) => j !== i && other && els[i] && other !== els[i] && other.contains(els[i]) && list[j].type === s.type));
  }

  // A "Price history" block the page itself shows (a chart table or list of date + price). Read only; at least 5 points.
  const MONTHS = { jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6, jul: 7, aug: 8, sep: 9, oct: 10, nov: 11, dec: 12 };
  function isoDate(s) {
    let m = s.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (m) return `${m[1]}-${m[2]}-${m[3]}`;
    m = s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{2,4})$/);
    if (m) return `${m[3].length === 2 ? '20' + m[3] : m[3]}-${m[1].padStart(2, '0')}-${m[2].padStart(2, '0')}`;
    m = s.match(/^([A-Za-z]{3})[a-z]*\.? (\d{1,2}),? (\d{4})$/);
    if (m && MONTHS[m[1].toLowerCase()]) return `${m[3]}-${String(MONTHS[m[1].toLowerCase()]).padStart(2, '0')}-${m[2].padStart(2, '0')}`;
    return null;
  }
  function pageHistory() {
    const head = findText(/^\s*price history\b/i, 3)[0];
    if (!head) return [];
    const box = head.closest('section, table, [class*=history], [id*=history]') || head.parentElement?.parentElement || head;
    const RE = /(\d{4}-\d{2}-\d{2}|\d{1,2}\/\d{1,2}\/\d{2,4}|[A-Z][a-z]{2,8}\.? \d{1,2},? \d{4})[^$\d]{0,24}\$\s?(\d{1,5}(?:\.\d{2})?)/g;
    const points = [];
    for (const m of cleanText(box).matchAll(RE)) {
      const d = isoDate(m[1]);
      if (d) points.push([d, parseFloat(m[2])]);
    }
    return points.length >= 5 ? [{ type: 'price_history', selector: cssPath(box), text: `Price history on the page: ${points.length} points`,
      detail: { points: points.slice(0, 400) } }] : [];
  }

  // A short product summary for the chat (read only): title, "About this item" bullets, description and spec tables, up to
  // ~4,000 characters. Reviews (and the names in them) are never included; cart sidebars are ignored like everywhere else.
  const PAGE_TEXT_MAX = 4000;
  const REVIEWS_SEL = '[id*=review i], [class*=review i], [data-hook*=review i], #customerReviews, [id*=rating i], [class*=rating i]';
  const PAGE_TEXT_SEL = ['#productTitle', '#feature-bullets', '#productOverview_feature_div', '#productDescription',
    '#productDetails_techSpec_section_1', '#productDetails_detailBullets_sections1', '#detailBullets_feature_div', '#aplus',
    '[itemprop=description]', '.product-description', '.product__description', '[class*=product-description]',
    '[class*=specifications]', '[class*=spec-table]', '[id*=specifications]', '[data-testid*=specifications]',
    '[class*=product-details]', '[id*=product-details]', '[class*=features]'].join(',');
  function pageText() {
    const seen = new Set();
    const parts = [];
    const add = (s) => { const v = (s || '').replace(/\s+/g, ' ').trim(); if (v && !seen.has(v)) { seen.add(v); parts.push(v); } };
    add(document.querySelector('#productTitle')?.textContent || document.querySelector('h1')?.innerText);
    document.querySelectorAll(PAGE_TEXT_SEL).forEach((el) => {
      if (ignored(el) || el.closest(REVIEWS_SEL) || [...seen].some((s) => s.length > 200 && s.includes((el.innerText || '').slice(0, 80)))) return;
      const copy = el.cloneNode(true);
      copy.querySelectorAll(`${REVIEWS_SEL}, script, style, [aria-hidden="true"]`).forEach((n) => n.remove());
      add(copy.innerText || copy.textContent);
    });
    if (parts.join(' ').length < 200) {
      // Little product text: the main area, without cart sidebars, reviews, navigation or screen-reader duplicates.
      const copy = (document.querySelector('main, [role=main], #dp, #content') || document.body).cloneNode(true);
      copy.querySelectorAll(`${IGNORE_SEL}, ${REVIEWS_SEL}, nav, header, footer, script, style, [aria-hidden="true"]`).forEach((n) => n.remove());
      add((copy.textContent || '').slice(0, 3000));
    }
    const text = parts.join('\n').slice(0, PAGE_TEXT_MAX);
    return text.length > 40 ? [{ type: 'page_text', text }] : [];
  }

  // Links a careful shopper looks for: a returns/refund policy and a way to contact the store (for the Trust Agent).
  function storeInfo() {
    const links = [...document.querySelectorAll('a[href]')].filter((a) => !ignored(a));
    const has = (re) => links.some((a) => re.test(`${a.textContent || ''} ${a.getAttribute('href') || ''}`));
    const returns = has(/return|refund|exchange/i);
    const contact = has(/contact|customer ?service|help ?cent(er|re)|support|\/help\b/i);
    return [{ type: 'store_info', text: `returns link: ${returns ? 'yes' : 'no'} · contact link: ${contact ? 'yes' : 'no'}`,
      detail: { returns, contact } }];
  }

  // Who sells and who ships, from the buy box when the page says so ("Sold by Huppins", "Ships from Amazon"). Read only.
  function sellerInfo() {
    const txt = (sel) => (document.querySelector(sel)?.textContent || '').replace(/\s+/g, ' ').trim();
    let soldBy = txt('[offer-display-feature-name="desktop-merchant-info"] .offer-display-feature-text-message') || txt('#sellerProfileTriggerId');
    let shipsFrom = txt('[offer-display-feature-name="desktop-fulfiller-info"] .offer-display-feature-text-message');
    const merchant = txt('#merchant-info');
    const both = merchant.match(/ships from and sold by\s+([^.]+)/i);
    if (both) { soldBy = soldBy || both[1].trim(); shipsFrom = shipsFrom || both[1].trim(); }
    if (!soldBy) soldBy = (merchant.match(/sold by\s+(.+?)(?:\s+and\s+(?:fulfilled|shipped)\b|\.|$)/i) || [])[1] || '';
    if (!shipsFrom && /fulfilled by amazon|shipped by amazon/i.test(merchant)) shipsFrom = 'Amazon';
    soldBy = soldBy.slice(0, 80); shipsFrom = shipsFrom.slice(0, 80);
    if (!soldBy) return [];
    return [{ type: 'seller', text: `Sold by ${soldBy}${shipsFrom ? `, ships from ${shipsFrom}` : ''}`,
      detail: { sold_by: soldBy, ships_from: shipsFrom || null } }];
  }

  // Not a new item: "(Open Box) JBL Flip 7", "Restored Apple iPhone 12", "... (Renewed)", or a "Condition: Pre-owned" row.
  // "Used" and "Restored" count only at the start of the title or after a bracket or dash ("Most used" is not a condition).
  const TITLE_COND_RE = /\b(open[- ]box|refurbished|renewed|pre-?owned)\b|(?:^|[(\[|\u2013-]\s*)(restored|used)\b/i;
  const ROW_COND_RE = /\bcondition\s*:?\s*(new|open[- ]box|restored|refurbished|renewed|pre-?owned|used)\b/i;
  const condKey = (w) => (/^open/i.test(w) ? 'open box' : /refurb|renew|restor/i.test(w) ? 'refurbished' : /owned|used/i.test(w) ? 'used' : 'new');
  function itemCondition(name) {
    const m = (name || '').match(TITLE_COND_RE);
    if (m) return [{ type: 'item_condition', text: m[0].trim().slice(0, 40), detail: { condition: condKey(m[1] || m[2]), source: 'title' } }];
    for (const el of findText(/\bcondition\b/i, 6)) {
      if (otherItem(el)) continue;
      const r = ((el.closest('tr, li, dl, div') || el).innerText || '').replace(/\s+/g, ' ').match(ROW_COND_RE);
      if (r) return [{ type: 'item_condition', selector: cssPath(block(el)), text: r[0].slice(0, 40), detail: { condition: condKey(r[1]), source: 'page' } }];
    }
    return [];
  }

  async function readPage() {
    const product = readProduct();
    const signals = [...itemCondition(product.name), ...claimedDiscounts(), ...(await countdowns()), ...precheckedAddons(), ...scarcity(), ...dripFees(), ...storeInfo(), ...sellerInfo(), ...pageHistory(), ...pageText()];
    return { url: location.href, host: location.host, title: document.title, https: location.protocol === 'https:' || location.hostname === 'localhost', product, signals };
  }

  // ---------------------------------------------------------------- automatic check on single product pages (any site)
  // A product page has structured product data (JSON-LD Product/ProductGroup, og:type=product, product:price meta,
  // microdata Product/Offer) OR one h1 + one prominent price + an add-to-cart/buy button.
  // Not product pages: search results and category/listing pages (many products), cart/checkout pages without a product,
  // and non-shopping sites. Read only: this decides whether to ask for a check, nothing else.
  const BUY_RE = /add to (cart|bag|basket|trolley)|buy (it )?now|add to order|pre-?order now|in den warenkorb|ajouter au panier/i;
  const SEARCH_URL_RE = /\/s\?|\/search\b|\/sch\/|[?&](k|q|d|query|search_query|searchterm|keyword|keywords|_nkw|text|term|st)=/i;
  const LISTING_PATH_RE = /\/(pl|c|category|categories|collections?|browse|department|shop)(\/[^/]*)?\/?$/i;
  const CART_URL_RE = /\/(cart|basket|bag|checkout)(\b|\.|\/|$)/i;

  function ldTopLevel() {
    const out = [];
    document.querySelectorAll('script[type="application/ld+json"]').forEach((s) => {
      try {
        const data = JSON.parse(s.textContent);
        for (const d of [].concat(data)) {
          if (!d || typeof d !== 'object') continue;
          out.push(d, ...(Array.isArray(d['@graph']) ? d['@graph'] : []));
        }
      } catch (_) { /* ignore broken JSON-LD */ }
    });
    return out.filter((d) => d && typeof d === 'object');
  }
  const ldType = (d) => [].concat(d['@type'] || []).map(String);

  function meta(prop) {
    return document.querySelector(`meta[property="${prop}"],meta[name="${prop}"]`)?.getAttribute('content') || '';
  }

  function buyButtons() {
    return [...document.querySelectorAll('button, input[type=submit], input[type=button], a, [role=button]')]
      .filter((el) => BUY_RE.test(el.innerText || el.value || el.getAttribute('aria-label') || '') && visible(el));
  }

  function prominentPrice() {
    const limit = window.innerHeight * 1.6;
    const cands = new Set(findText(PRICE_RE, 40));
    document.querySelectorAll('[class*=price],[id*=price],[itemprop=price],[data-testid*=price]').forEach((el) => cands.add(el));
    for (const el of cands) {
      if (!visible(el) || el.closest('s,del,strike') || ignored(el) || !PRICE_RE.test(el.innerText || '')) continue;
      const r = el.getBoundingClientRect();
      if (r.top + window.scrollY > limit) continue;
      const sizes = [el, ...el.querySelectorAll('*')].slice(0, 8).map((n) => parseFloat(getComputedStyle(n).fontSize) || 0);
      if (Math.max(...sizes) >= 18) return true;
    }
    return false;
  }

  // Product URLs and controls of large shops that often ship no JSON-LD (read only: used to decide whether to check).
  const STORE_PRODUCT_URL_RE = /\/dp\/[A-Z0-9]{10}|\/gp\/product\/[A-Z0-9]{10}|\/ip\/[^?#]+\/\d{5,}|\/ip\/\d{5,}|\/itm\/(?:[^/?#]+\/)?\d{9,}|\/site\/[^?#]+\.p(?:[/?]|$)|\/p\/[^?#]*\/-\/A-\d{5,}|\/products\/[^/?#]+\/?$/i;
  const PRICE_SELECTORS = ['[itemprop=price]', '#corePrice_feature_div .a-price', '#corePrice_desktop .a-price',
    '#corePriceDisplay_desktop_feature_div .a-price', '#priceblock_ourprice',
    '[data-testid="customer-price"]', '.priceView-customer-price', '[data-test="product-price"]', '[itemprop=offers] [itemprop=price]',
    '.x-price-primary', '[data-automation-id="product-price"]', '[itemprop="price"]', '.price-item--regular', '.product__price',
    '.product-single__price'].join(',');
  const ATC_SELECTORS = ['#add-to-cart-button', '#buy-now-button', '.add-to-cart-button', '[data-test="addToCartButton"]',
    '[data-automation-id="atc"]', '#atcBtn_btn_1', 'form[action*="/cart/add"] [type=submit]', 'form[action*="/cart/add"] button',
    'button[name="add"]', '[data-button-state="ADD_TO_CART"]'].join(',');
  const anyVisible = (sel) => { try { return [...document.querySelectorAll(sel)].some(visible); } catch (_) { return false; } };

  // { product: true|false, reason }: why this page was (or wasn't) treated as a product page. Logged once per URL.
  function detectProduct() {
    const ld = ldTopLevel();
    const ldProducts = ld.filter((d) => ldType(d).some((t) => t === 'Product' || t === 'ProductGroup')).length;
    const itemList = ld.some((d) => ldType(d).includes('ItemList') && [].concat(d.itemListElement || []).length >= 3);
    const microProducts = document.querySelectorAll('[itemscope][itemtype*="schema.org/Product"]').length;
    const microOffer = document.querySelector('[itemscope][itemtype*="schema.org/Offer"], [itemprop=offers]');
    const buttons = buyButtons();
    const top = (el) => el.getBoundingClientRect().top + window.scrollY;
    const firstScreen = buttons.filter((el) => top(el) < window.innerHeight);
    const nearTop = buttons.filter((el) => top(el) < window.innerHeight * 1.6);
    const path = location.pathname + location.search;

    // Several buy buttons on the first screen means a grid of products. "Related items" carousels sit lower on a
    // product page, so they don't count.
    const no = (reason) => ({ product: false, reason });
    const yes = (reason) => ({ product: true, reason });
    if (ldProducts > 1) return no(`listing: ${ldProducts} JSON-LD products`);
    if (itemList && ldProducts === 0) return no('listing: JSON-LD ItemList');
    if (microProducts > 2) return no(`listing: ${microProducts} microdata products`);
    const priceEl = anyVisible(PRICE_SELECTORS);
    const atcEl = anyVisible(ATC_SELECTORS);
    // A known product URL with a price or an add-to-cart control (before the button count: product pages of big shops
    // show "Buy now", "Add to cart" and protection-plan buttons together on the first screen).
    if (STORE_PRODUCT_URL_RE.test(location.pathname) && (priceEl || atcEl || buttons.length))
      return yes(`store product URL + ${priceEl ? 'price element' : atcEl ? 'add-to-cart control' : 'buy button'}`);
    if (firstScreen.length >= 4) return no(`listing: ${firstScreen.length} buy buttons on the first screen`);
    if (ldProducts === 1) return yes('JSON-LD Product');
    if (/product/i.test(meta('og:type'))) return yes(`og:type=${meta('og:type')}`);
    if (meta('product:price:amount') || meta('og:price:amount')) return yes('product price meta');
    if (microProducts === 1 || (!!microOffer && microProducts <= 1)) return yes('microdata Product/Offer');
    // Without structured data, the URL tells search, category and cart pages apart from a product.
    const listingPath = LISTING_PATH_RE.test(location.pathname) && !/\/products?\//i.test(location.pathname);
    if (SEARCH_URL_RE.test(path)) return no('search results URL');
    if (listingPath) return no('category URL');
    if (CART_URL_RE.test(location.pathname)) return no('cart or checkout URL');
    if (atcEl && priceEl) return yes('add-to-cart control + price element');
    const h1s = [...document.querySelectorAll('h1')].filter(visible);
    if (h1s.length === 1 && nearTop.length >= 1 && prominentPrice()) return yes('one heading + buy button + prominent price');
    return no(h1s.length !== 1 ? `${h1s.length} main headings, no product data` : nearTop.length ? 'no prominent price' : 'no buy button');
  }

  let loggedUrl = null;
  function logDecision(d) {
    if (loggedUrl === location.href + d.reason) return;
    loggedUrl = location.href + d.reason;
    console.debug(`[Pricise] product page: ${d.product ? 'yes' : 'no'} (${d.reason})`); // DevTools > Console > Verbose
  }

  let checkedUrl = null;
  async function autoCheck(retries = 1) {
    if (window.top !== window) return;
    const url = location.href;
    const s = (await chrome.storage.local.get('pricise:settings'))['pricise:settings'] || {};
    if ((s.neverSites || []).includes(location.host)) return;
    const d = detectProduct();
    logDecision(d);
    if (!d.product) {
      // Some shops render the product after load: look once more before giving up.
      if (retries > 0) setTimeout(() => { if (location.href === url) autoCheck(retries - 1); }, 2500);
      return;
    }
    if (checkedUrl === url) return;
    checkedUrl = url;
    const page = await readPage();
    if (location.href !== url) return; // moved on while reading
    // A product URL that served something else (a block or interstitial page titled just "Amazon.com"): no price and no
    // product name. Nothing to check, no card. The backend makes the same call for any other caller.
    const site = location.hostname.replace(/^www\./, '');
    const name = (page.product.name || '').trim().toLowerCase().replace(/^www\./, '');
    if (page.product.price == null && (!name || name === site || name === site.split('.')[0])) {
      console.debug('[Pricise] product page: no (no price and only the site name: not a product page after all)');
      return;
    }
    chrome.runtime.sendMessage({ type: 'pricise:auto-check', page }).catch(() => {});
  }
  autoCheck().catch(() => {});

  // Single-page shops (Amazon, Walmart, Shopify themes) change the URL without reloading. The content script can't see the
  // page's own history.pushState calls, so watch the signals it can: popstate, the Navigation API, and the <title> changing.
  let lastUrl = location.href;
  let settle = null;
  function onMaybeNewUrl() {
    if (location.href === lastUrl) return;
    lastUrl = location.href;
    window.__priciseRemoveCard?.();
    clearMarks();
    clearTimeout(settle);
    settle = setTimeout(() => autoCheck(1).catch(() => {}), 1200); // give the new product a moment to render
  }
  window.addEventListener('popstate', onMaybeNewUrl);
  window.navigation?.addEventListener?.('navigatesuccess', onMaybeNewUrl);
  const titleWatch = new MutationObserver(onMaybeNewUrl);
  const watchTitle = () => {
    const t = document.querySelector('head > title');
    if (t) titleWatch.observe(t, { childList: true, characterData: true, subtree: true });
  };
  watchTitle();
  if (document.head) new MutationObserver(() => { watchTitle(); onMaybeNewUrl(); }).observe(document.head, { childList: true });
  setInterval(onMaybeNewUrl, 1500); // pushState without a title change (some Shopify themes, Walmart variants)

  // ---------------------------------------------------------------- outlines on the page
  let marks = [];
  function clearMarks() {
    document.querySelectorAll('.pricise-mark').forEach((el) => el.classList.remove('pricise-mark', 'pricise-mark--caution', 'pricise-mark--neutral'));
    document.querySelectorAll('.pricise-label').forEach((el) => el.remove());
    marks = [];
  }
  function placeLabels() {
    marks.forEach(({ el, label, placement }) => {
      if (!label) return;
      // Put the label where it covers the least: to the right of small elements, otherwise on the top-right corner.
      const r = el.getBoundingClientRect();
      const w = label.offsetWidth || 90;
      const roomRight = window.innerWidth - r.right > w + 16;
      if (roomRight && (placement === 'right' || r.width < window.innerWidth * 0.35)) {
        label.style.top = `${r.top + window.scrollY + r.height / 2 - 11}px`;
        label.style.left = `${r.right + window.scrollX + 12}px`;
      } else {
        label.style.top = `${r.top + window.scrollY - 13}px`;
        label.style.left = `${Math.max(4, r.right + window.scrollX - w + 6)}px`;
      }
    });
  }
  function highlight(list) {
    clearMarks();
    list.forEach(({ selector, label, tone, placement }) => {
      let el = null;
      try { el = document.querySelector(selector); } catch (_) { return; }
      if (!el || marks.some((m) => m.el === el)) return;
      el.classList.add('pricise-mark');
      if (tone === 'caution' || tone === 'neutral') el.classList.add(`pricise-mark--${tone}`);
      let tag = null;
      if (label) {
        tag = document.createElement('div');
        tag.className = 'pricise-label' + (tone === 'caution' || tone === 'neutral' ? ` pricise-label--${tone}` : '');
        tag.textContent = label;
        document.body.appendChild(tag);
      }
      marks.push({ el, label: tag, placement });
    });
    placeLabels();
    return marks.length;
  }
  window.addEventListener('resize', placeLabels);
  window.addEventListener('scroll', placeLabels, { passive: true });

  // ---------------------------------------------------------------- messages from the side panel
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg?.type === 'pricise:read') { readPage().then(sendResponse); return true; }
    if (msg?.type === 'pricise:detect') { sendResponse(detectProduct()); }
    if (msg?.type === 'pricise:highlight') { sendResponse({ ok: true, count: highlight(msg.marks || []) }); }
    if (msg?.type === 'pricise:clear') { clearMarks(); sendResponse({ ok: true }); }
    if (msg?.type === 'pricise:untick') {
      // The one change Pricise makes, and only because the user pressed the button.
      let cb = null;
      try { cb = document.querySelector(msg.selector); } catch (_) { /* ignore */ }
      if (cb && cb.tagName !== 'INPUT') cb = cb.querySelector('input[type=checkbox]');
      if (cb && cb.checked) { cb.click(); if (cb.checked) cb.checked = false; }
      const m = marks.find((x) => x.el.contains(cb) || x.el === cb);
      if (m) { m.el.classList.remove('pricise-mark', 'pricise-mark--caution'); m.label?.remove(); marks = marks.filter((x) => x !== m); }
      sendResponse({ ok: !!cb, checked: cb ? cb.checked : null });
    }
  });
})();
