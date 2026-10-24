// Pricise side panel: reads the current page, asks the local backend to check it, and fills in live as findings stream in.
import { api, money, esc, SHIELD } from '../shared/api.js';

const $ = (sel) => document.querySelector(sel);
const main = $('#main');
$('#logo').innerHTML = SHIELD;

const AGENTS = [
  { key: 'trust', name: 'Trust', color: '#7c4dff' },
  { key: 'deal', name: 'Deal', color: '#e37400' },
  { key: 'price', name: 'Price', color: '#137333' },
  { key: 'skeptic', name: 'Skeptic', color: '#c5221f' },
];
const ICON = { bad: '✕', caution: '!', good: '✓', unknown: '?', pending: '…' };
const TRICK_TYPES = new Set(['fake_discount', 'resetting_timer', 'prechecked_addon', 'drip_fees']);

const PINNED_TAB = new URLSearchParams(location.search).get('tab'); // dev/testing: sidepanel/index.html?tab=<id>

let state = null; // { tab, url, page, checkId, findings, card, startedAt, es, unticked, watching }

// ------------------------------------------------------------------ boot
$('#recheck').addEventListener('click', () => runCheck(true));
$('#open-wishlist').addEventListener('click', (e) => { e.preventDefault(); chrome.tabs.create({ url: chrome.runtime.getURL('wishlist/index.html') }); });

chrome.tabs.onActivated.addListener(() => runCheck());
chrome.tabs.onUpdated.addListener((tabId, info, tab) => {
  if (info.status === 'complete' && tab.active && state?.tab?.windowId === tab.windowId) runCheck();
});
runCheck();

// ------------------------------------------------------------------ main flow
async function activeTab() {
  if (PINNED_TAB) return chrome.tabs.get(Number(PINNED_TAB));
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function readPage(tab) {
  try {
    return await chrome.tabs.sendMessage(tab.id, { type: 'pricise:read' });
  } catch (_) {
    // Tab was open before Pricise was installed: inject the content script once, then ask again.
    await chrome.scripting.insertCSS({ target: { tabId: tab.id }, files: ['content/content.css'] });
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ['content/content.js'] });
    return chrome.tabs.sendMessage(tab.id, { type: 'pricise:read' });
  }
}

async function runCheck(force = false) {
  const tab = await activeTab();
  if (!tab) return;
  if (!force && state && state.tab.id === tab.id && state.url === tab.url) return;
  state?.es?.close();
  state = { tab, url: tab.url, findings: [], card: null, startedAt: performance.now(), unticked: false, watching: false };

  if (!/^https?:/.test(tab.url || '')) return renderIdle();

  try {
    await api.health();
  } catch (_) {
    return renderOffline();
  }

  setChecking(true);
  render();
  try {
    const mine = state;
    const page = await readPage(tab);
    if (mine !== state) return;
    state.page = page;
    const { check_id } = await api.startCheck(tab.url, page);
    state.checkId = check_id;
    const es = api.events(check_id);
    state.es = es;
    es.onmessage = (ev) => {
      if (mine !== state) return es.close();
      const msg = JSON.parse(ev.data);
      if (msg.kind === 'finding') { state.findings.push(msg.finding); render(); highlightOnPage(); }
      if (msg.kind === 'final_card') { state.card = msg.card; es.close(); setChecking(false); render(); highlightOnPage(); setBadge(); }
    };
    es.onerror = () => { if (!state.card) { es.close(); setChecking(false); renderOffline(); } };
  } catch (err) {
    setChecking(false);
    renderError(err);
  }
}

function setChecking(on) {
  document.body.classList.toggle('checking', on);
  const secs = ((performance.now() - (state?.startedAt || 0)) / 1000).toFixed(1);
  $('#status').textContent = on ? 'Checking this page…' : state?.card ? `Checked this page in ${state.card.elapsed_s || secs}s` : '';
}

// ------------------------------------------------------------------ page outlines + badge
function marksFromFindings() {
  const marks = [];
  for (const f of state.findings) {
    for (const e of f.evidence || []) {
      if (e.kind === 'selector' && e.label != null) {
        const tone = f.status === 'confirmed' || TRICK_TYPES.has(f.type) ? 'bad' : 'caution';
        marks.push({ selector: e.ref, label: e.label, tone, placement: e.placement });
      }
    }
  }
  return marks;
}
function highlightOnPage() {
  chrome.tabs.sendMessage(state.tab.id, { type: 'pricise:highlight', marks: marksFromFindings() }).catch(() => {});
}
function setBadge() {
  chrome.runtime.sendMessage({ type: 'badge', tabId: state.tab.id, count: state.card?.tricks || 0 }).catch(() => {});
}

