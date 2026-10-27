// Evidence page sections: the proof behind every line in the side panel, as a pure function of the stored check, so the
// page shows only sections with real content (tested with Node in backend/tests/test_evidence_page.py).
import { esc, money } from '../shared/api.js';
import { compareLinks, fetchedAt, rowKey, todayize } from '../shared/check.js';

export const isStoreLab = (url) => /^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?\/(storelab|bench)\//.test(url || '');

// [{ id, html }] in page order. Real store pages: what was read, store checks, price comparison, price history, trace.
// Store Lab pages keep their demo panels (timer replay, checkout fees) when those have data.
// Tabs in order. A tab exists only when its section has content on this page.
export const TABS = [['overview', 'Overview'], ['page', 'Page'], ['whois', 'Store checks'], ['stores', 'Price comparison'],
  ['chart', 'Price history'], ['fees', 'Fees'], ['replay', 'Timer replay'], ['trace', 'Agent trace']];
const MISSING = { replay: 'No timer on this page', fees: 'No checkout fee table on this page', chart: 'No price history for this page',
  whois: 'No store checks for this page', stores: 'No price comparison for this page', trace: 'No agent findings yet' };

const SIGNALS = { replay: ['resetting_timer', 'countdown'], fees: ['drip_fees'], chart: ['claimed_discount'] };

// Which tab a link opens: { tab, note, signals }. A tab with no data on this page opens the nearest one with a one-line note:
// the timer and fees live in "What Pricise read" on a real page (their signal rows), everything else in the overview.
export function resolve(view, available) {
  const want = view || 'overview';
  if (available.includes(want)) return { tab: want, note: null, signals: [] };
  const near = ['replay', 'fees', 'chart'].includes(want) && available.includes('page') ? 'page' : 'overview';
  return { tab: near, note: MISSING[want] || null, signals: near === 'page' ? SIGNALS[want] || [] : [] };
}

// The row a link should highlight among a tab's row keys: the exact key, else one that starts the same way (a panel line
// is often a shortened finding), else -1 (the caller then uses the tab's default row).
export function matchRow(keys, key) {
  if (!key) return -1;
  const exact = keys.indexOf(key);
  if (exact >= 0) return exact;
  const prefix = keys.findIndex((k) => k && (k.startsWith(key) || key.startsWith(k)));
  if (prefix >= 0) return prefix;
  // Same words, other wording ("Domain registered 6 days ago" / "The domain was registered 6 days ago."): most shared words.
  const words = key.split('-');
  let best = -1, bestScore = 0;
  keys.forEach((k, i) => {
    const have = new Set((k || '').split('-'));
    const score = words.filter((w) => have.has(w)).length / words.length;
    if (score > bestScore) { best = i; bestScore = score; }
  });
  return bestScore >= 0.6 ? best : -1;
}

export function sections(chk) {
  const ev = chk.evidence_data || {};
  const p = chk.page?.product || {};
  const findings = chk.findings || [];
  const real = !isStoreLab(chk.url);
  const out = [];
  const add = (id, html) => { if (html) out.push({ id, html }); };
  add('overview', 'pending'); // filled in below, once the other sections are known
  add('page', pagePanel(chk));
  add('whois', real && ev.store_checks?.length ? storeChecksPanel(ev.store_checks) : trustPanel(findings));
  add('stores', real ? comparisonPanel(ev, p) : storesPanel(ev.stores, ev.stores_sql));
  add('chart', ev.price_history ? chartPanel(ev.price_history, p) : historyNote(real, p));
  const fees = (ev.fees || []).filter((f) => f.label !== 'Item');
  if (fees.length || !real) add('fees', feesPanel(ev));
  if ((ev.timer_frames || []).length || findings.some((f) => /timer|countdown/.test(f.type))) add('replay', replayPanel(ev.timer_frames, findings));
  add('trace', tracePanel(findings));
  out[0].html = overviewPanel(chk, out.slice(1).map((s) => s.id));
  return out;
}

