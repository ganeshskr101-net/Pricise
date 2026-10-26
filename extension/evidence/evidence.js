// Evidence page: the proof behind every line in the side panel.
import { api, money, esc } from '../shared/api.js';

const grid = document.getElementById('grid');
const id = new URLSearchParams(location.search).get('check');

load();

async function load() {
  if (!id) { grid.innerHTML = '<div class="panel span2 empty-note">Open this page from the Pricise side panel.</div>'; return; }
  let chk;
  try { chk = await api.getCheck(id); } catch (e) {
    grid.innerHTML = `<div class="panel span2 empty-note">Could not load this check (${esc(e.message)}). Is the backend running?</div>`; return;
  }
  const ev = chk.evidence_data || {};
  const p = chk.page?.product || {};
  document.getElementById('page-url').textContent = chk.url;
  document.getElementById('title').textContent = `Evidence · ${p.name ? p.name.slice(0, 70) : chk.page?.host || 'this page'}`;
  if (chk.final_card) document.getElementById('subtitle').textContent = `${chk.final_card.title} · ${chk.final_card.subtitle}`;

  grid.innerHTML = [
    chartPanel(ev.price_history, p),
    feesPanel(ev),
    replayPanel(ev.timer_frames, chk.findings),
    storesPanel(ev.stores, ev.stores_sql),
    trustPanel(chk.findings),
    tracePanel(chk.findings),
  ].join('');
  if (location.hash) document.querySelector(location.hash)?.scrollIntoView({ block: 'center' });
}

function chartPanel(h, p) {
  if (!h) {
    return `<section class="panel" id="chart"><div class="panel-head"><h2>Price history</h2><span class="pill unknown">Not connected</span></div>
      <div class="empty-note">Price history for this store comes from the Deal Agent's data in Zetaris. It isn't connected yet, so discount claims on this page are marked unverified.${p.was_price ? ` The page claims a was-price of ${money(p.was_price)}.` : ''}</div></section>`;
  }
  const W = 640, H = 220, pad = 30, max = Math.max(...h.points, h.claimed_was) * 1.08, min = 0;
  const x = (i) => pad + (i / (h.points.length - 1)) * (W - pad * 2);
  const y = (v) => H - pad - ((v - min) / (max - min)) * (H - pad * 2);
  const line = h.points.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('');
  return `<section class="panel" id="chart"><div class="panel-head"><h2>Price history · ${esc(h.store)}</h2><span class="pill bad">Fake discount</span></div>
    <svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="90-day price history">
      <line x1="${pad}" x2="${W - pad}" y1="${y(h.claimed_was)}" y2="${y(h.claimed_was)}" style="stroke: var(--bad)" stroke-dasharray="5 5" stroke-width="1.5"/>
      <text x="${W - pad}" y="${y(h.claimed_was) - 7}" text-anchor="end" font-size="12" style="fill: var(--bad)" font-weight="700">"Was" ${money(h.claimed_was)}: ${h.days_at_claim} days</text>
      <path d="${line}" fill="none" style="stroke: var(--ink)" stroke-width="2.5" stroke-linejoin="round"/>
      <text x="${pad}" y="${y(h.points[0]) + 18}" font-size="12" style="fill: var(--ink-2)">${money(h.points[0])} for ${h.days_at_price} of 90 days</text>
      <text x="${pad}" y="${H - 8}" font-size="12" style="fill: var(--ink-3)">90 days ago</text><text x="${W - pad}" y="${H - 8}" font-size="12" style="fill: var(--ink-3)" text-anchor="end">Today</text>
    </svg><div class="note">${esc(h.label)}</div>${sqlBlock(h.sql, 'Zetaris query behind this chart')}</section>`;
}

// The Lightning SQL a finding ran through Zetaris, shown as proof (collapsed by default).
function sqlBlock(sql, title) {
  if (!sql) return '';
  return `<details class="sql"><summary>${esc(title)}</summary><pre>${esc(sql)}</pre></details>`;
}