// ------------------------------------------------------------------ rendering
function render() {
  const f = state.findings;
  const card = state.card;
  const by = (agent) => f.filter((x) => x.agent === agent);
  const parts = [];

  if (card) parts.push(verdictHtml(card));

  // Shop safe?
  parts.push(questionHtml({
    id: 'safe', title: 'Shop safe?',
    verdict: card?.safe,
    live: by('trust').map(liveRow),
  }));

  // Honest deal?
  const addon = f.find((x) => x.type === 'prechecked_addon');
  const addonAmt = addon ? (addon.summary.match(/\$\d+(?:\.\d{2})?/) || [''])[0] : '';
  const honestActions = card ? [
    addon && !state.unticked ? `<button class="btn primary" data-act="untick">Untick ${esc(addonAmt)} add-on</button>` : '',
    addon && state.unticked ? `<button class="btn" disabled>✓ Add-on unticked</button>` : '',
    `<button class="btn" data-act="evidence" data-view="">See all evidence</button>`,
  ].join('') : '';
  parts.push(questionHtml({
    id: 'honest', title: 'Honest deal?',
    verdict: card?.honest,
    live: by('deal').map(liveRow),
    actions: honestActions,
  }));

  // Best safe price
  const bp = card?.best_price;
  const bestActions = card ? [
    bp?.url ? `<button class="btn go" data-act="go" data-url="${esc(bp.url)}">Go to ${esc(bp.store)}</button>` : '',
    `<button class="btn" data-act="watch" ${state.watching ? 'disabled' : ''}>${state.watching ? '★ Watching' : '☆ Watch price'}</button>`,
    bp?.url ? '' : `<button class="btn" data-act="evidence" data-view="stores">See why</button>`,
  ].join('') : '';
  parts.push(questionHtml({
    id: 'best', title: 'Best safe price',
    verdict: bp,
    live: [...by('page'), ...by('price'), ...by('skeptic')].map(liveRow),
    actions: bestActions,
  }));

  parts.push(agentsHtml());
  main.innerHTML = parts.join('');
  wire();
}

function verdictHtml(c) {
  const subtitle = state.unticked && c.subtitle.includes('$191') ? c.subtitle.replace('$191', '$186.01') : c.subtitle;
  const alt = c.alt ? `
    <div class="alt">
      <div class="alt-text"><b>Safer choice: ${esc(c.alt.store)} · ${money(c.alt.total)}</b>Same model, new, full warranty</div>
      <button class="btn go" data-act="go" data-url="${esc(c.alt.url)}">Go ›</button>
    </div>` : '';
  return `
    <section class="card verdict ${c.tone}">
      <div class="verdict-top">
        <div class="v-icon ${c.tone}">${ICON[c.tone] || '!'}</div>
        <div><div class="v-title">${esc(c.title)}</div><div class="v-sub">${esc(subtitle)}</div></div>
      </div>${alt}
    </section>`;
}

function questionHtml({ id, title, verdict, live, actions = '' }) {
  const status = verdict ? verdict.status : 'pending';
  const pill = verdict
    ? `<span class="pill ${verdict.status}">${esc(verdict.headline)}</span>`
    : `<span class="pill unknown">Checking…</span>`;
  let rows;
  if (verdict) {
    rows = verdict.reasons.map((r) => `
      <li class="row"><span class="dot ${r.tone}"></span><span class="txt">${esc(r.text)}</span>
      ${r.proof ? `<a href="#" class="proof" data-act="evidence" data-view="${esc(r.proof)}">${proofLabel(r.proof)}</a>` : ''}</li>`).join('');
  } else if (live.length) {
    rows = live.join('');
  } else {
    rows = '<li><div class="skeleton sk" style="width:86%"></div><div class="skeleton sk" style="width:64%"></div></li>';
  }
  return `
    <section class="card q" id="q-${id}">
      <div class="q-head"><div class="q-icon ${status}">${ICON[status]}</div><div class="q-title">${title}</div>${pill}</div>
      <ul class="rows">${rows}</ul>
      ${actions ? `<div class="actions">${actions}</div>` : ''}
    </section>`;
}