function overviewPanel(chk, ids) {
  const card = chk.final_card || {};
  const ev = chk.evidence_data || {};
  const line = {
    page: `${esc(chk.page?.product?.name?.slice(0, 70) || chk.page?.host || 'This page')}${chk.page?.product?.price != null ? ` · ${money(chk.page.product.price)}` : ''}`,
    whois: esc(card.safe?.headline || 'Store checks'), stores: esc(card.best_price?.headline || 'Price comparison'),
    chart: ev.price_history ? esc(ev.price_history.label || 'Price history') : 'No price history for this store yet',
    fees: ev.total != null ? `Real total ${money(ev.total)}` : 'Checkout fees', replay: 'Countdown timer visits',
    trace: `${(chk.findings || []).length} findings`,
  };
  const items = ids.map((id) => `<li data-row="${id}"><a href="#" data-tab="${id}">${esc(TABS.find((x) => x[0] === id)?.[1] || id)}</a>
    <span class="muted"> · ${line[id] || ''}</span></li>`).join('');
  return `<section class="panel span2" id="overview"><div class="panel-head"><h2>${esc(card.title || 'Overview')}</h2>
    ${card.subtitle ? `<span class="pill info">${esc(card.subtitle)}</span>` : ''}</div><ul class="ov" data-row-default>${items}</ul></section>`;
}

const rk = (text) => `data-row="${esc(rowKey(text))}"`; // ties a row to the panel line that links to it
const row = (k, v) => (v == null || v === '' ? '' : `<tr ${rk(k)}><td>${esc(k)}</td><td>${v}</td></tr>`);

function pagePanel(chk) {
  const page = chk.page || {};
  const p = page.product || {};
  const sigRows = (page.signals || []).filter((s) => s.type !== 'page_text').map((s, i) => `<tr id="signal-${i}" ${rk(s.text)} data-signal="${esc(s.type)}"><td>${i ? '' : 'Signals found'}</td>
    <td>${esc(s.type.replace(/_/g, ' '))}: ${esc((s.text || '').slice(0, 90))}</td></tr>`).join('');
  return `<section class="panel span2" id="page"><div class="panel-head"><h2>What Pricise read on this page</h2><span class="pill unknown">Read only</span></div>
    <table><tbody>${row('Product', esc(p.name))}${row('Price', p.price != null ? `<b>${money(p.price)}</b>` : '')}
    ${row('Was-price on the page', p.was_price != null ? money(p.was_price) : 'None shown')}${row('Brand', esc(p.brand))}
    ${row('Model', esc(p.model))}${row('SKU', esc(p.sku))}${row('Seller (site)', esc(page.host))}
    ${row('Read from', esc(p.source || 'the visible page'))}${row('Product text for the chat',
      (() => { const t = (page.signals || []).find((s) => s.type === 'page_text')?.text; return t ? `${t.length} characters (no reviews)` : ''; })())}${sigRows || row('Signals found', 'None')}</tbody></table></section>`;
}

function storeChecksPanel(checks) {
  const tone = { ok: 'good', flag: 'bad', unchecked: 'unknown' };
  const word = { ok: 'OK', flag: 'Warning', unchecked: 'Not checked' };
  const source = (c) => (c.evidence || []).map((e) => (e.kind === 'url' && /^https?:/.test(e.ref)
    ? `<a href="${esc(e.ref)}" target="_blank" rel="noopener noreferrer">${esc(e.ref.replace(/^https?:\/\//, '').slice(0, 48))}</a>`
    : esc(String(e.ref).slice(0, 80)))).join('<br>');
  const rows = checks.map((c, i) => `<tr ${rk(c.result)}${i ? '' : ' data-row-default'}><td>${esc(c.result)}</td><td class="muted">${source(c)}</td>
    <td class="num"><span class="pill ${tone[c.status]}">${word[c.status]}</span></td></tr>`).join('');
  return `<section class="panel" id="whois"><div class="panel-head"><h2>Store checks</h2></div>
    <table><thead><tr><th>Check</th><th>Source</th><th class="num"></th></tr></thead><tbody>${rows}</tbody></table>
    <div class="note">Domain age comes from public registration data (RDAP), never from the store's own site.</div></section>`;
}