function feesPanel(ev) {
  const fees = ev.fees || [];
  if (!fees.length) return `<section class="panel" id="fees"><div class="panel-head"><h2>True total</h2><span class="pill unknown">No fees found</span></div>
    <div class="empty-note">No extra fees were visible on this page. Fees often appear later in checkout; open the cart and Pricise will check again.</div></section>`;
  const rows = fees.map((f) => `<tr><td>${esc(f.label)}</td><td>${f.step ? (f.step > 1 ? `<span class="late">Step ${f.step}</span>` : 'Step 1') : ''}</td><td class="num">${money(f.amount)}</td></tr>`).join('');
  const shown = fees[0]?.amount;
  return `<section class="panel" id="fees"><div class="panel-head"><h2>True total</h2>${ev.total && shown && ev.total > shown ? `<span class="pill bad">+${money(+(ev.total - shown).toFixed(2))} hidden</span>` : ''}</div>
    <table><thead><tr><th>Charge</th><th>Shown at</th><th class="num">Amount</th></tr></thead><tbody>${rows}
    <tr class="total"><td>Real total</td><td></td><td class="num">${money(ev.total)}</td></tr></tbody></table></section>`;
}

function replayPanel(frames, findings) {
  const timer = (findings || []).find((f) => /timer|countdown/.test(f.type));
  if (!frames?.length) {
    return `<section class="panel" id="replay"><div class="panel-head"><h2>Timer replay</h2><span class="pill ${timer ? 'caution' : 'unknown'}">${timer ? 'Watching' : 'No timer'}</span></div>
      <div class="empty-note">${timer ? esc(timer.summary) : 'No countdown timer on this page.'}</div></section>`;
  }
  const cells = frames.map((f) => `<div class="frame"><div class="t">Visit ${f.visit} · ${esc(f.time)}</div><div class="v">${esc(f.shown)}</div><div class="t">"Deal ends in"</div></div>`).join('');
  return `<section class="panel" id="replay"><div class="panel-head"><h2>Timer replay</h2><span class="pill bad">Resets every visit</span></div>
    <div class="frames">${cells}</div><div class="note">Same "deadline" on every visit, so it isn't a real deadline.</div></section>`;
}

function storesPanel(stores, sql) {
  if (!stores?.length) return `<section class="panel" id="stores"><div class="panel-head"><h2>Other stores</h2><span class="pill unknown">Not connected</span></div>
    <div class="empty-note">The Price Agent searches trusted stores for the exact same item. It isn't connected yet.</div></section>`;
  const tone = { chosen: 'good', ok: 'unknown', rejected: 'bad', skipped: 'unknown' };
  const rows = stores.map((s) => `<tr><td><b>${esc(s.store)}</b></td><td>${esc(s.note)}</td><td class="num">${money(s.total)}</td>
    <td class="num"><span class="pill ${tone[s.status]}">${s.status === 'chosen' ? 'Best safe price' : s.status === 'rejected' ? 'Rejected' : s.status === 'skipped' ? 'Skipped' : 'OK'}</span></td></tr>`).join('');
  return `<section class="panel" id="stores"><div class="panel-head"><h2>Store comparison</h2></div>
    <table><thead><tr><th>Store</th><th>Why</th><th class="num">True total</th><th class="num"></th></tr></thead><tbody>${rows}</tbody></table>
    ${sqlBlock(sql, 'Zetaris query behind this comparison')}</section>`;
}

function trustPanel(findings) {
  const t = (findings || []).filter((f) => f.agent === 'trust');
  const rows = t.map((f) => `<tr><td>${esc(f.summary)}</td><td class="num"><span class="pill ${f.status === 'unverified' ? 'unknown' : 'bad'}">${esc(f.status)}</span></td></tr>`).join('');
  return `<section class="panel" id="whois"><div class="panel-head"><h2>Store checks</h2></div>
    ${t.length ? `<table><tbody>${rows}</tbody></table>` : '<div class="empty-note">No store checks yet.</div>'}</section>`;
}

function tracePanel(findings) {
  const rows = (findings || []).map((f) => `<div class="r"><span>${new Date(f.ts * 1000).toLocaleTimeString()}</span><b>${esc(f.agent)}</b>
    <span>${esc(f.status)}${f.challenged_by ? `, challenged by ${esc(f.challenged_by)}` : ''}</span><span>${esc(f.summary)}<br><span class="muted">${esc(f.model || '')}</span></span></div>`).join('');
  return `<section class="panel span2" id="trace"><div class="panel-head"><h2>Agent trace</h2><span class="pill info">${(findings || []).length} findings</span></div>
    <div class="trace">${rows || '<div class="empty-note">No findings.</div>'}</div></section>`;
}
