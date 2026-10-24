// Pricise content script. Runs on every page, but only READS the page.
// It never clicks, submits or buys anything. The only change it makes is when the user presses "Untick" in the panel.
(() => {
  if (window.__pricise) return;
  window.__pricise = true;

  const MAX_PER_TYPE = 4;
  const PRICE_RE = /(?:US\s?)?([$€£₹])\s?(\d{1,3}(?:[,.\s]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)/;

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
        if (p.closest('.pricise-label')) return NodeFilter.FILTER_REJECT;
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

  function readProduct() {
    const ld = readJsonLd() || {};
    const p = { ...ld };
    p.name = p.name || meta('og:title') || document.querySelector('h1')?.innerText?.trim() || document.title;
    if (p.price == null) {
      const m = meta('product:price:amount') || meta('og:price:amount') || document.querySelector('[itemprop=price]')?.getAttribute('content');
      if (m) { p.price = parseFloat(m); p.source = p.source || 'meta'; p.currency = p.currency || meta('product:price:currency'); }
    }
    if (p.price == null) {
      // Visible fallback: the biggest price-looking text that is not struck through.
      let best = null;
      document.querySelectorAll('[class*=price],[id*=price],[data-testid*=price],[itemprop=price]').forEach((el) => {
        if (!visible(el) || el.closest('s,del,strike')) return;
        const t = el.innerText || '';
        const v = parseAmount(t);
        if (v == null) return;
        const size = parseFloat(getComputedStyle(el).fontSize) || 0;
        if (!best || size > best.size) best = { v, size, t };
      });
      if (best) { p.price = best.v; p.currency = currencyOf(best.t); p.source = 'visible'; }
    }
    p.name = (p.name || '').replace(/\s+/g, ' ').trim().slice(0, 160) || null;
    return p;
  }

  // ---------------------------------------------------------------- signals (what a careful shopper would notice)
  function claimedDiscounts() {
    const out = [];
    const els = new Set();
    document.querySelectorAll('s,del,strike,[class*=strike],[class*=was-price],[class*=list-price],[class*=original-price],[class*=compare]')
      .forEach((el) => { if (visible(el) && PRICE_RE.test(el.innerText || '')) els.add(el); });
    findText(/\b(was|list price|reg\.?|regular|rrp|compare at)\b[^\n]{0,20}[$€£₹]\s?\d/i).forEach((el) => els.add(el));
    findText(/-\s?\d{1,2}\s?%|\b\d{1,2}\s?%\s?off\b|save\s+\d{1,2}\s?%/i).forEach((el) => els.add(el));
    for (const el of [...els].slice(0, MAX_PER_TYPE)) {
      const b = block(el);
      out.push({ type: 'claimed_discount', selector: cssPath(b), text: (b.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 100), amount: parseAmount(el.innerText) });
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

  async function readPage() {
    const signals = [...claimedDiscounts(), ...(await countdowns()), ...precheckedAddons(), ...scarcity(), ...dripFees()];
    return { url: location.href, host: location.host, title: document.title, https: location.protocol === 'https:' || location.hostname === 'localhost', product: readProduct(), signals };
  }

  // ---------------------------------------------------------------- outlines on the page
  let marks = [];
  function clearMarks() {
    document.querySelectorAll('.pricise-mark').forEach((el) => el.classList.remove('pricise-mark', 'pricise-mark--caution'));
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
      if (tone === 'caution') el.classList.add('pricise-mark--caution');
      let tag = null;
      if (label) {
        tag = document.createElement('div');
        tag.className = 'pricise-label' + (tone === 'caution' ? ' pricise-label--caution' : '');
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