function comparisonPanel(ev, p) {
  const o = ev.online;
  const links = compareLinks(p).map((l) => `<a href="${esc(l.url)}" target="_blank" rel="noopener noreferrer">${esc(l.label)}</a>`).join(' · ');
  if (!o) {
    const seen = (ev.stores || []).map((s, i) => `<tr ${rk(s.store)}${i ? '' : ' data-row-default'}><td><b>${esc(s.store)}</b></td><td>${esc(s.note)}</td><td class="num">${money(s.total)}</td></tr>`).join('');
    return `<section class="panel" id="stores"><div class="panel-head"><h2>Price comparison</h2><span class="pill unknown">Compare</span></div>
      ${seen ? `<table><tbody>${seen}</tbody></table>` : '<div class="empty-note">No other store prices seen yet, and online price search is off.</div>'}
      <div class="note">Search other stores: ${links}</div></section>`;
  }
  const shown = (o.shown || []).map((s, i) => `<tr ${rk(s.store)}${i ? '' : ' data-row-default'}><td><b>${esc(s.store)}</b>${
    (s.caveats || []).length ? `<div class="muted">Caveats: ${s.caveats.map(esc).join('; ')}</div>` : ''}</td><td>${esc(s.condition)} · ${esc(s.shipping)}${s.fetched_at ? `<div class="muted">fetched ${esc(fetchedAt(s.fetched_at))}</div>` : ''}</td>
    <td class="num">${money(s.total)}</td><td class="num"><a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">Open</a></td></tr>`).join('');
  const groups = {};
  (o.dropped || []).forEach((d) => { (groups[d.group] ||= []).push(d); });
  const dropped = Object.entries(groups).map(([g, list]) => `<details class="sql"><summary>${esc(g)} (${list.length})</summary><table><tbody>${
    list.map((d) => `<tr ${rk(d.source)}><td>${esc(d.source)}</td><td>${esc(d.title)}</td><td class="num">${money(d.price)}</td><td class="muted">${esc(d.reason)}</td></tr>`).join('')
  }</tbody></table></details>`).join('');
  return `<section class="panel span2" id="stores"><div class="panel-head"><h2>Price comparison</h2><span class="pill info">${(o.shown || []).length} credible store${(o.shown || []).length === 1 ? '' : 's'}</span></div>
    <div class="note">Search: “${esc(o.query)}” · ${o.offers_seen} offers from Google Shopping (${esc(o.source)})${o.sellers_source ? ` · sellers list (${esc(o.sellers_source)})` : ''}</div>
    ${(o.fetched || []).length ? `<div class="note">Prices from ${o.fetched.map((x) => `${esc(x.what)}, fetched ${esc(fetchedAt(x.at))}`).join('; ')}.${
      o.source === 'zetaris usl' ? ' This is a cached snapshot, not a live search: prices may have changed since. Open a listing to confirm.' : ''}</div>` : ''}
    ${shown ? `<table><thead><tr><th>Store</th><th>Condition · shipping</th><th class="num">True total</th><th class="num">Listing</th></tr></thead><tbody>${shown}</tbody></table>` : '<div class="empty-note">No trusted store had this exact item at a real price.</div>'}
    ${(o.also_checked || []).length ? `<div class="note">Also checked: ${esc(o.also_checked.join('; '))}</div>` : ''}
    ${dropped ? `<h3 style="font-size:13px;margin:14px 0 0">Left out, by reason</h3>${dropped}` : ''}
    ${o.usl_fallback ? `<div class="note">The Zetaris USL (safe_price) didn't answer (${esc(o.usl_fallback)}), so these offers came from ${esc(o.source === 'cache' ? 'the Pricise cache' : 'a Google Shopping search')}.</div>` : ''}
    ${sqlBlock(o.sql, o.source === 'zetaris usl' ? 'Zetaris query that read these offers (USL safe_price)' : 'Zetaris query that read these offers')}
    <div class="note">Search other stores: ${links}</div></section>`;
}

function historyNote(real, p) {
  if (!real) return '';
  return `<section class="panel" id="chart"><div class="panel-head"><h2>Price history</h2><span class="pill unknown">None yet</span></div>
    <div class="empty-note">No price history for this store yet. Pricise saves the price each time you check this item, and reads a
    price-history chart the page itself shows.${p.was_price ? ` The page shows a was-price of ${money(p.was_price)}.` : ''}</div></section>`;
}

export function chartPanel(h, p) {
  if (!h) {
    return `<section class="panel" id="chart"><div class="panel-head"><h2>Price history</h2><span class="pill unknown">Not connected</span></div>
      <div class="empty-note">Price history for this store comes from the Deal Agent's data in Zetaris. It isn't connected yet, so discount claims on this page are marked unverified.${p.was_price ? ` The page claims a was-price of ${money(p.was_price)}.` : ''}</div></section>`;
  }
  const W = 640, H = 220, pad = 30, max = Math.max(...h.points, h.claimed_was) * 1.08, min = 0;
  const x = (i) => pad + (i / (h.points.length - 1)) * (W - pad * 2);
  const y = (v) => H - pad - ((v - min) / (max - min)) * (H - pad * 2);
  const line = h.points.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('');
  const fake = (h.days_at_claim ?? 99) <= 7;
  return `<section class="panel" id="chart"><div class="panel-head"><h2>Price history · ${esc(h.store)}</h2><span class="pill ${fake ? 'bad' : 'unknown'}">${fake ? 'Fake discount' : 'History'}</span></div>
    <svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="90-day price history">
      <line x1="${pad}" x2="${W - pad}" y1="${y(h.claimed_was)}" y2="${y(h.claimed_was)}" style="stroke: var(--bad)" stroke-dasharray="5 5" stroke-width="1.5"/>
      <text x="${W - pad}" y="${y(h.claimed_was) - 7}" text-anchor="end" font-size="12" style="fill: var(--bad)" font-weight="700">"Was" ${money(h.claimed_was)}: ${h.days_at_claim} days</text>
      <path d="${line}" fill="none" style="stroke: var(--ink)" stroke-width="2.5" stroke-linejoin="round"/>
      <text x="${pad}" y="${y(h.points[0]) + 18}" font-size="12" style="fill: var(--ink-2)">${money(h.points[0])} for ${h.days_at_price} of 90 days</text>
      <text x="${pad}" y="${H - 8}" font-size="12" style="fill: var(--ink-3)">90 days ago</text><text x="${W - pad}" y="${H - 8}" font-size="12" style="fill: var(--ink-3)" text-anchor="end">Today</text>
    </svg><div class="note">${esc(h.label)}</div>${sqlBlock(h.sql, 'Zetaris query behind this chart')}</section>`;
}

// The Lightning SQL a finding ran through Zetaris, shown as proof (collapsed by default).
export function sqlBlock(sql, title) {
  if (!sql) return '';
  return `<details class="sql"><summary>${esc(title)}</summary><pre>${esc(sql)}</pre></details>`;
}

export function feesPanel(ev) {
  const fees = ev.fees || [];
  if (!fees.length) return `<section class="panel" id="fees"><div class="panel-head"><h2>True total</h2><span class="pill unknown">No fees found</span></div>
    <div class="empty-note">No extra fees were visible on this page. Fees often appear later in checkout; open the cart and Pricise will check again.</div></section>`;
  const rows = fees.map((f) => `<tr ${rk(f.label)}><td>${esc(f.label)}</td><td>${f.step ? (f.step > 1 ? `<span class="late">Step ${f.step}</span>` : 'Step 1') : ''}</td><td class="num">${money(f.amount)}</td></tr>`).join('');
  const shown = fees[0]?.amount;
  return `<section class="panel" id="fees"><div class="panel-head"><h2>True total</h2>${ev.total && shown && ev.total > shown ? `<span class="pill bad">+${money(+(ev.total - shown).toFixed(2))} hidden</span>` : ''}</div>
    <table><thead><tr><th>Charge</th><th>Shown at</th><th class="num">Amount</th></tr></thead><tbody>${rows}
    <tr class="total" data-row-default><td>Real total</td><td></td><td class="num">${money(ev.total)}</td></tr></tbody></table></section>`;
}

export function replayPanel(frames, findings) {
  const timer = (findings || []).find((f) => /timer|countdown/.test(f.type));
  if (!frames?.length) {
    return `<section class="panel" id="replay"><div class="panel-head"><h2>Timer replay</h2><span class="pill ${timer ? 'caution' : 'unknown'}">${timer ? 'Watching' : 'No timer'}</span></div>
      <div class="empty-note">${timer ? esc(timer.summary) : 'No countdown timer on this page.'}</div></section>`;
  }
  const cells = frames.map((f, i) => `<div class="frame"${i ? '' : ' data-row-default'}><div class="t">Visit ${f.visit} · ${esc(f.time)}</div><div class="v">${esc(f.shown)}</div><div class="t">"Deal ends in"</div></div>`).join('');
  return `<section class="panel" id="replay"><div class="panel-head"><h2>Timer replay</h2><span class="pill bad">Resets every visit</span></div>
    <div class="frames">${cells}</div><div class="note">Same "deadline" on every visit, so it isn't a real deadline.</div></section>`;
}