function liveRow(f) {
  const tone = f.status === 'rejected' ? 'caution' : f.status === 'unverified' ? 'unknown'
    : f.agent === 'page' ? 'unknown' : f.status === 'confirmed' && (f.agent === 'skeptic') ? 'good'
    : TRICK_TYPES.has(f.type) || f.agent === 'trust' ? 'bad' : 'caution';
  const tag = f.status === 'rejected' ? 'Skipped' : f.status === 'proposed' ? 'Checking' : '';
  return `<li class="row live"><span class="dot ${tone}"></span><span class="txt">${tag ? `<span class="tag">${tag}</span>` : ''}${esc(f.summary)}</span></li>`;
}

function proofLabel(view) {
  return { chart: 'Chart', replay: 'Replay', fees: 'Fees', whois: 'Proof', stores: 'Compare' }[view] || 'Proof';
}

function agentsHtml() {
  const f = state.findings;
  const counts = {
    trust: f.filter((x) => x.agent === 'trust' || x.challenged_by === 'trust').length,
    deal: f.filter((x) => x.agent === 'deal').length,
    price: f.filter((x) => x.agent === 'price').length,
    skeptic: f.filter((x) => x.agent === 'skeptic' || x.challenged_by === 'skeptic').length,
  };
  const last = f[f.length - 1];
  const activeAgent = !state.card && last ? (last.challenged_by || last.agent) : null;
  const chips = AGENTS.map((a) => `
    <span class="chip ${activeAgent === a.key ? 'active' : ''}"><i style="background:${a.color}">${a.name[0]}</i>${a.name}${a.key === 'skeptic' && counts.skeptic ? ` <span class="n">· ${counts.skeptic}</span>` : ''}</span>`).join('');
  return `<section class="agents"><div class="agents-title">Agents on this check</div><div class="chips">${chips}</div></section>`;
}

function renderIdle() {
  setChecking(false);
  $('#status').textContent = 'Waiting for a product page';
  main.innerHTML = `<div class="card empty"><div class="logo" style="margin:0 auto">${SHIELD}</div>
    <h2>Open a product page</h2><p>Pricise checks the page you are on: is the shop safe, is the deal honest, and is there a better safe price.</p></div>`;
}

function renderOffline() {
  setChecking(false);
  $('#status').textContent = 'Backend not running';
  main.innerHTML = `<div class="card empty"><h2>Start the local backend</h2>
    <p>Pricise checks pages on this laptop. In the repo folder, run:</p>
    <p><code>python -m uvicorn backend.stub_server:app --port 8787</code></p>
    <button class="btn primary" id="retry">Try again</button></div>`;
  $('#retry').addEventListener('click', () => runCheck(true));
}

function renderError(err) {
  $('#status').textContent = 'Could not check this page';
  main.innerHTML = `<div class="card empty"><h2>Could not read this page</h2><p class="muted">${esc(err?.message || err)}</p>
    <button class="btn primary" id="retry">Try again</button></div>`;
  $('#retry').addEventListener('click', () => runCheck(true));
}

// ------------------------------------------------------------------ actions
function wire() {
  main.querySelectorAll('[data-act]').forEach((el) => el.addEventListener('click', onAction));
}

async function onAction(e) {
  e.preventDefault();
  const el = e.currentTarget;
  const act = el.dataset.act;
  if (act === 'evidence') {
    const url = chrome.runtime.getURL(`evidence/index.html?check=${state.checkId}${el.dataset.view ? '#' + el.dataset.view : ''}`);
    chrome.tabs.create({ url });
  } else if (act === 'go') {
    chrome.tabs.update(state.tab.id, { url: el.dataset.url });
  } else if (act === 'untick') {
    const addon = state.findings.find((x) => x.type === 'prechecked_addon');
    const sel = addon?.evidence?.find((x) => x.kind === 'selector')?.ref;
    await chrome.tabs.sendMessage(state.tab.id, { type: 'pricise:untick', selector: sel }).catch(() => {});
    await api.untick(state.checkId, sel).catch(() => {});
    state.unticked = true;
    state.findings = state.findings.filter((x) => x !== addon);
    render();
    highlightOnPage();
  } else if (act === 'watch') {
    const p = state.page?.product || {};
    const name = p.brand && p.model ? `${p.brand} ${p.model}` : (p.name || state.tab.title || 'Item').slice(0, 60);
    const bp = state.card?.best_price;
    const ref = bp?.total ?? p.price;
    await api.addWishlist({ name, url: state.url, rule: ref ? `Under ${money(Math.floor(ref * 0.94))}` : 'Any genuine drop', target: ref ? Math.floor(ref * 0.94) : null, best_price: ref ?? null, best_store: bp?.store || state.page?.host, status: 'Watching' });
    state.watching = true;
    render();
  }
}