export function storesPanel(stores, sql) {
  if (!stores?.length) return `<section class="panel" id="stores"><div class="panel-head"><h2>Other stores</h2><span class="pill unknown">Not connected</span></div>
    <div class="empty-note">The Price Agent searches trusted stores for the exact same item. It isn't connected yet.</div></section>`;
  const tone = { chosen: 'good', ok: 'unknown', rejected: 'bad', skipped: 'unknown' };
  const rows = stores.map((s) => `<tr ${rk(s.store)}${s.status === 'chosen' ? ' data-row-default' : ''}><td><b>${esc(s.store)}</b></td><td>${esc(s.note)}</td><td class="num">${money(s.total)}</td>
    <td class="num"><span class="pill ${tone[s.status]}">${s.status === 'chosen' ? 'Best safe price' : s.status === 'rejected' ? 'Rejected' : s.status === 'skipped' ? 'Skipped' : 'OK'}</span></td></tr>`).join('');
  return `<section class="panel" id="stores"><div class="panel-head"><h2>Store comparison</h2></div>
    <table><thead><tr><th>Store</th><th>Why</th><th class="num">True total</th><th class="num"></th></tr></thead><tbody>${rows}</tbody></table>
    ${sqlBlock(sql, 'Zetaris query behind this comparison')}</section>`;
}

export function trustPanel(findings) {
  const t = (findings || []).filter((f) => f.agent === 'trust');
  const rows = t.map((f, i) => `<tr ${rk(f.summary)}${i ? '' : ' data-row-default'}><td>${esc(f.summary)}</td><td class="num"><span class="pill ${f.status === 'unverified' ? 'unknown' : 'bad'}">${esc(f.status)}</span></td></tr>`).join('');
  return `<section class="panel" id="whois"><div class="panel-head"><h2>Store checks</h2></div>
    ${t.length ? `<table><tbody>${rows}</tbody></table>` : '<div class="empty-note">No store checks yet.</div>'}</section>`;
}

export const TRACE_FIRST = 40; // longer traces show the first 40 rows and a "Show all" button

// "Model: nvidia/... · Source: pricise_usl.safe_price": who decided and what the finding read.
export function traceSource(f) {
  const ev = f.evidence || [];
  const sql = ev.find((e) => e.kind === 'sql');
  if (sql) {
    const m = sql.ref.match(/\bFROM\s+([\w.]+)/i);
    const parts = (m ? m[1] : 'zetaris').split('.');
    return `Zetaris ${parts[0] === 'lightning' ? parts.slice(-2).join('.') : parts.slice(-1)[0]}`;
  }
  const log = ev.find((e) => e.kind === 'log');
  if (log) return log.ref.startsWith('google_shopping:') ? 'Google Shopping (SerpApi)' : log.ref.split(':')[0];
  if (ev.some((e) => e.kind === 'selector' || e.kind === 'text')) return 'this page';
  const url = ev.find((e) => e.kind === 'url');
  return url ? (() => { try { return new URL(url.ref).hostname; } catch (_) { return 'a link'; } })() : 'rules';
}

// "challenged by skeptic", not "challenged, challenged by skeptic"; a decided one keeps its status: "confirmed, challenged by skeptic".
export function traceStatus(f) {
  if (!f.challenged_by) return f.status;
  return f.status === 'challenged' ? `challenged by ${f.challenged_by}` : `${f.status}, challenged by ${f.challenged_by}`;
}

export function tracePanel(findings) {
  const all = findings || [];
  const row = (f, i) => `<div class="r${i >= TRACE_FIRST ? ' more' : ''}" id="finding-${i}" ${rk(f.summary)}${i >= TRACE_FIRST ? ' hidden' : ''}><span>${new Date(f.ts * 1000).toLocaleTimeString()}</span><b>${esc(f.agent)}</b>
    <span>${esc(traceStatus(f))}</span><span>${esc(todayize(f.summary))}<br><span class="muted src">Model: ${esc(f.model || 'rules')} · Source: ${esc(traceSource(f))}</span></span></div>`;
  const more = all.length > TRACE_FIRST
    ? `<button class="btn" data-act="trace-all" type="button">Show all ${all.length} findings</button>` : '';
  return `<section class="panel span2" id="trace"><div class="panel-head"><h2>Agent trace</h2><span class="pill info">${all.length} findings</span></div>
    <div class="trace">${all.map(row).join('') || '<div class="empty-note">No findings.</div>'}</div>${more}</section>`;
}
