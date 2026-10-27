// Pricise side panel: reads the current page, asks the local backend to check it, and fills in live as findings stream in.
// Layout follows docs/mockups/ui-v2/panel.html: verdict block, three question sections, "Checked by", chat, footer.
import { api, money, esc } from '../shared/api.js';
import { chatErrorText, chatSuggestions, compareLinks, followCheck, getSettings, isCompareUrl, money2, PENDING, rowKey, saveSettings, summarize, todayize, TRICK_TYPES } from '../shared/check.js';
import { ICONS } from '../shared/icons.js';

const $ = (sel) => document.querySelector(sel);
const main = $('#main');
const AGENTS = [['trust', 'Trust'], ['deal', 'Deal'], ['price', 'Price'], ['skeptic', 'Skeptic']];
const SECTION_ICON = { safe: ICONS.store, honest: ICONS.tag, best: ICONS.target };

const PINNED_TAB = new URLSearchParams(location.search).get('tab'); // dev/testing: sidepanel/index.html?tab=<id>

let state = null; // { tab, url, page, checkId, findings, card, startedAt, stop, unticked, watching, chat, asking, status }

// ------------------------------------------------------------------ boot
$('#recheck').innerHTML = ICONS.refresh;
$('#ft-lock').innerHTML = ICONS.lock;
$('#ask-send').innerHTML = ICONS.send;
$('#recheck').addEventListener('click', () => runCheck(true));
$('#open-wishlist').addEventListener('click', (e) => { e.preventDefault(); chrome.tabs.create({ url: chrome.runtime.getURL('wishlist/index.html') }); });

chrome.tabs.onActivated.addListener(() => runCheck());
let urlTimer = null;
chrome.tabs.onUpdated.addListener((tabId, info, tab) => {
  if (!tab.active || state?.tab?.windowId !== tab.windowId) return;
  if (info.status === 'complete') runCheck();
  // Single-page shops change the URL without loading: give the new product a moment to render, then check it.
  else if (info.url) { clearTimeout(urlTimer); urlTimer = setTimeout(() => runCheck(), 1500); }
});
chrome.storage.onChanged.addListener((changes) => { if (changes['pricise:settings']) renderCardSetting(); });
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
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ['content/quick-card.js', 'content/content.js'] });
    return chrome.tabs.sendMessage(tab.id, { type: 'pricise:read' });
  }
}

async function runCheck(force = false) {
  const tab = await activeTab();
  if (!tab) return;
  if (!force && state && state.tab.id === tab.id && state.url === tab.url) return;
  state?.stop?.();
  state = { tab, url: tab.url, findings: [], card: null, startedAt: performance.now(), unticked: false, watching: false,
            chat: [], asking: false, status: 'checking' };
  renderCardSetting();
  renderThread();

  if (!/^https?:/.test(tab.url || '')) return renderIdle();

  try {
    await api.health();
  } catch (_) {
    return renderOffline();
  }

  render();
  try {
    const mine = state;
    // The background runs the check (shared with the quick card). If it already has one for this page, join it straight
    // away instead of reading the page again (that read alone takes ~1.5 s: it watches the countdown tick).
    const onUpdate = async (snap) => {
      if (mine !== state) return;
      if (snap.missing) {
        mine.stop?.();
        let page;
        try { page = await readPage(tab); } catch (err) { if (mine === state) renderError(err); return; }
        if (mine !== state) return;
        state.page = page;
        state.stop = followCheck({ tabId: tab.id, url: tab.url, page, force }, onUpdate);
        return;
      }
      state.page ??= snap.page;
      if (snap.error && !state.card) return renderOffline();
      if ((snap.seq ?? 0) < (state.seq ?? -1)) return; // an older update arrived late
      state.seq = snap.seq ?? 0;
      state.checkId = snap.checkId;
      state.findings = state.unticked ? snap.findings.filter((x) => x.type !== 'prechecked_addon') : [...snap.findings];
      // The verdict shows as soon as the fast checks finish and firms up as proof lands; "Checked in" waits for the last one.
      if (snap.card) { state.card = snap.card; setBadge(); }
      if (snap.final && state.card) state.status = 'done';
      render();
      highlightOnPage();
    };
    state.stop = force
      ? followCheck({ tabId: tab.id, url: tab.url, page: (state.page = await readPage(tab)), force }, onUpdate)
      : followCheck({ tabId: tab.id, url: tab.url, page: null, force }, onUpdate);
  } catch (err) {
    renderError(err);
  }
}

function renderMeta() {
  let host = '';
  try { host = new URL(state?.url || '').host; } catch (_) { /* not a web page */ }
  const secs = state?.card?.elapsed_s ?? ((performance.now() - (state?.startedAt || 0)) / 1000).toFixed(1);
  const status = {
    checking: 'Checking…', done: `Checked in ${secs}s`, idle: 'Waiting for a product page',
    offline: 'Backend not running', error: "Couldn't check this page",
  }[state?.status] || '';
  const parts = [host && `<span class="host">${esc(host)}</span>`, status && `<span>${esc(status)}</span>`,
    ['checking', 'done'].includes(state?.status) ? '<span>4 agents</span>' : ''].filter(Boolean);
  $('#meta').innerHTML = parts.join('<span class="d"></span>');
}

// ------------------------------------------------------------------ page outlines + badge
function marksFromFindings() {
  const marks = [];
  for (const f of state.findings) {
    for (const e of f.evidence || []) {
      if (e.kind === 'selector' && e.label != null) {
        // Red only for tricks; a discount that checked out (real vs launch price, or by price history) is outlined grey.
        const tone = f.type === 'claimed_discount' && f.status === 'confirmed' ? 'neutral'
          : f.status === 'confirmed' || TRICK_TYPES.has(f.type) ? 'bad' : 'caution';
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
  const addon = f.find((x) => x.type === 'prechecked_addon');
  const addonAmt = addon ? (addon.summary.match(/\$\d+(?:\.\d{2})?/) || [''])[0] : '';
  const bp = card?.best_price;
  const parts = [verdictHtml(card)];

  parts.push(sectionHtml({
    id: 'safe', title: 'Shop safe?', verdict: card?.safe, live: by('trust').map(liveRow),
  }));
  parts.push(sectionHtml({
    id: 'honest', title: 'Honest deal?', verdict: card?.honest, live: by('deal').map(liveRow),
    actions: card ? [
      addon && !state.unticked ? `<button class="btn primary" data-act="untick">Untick ${esc(addonAmt)} add-on</button>` : '',
      state.unticked ? `<button class="btn" disabled>${ICONS.check}Add-on unticked</button>` : '',
      `<button class="btn" data-act="evidence" data-view="overview">All evidence</button>`,
    ].join('') : '',
  }));
  parts.push(sectionHtml({
    id: 'best', title: 'Best safe price', verdict: bp, live: [...by('page'), ...by('price'), ...by('skeptic')].map(liveRow),
    actions: card ? [
      bp?.url ? `<button class="btn" data-act="go" data-url="${esc(bp.url)}">Go to ${esc(bp.store)}</button>` : '',
      `<button class="btn" data-act="watch" ${state.watching ? 'disabled' : ''}>${state.watching ? `${ICONS.check}Watching` : `${ICONS.star}Watch price`}</button>`,
      compareHtml(),
      bp?.url ? '' : `<button class="btn" data-act="evidence" data-view="stores">See why</button>`,
    ].join('') : '',
  }));
  parts.push(agentsHtml());
  main.innerHTML = parts.join('');
  renderMeta();
  wire();
  updateAsk();
}

// "Compare prices": Google Shopping for this exact item, plus small links to the same search on other stores (new tabs).
function compareHtml() {
  const links = compareLinks(state.page?.product);
  if (!links.length) return '';
  const [main, ...rest] = links;
  return `<button class="btn" data-act="compare" data-url="${esc(main.url)}">Compare prices</button>`
    + `<span class="cmp">${rest.map((l) => `<a href="${esc(l.url)}" target="_blank" rel="noopener noreferrer">${esc(l.label)}</a>`).join(' · ')}</span>`;
}

function verdictHtml(card) {
  if (!card) {
    return `<section class="verdict pending" aria-busy="true"><div class="lbl">Verdict</div>
      <span class="skeleton sk big"></span><span class="skeleton sk" style="width:80%"></span><div class="skeleton sk-stats"></div></section>`;
  }
  const s = summarize(card, state.findings, state.page);
  let total = s.realTotal;
  if (state.unticked && total != null) { // the user removed the add-on: take it off the real total
    const amt = Number(((state.untickedSummary || '').match(/\$(\d+(?:\.\d{2})?)/) || [])[1]);
    if (amt) total = Math.round((total - amt) * 100) / 100;
  }
  const struck = s.listed != null && total != null && total > s.listed + 0.005 ? `<small>${money(s.listed)}</small>` : '';
  // The highlighted row: a credible store at least $1 cheaper ("Cheaper at Best Buy · $229.99 (save $20) · Go"), or the Store
  // Lab safer choice. A real page that is already the best price gets a small "Best price: this page" note instead.
  const a1 = s.safer?.cheaper
    ? `Cheaper at ${esc(s.safer.store)} · ${money2(s.safer.total)}${s.safer.save ? ` (save ${money(s.safer.save)})` : ''}`
    : s.safer ? `${esc(s.safer.store)} · ${money2(s.safer.total)}` : '';
  const a2 = s.safer?.caveat ? s.safer.note : s.safer?.cheaper ? 'Same item, new · trusted store' : s.safer?.note || '';
  const alt = s.safer ? `
    <div class="alt"><div class="ic">${ICONS.shield}</div>
      <div class="tx"><div class="a1">${a1}</div><div class="a2">${esc(a2)}</div></div>
      ${s.safer.url ? `<button class="go" data-act="${s.safer.cheaper ? 'open' : 'go'}" data-url="${esc(s.safer.url)}">${s.safer.cheaper ? 'Go' : 'View'}${ICONS.chevron}</button>` : ''}</div>`
    : s.bestHere ? '<div class="bp-note">Best price: this page</div>' : '';
  const tone = s.tone === 'bad' ? 'bad' : card.tone; // confirmed tricks are red here too, matching the quick card
  return `<section class="verdict ${esc(tone)}"><div class="lbl">Verdict</div>
    <div class="vt">${esc(card.title)}</div><div class="vs">${esc(verdictReason(card, s))}</div>
    <div class="stats">
      <div class="stat"><div class="k">Real total</div><div class="v">${total != null ? money2(total) : '—'}${struck}</div></div>
      <div class="stat"><div class="k">Tricks</div><div class="v ${s.tricks ? 'bad' : 'good'}">${s.tricks}</div></div>
      <div class="stat"><div class="k">Store risk</div><div class="v ${s.storeTone}">${esc(s.storeRisk)}</div></div>
    </div>${alt}</section>`;
}

function verdictReason(card, s) {
  const tricks = s.tricks ? `${s.tricks} trick${s.tricks === 1 ? '' : 's'} on this page` : '';
  if (s.risky && tricks) return `Risky store and ${tricks}.`;
  if (s.risky) return 'Risky store, though no tricks were found on this page.';
  if (tricks) return `${tricks.charAt(0).toUpperCase()}${tricks.slice(1)}.`;
  if (s.soft.length) return "Some claims on this page can't be verified yet.";
  return card.safe?.status === 'unknown' ? "No tricks found; store checks aren't connected yet." : 'No tricks found on this page.';
}

function sectionHtml({ id, title, verdict, live, actions = '' }) {
  const status = verdict
    ? `<span class="st ${esc(verdict.status)}">${esc(verdict.headline)}</span>`
    : '<span class="st pending">Checking…</span>';
  let rows;
  if (verdict) {
    rows = verdict.reasons.map((r) => `<li><span class="m ${esc(r.tone)}"></span><span class="tx">${esc(todayize(r.text))}</span>
      ${r.proof ? `<a href="#" class="pf" data-act="evidence" data-view="${esc(r.proof)}" data-row="${esc(rowKey(r.text))}">${proofLabel(r.proof)}</a>` : ''}</li>`).join('');
  } else if (live.length) {
    rows = live.join('');
  } else {
    rows = '<li aria-hidden="true"><span class="skeleton sk" style="width:86%"></span></li><li aria-hidden="true"><span class="skeleton sk" style="width:62%"></span></li>';
  }
  return `<section class="sec" id="q-${id}"${verdict ? '' : ' aria-busy="true"'}>
    <div class="sh"><span class="ico">${SECTION_ICON[id]}</span><span class="ttl">${title}</span>${status}</div>
    <ul class="rows">${rows}</ul>${actions ? `<div class="acts">${actions}</div>` : ''}</section>`;
}

function liveRow(f) {
  if (f.model === PENDING) return `<li class="live"><span class="m"></span><span class="tx">${esc(f.summary)}</span></li>`;
  if (f.type === 'offers_filtered') { // "Rejected 39 offers by rule: ...": one line here, the list on the evidence page
    return `<li class="live"><span class="m unknown"></span><span class="tx">${esc(f.summary)}</span>
      <a href="#" class="pf" data-act="evidence" data-view="stores">List</a></li>`;
  }
  const bySkeptic = f.challenged_by === 'skeptic' && f.agent !== 'skeptic';  // the Skeptic's decision on another agent's finding
  const tone = f.status === 'rejected' ? 'caution' : f.status === 'unverified' ? 'unknown'
    : f.agent === 'page' ? 'unknown' : f.status === 'confirmed' && (f.agent === 'skeptic' || bySkeptic) ? 'good'
    : TRICK_TYPES.has(f.type) || f.agent === 'trust' ? 'bad' : 'caution';
  const tag = bySkeptic && f.status === 'rejected' ? 'Rejected' : bySkeptic && f.status === 'confirmed' ? 'Approved'
    : f.status === 'rejected' ? 'Skipped' : f.status === 'proposed' || f.status === 'challenged' ? 'Checking' : '';
  return `<li class="live"><span class="m ${tone}"></span><span class="tx">${tag ? `<span class="tag">${tag}</span>` : ''}${esc(todayize(f.summary))}</span></li>`;
}

function proofLabel(view) {
  return { chart: 'Chart', replay: 'Replay', fees: 'Fees', whois: 'Proof', stores: 'Compare' }[view] || 'Proof';
}

function agentsHtml() {
  const f = state.findings;
  const challenges = f.filter((x) => x.challenged_by).length;
  const last = f[f.length - 1];
  const active = !state.card && last ? (last.challenged_by || last.agent) : null;
  const items = AGENTS.map(([key, name]) => {
    const extra = key === 'skeptic' && challenges ? ` · ${challenges} challenge${challenges === 1 ? '' : 's'}` : '';
    const cls = ['ag', active === key ? 'active' : '', key === 'skeptic' && challenges ? 'challenged' : ''].filter(Boolean).join(' ');
    return `<span class="${cls}"><i></i>${name}${extra}</span>`;
  }).join('');
  return `<section class="agents">Checked by ${items}</section>`;
}

function renderIdle() {
  $('#ask').hidden = true;
  state.status = 'idle';
  renderMeta();
  main.innerHTML = `<div class="empty"><img class="logo" src="../icons/logo.svg" alt="">
    <h2>Open a product page</h2><p>Pricise checks the page you're on: is the shop safe, is the deal honest, and is there a better safe price.</p></div>`;
}

function renderOffline() {
  $('#ask').hidden = true;
  state.status = 'offline';
  renderMeta();
  main.innerHTML = `<div class="empty"><h2>Start the local backend</h2>
    <p>Pricise checks pages on this laptop. In the repo folder, run:</p>
    <p><code>python -m uvicorn backend.stub_server:app --port 8787</code></p>
    <button class="btn primary" id="retry">Check again</button></div>`;
  $('#retry').addEventListener('click', () => runCheck(true));
}

function renderError(err) {
  $('#ask').hidden = true;
  state.status = 'error';
  renderMeta();
  main.innerHTML = `<div class="empty"><h2>Couldn't read this page</h2><p class="muted">${esc(err?.message || err)}</p>
    <button class="btn primary" id="retry">Check again</button></div>`;
  $('#retry').addEventListener('click', () => runCheck(true));
}

// ------------------------------------------------------------------ ask about this page
// Answers come from POST /chat, which only uses this check's stored results. The thread lives outside <main>, so live
// re-renders of the check don't wipe it; it resets when the panel moves to another page or re-checks.
// Chips follow the page (chatSuggestions in shared/check.js): a question its product text can answer, then deal and price.

$('#ask-form').addEventListener('submit', (e) => { e.preventDefault(); ask($('#ask-input').value); });
$('#ask-input').addEventListener('input', updateAsk);

function updateAsk() {
  const ready = !!(state?.checkId && state.card);
  $('#ask').hidden = !state?.checkId;
  $('#ask-input').disabled = !ready;
  $('#ask-send').disabled = !ready || state.asking || !$('#ask-input').value.trim();
  $('#ask-chips').querySelectorAll('button').forEach((b) => { b.disabled = !ready || state.asking; });
  $('#ask-note').textContent = ready ? '' : 'You can ask once the check finishes.';
}

function renderThread() {
  const chips = $('#ask-chips');
  chips.innerHTML = state?.chat.length ? '' : chatSuggestions(state?.page, state?.card)
    .map((q) => `<button class="chip" type="button">${esc(q)}</button>`).join('');
  chips.querySelectorAll('button').forEach((b) => b.addEventListener('click', () => ask(b.textContent)));
  const seen = state?.chatShown || 0; // only messages added since the last draw get the fade-in
  $('#ask-thread').innerHTML = (state?.chat || []).map((m, i) => {
    const cls = (m.role === 'user' ? 'user' : `bot${m.pending ? ' pending' : ''}${m.error ? ' error' : ''}`) + (i >= seen ? ' new' : '');
    const src = m.sources?.length ? `<span class="src">From: ${esc(m.sources.map((t) => t.replace(/_/g, ' ')).join(', '))}</span>` : '';
    const links = (m.links || []).filter((l) => isCompareUrl(l.url))
      .map((l) => `<a href="${esc(l.url)}" target="_blank" rel="noopener noreferrer">${esc(l.label)}</a>`).join(' · ');
    return `<div class="msg ${cls}">${esc(m.text)}${links ? `<span class="links">${links}</span>` : ''}${src}</div>`;
  }).join('');
  if (state) state.chatShown = state.chat.length;
  updateAsk();
}

async function ask(text) {
  const question = String(text || '').trim().slice(0, 300);
  if (!question || !state?.checkId || !state.card || state.asking) return;
  const mine = state;
  const history = mine.chat.filter((m) => !m.pending && !m.error).slice(-6).map((m) => ({ role: m.role, content: m.text }));
  const reply = { role: 'assistant', text: 'Checking…', pending: true };
  mine.chat.push({ role: 'user', text: question }, reply);
  mine.asking = true;
  $('#ask-input').value = '';
  renderThread();
  $('#ask').scrollIntoView({ block: 'end', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  try {
    const res = await api.chat(mine.checkId, question, history);
    Object.assign(reply, { text: res.answer, sources: res.sources || [], links: res.links || [], pending: false });
  } catch (err) {
    Object.assign(reply, { text: chatErrorText(err), pending: false, error: true });
  }
  mine.asking = false;
  if (mine !== state) return; // the panel moved to another page meanwhile
  renderThread();
  $('#ask').scrollIntoView({ block: 'end' });
  $('#ask-input').focus();
}

// ------------------------------------------------------------------ quick card setting (footer)
async function renderCardSetting() {
  const el = $('#card-setting');
  const { card, neverSites } = await getSettings();
  let host = '';
  try { host = new URL(state?.url || '').host; } catch (_) { /* not a web page */ }
  const offHere = host && neverSites.includes(host);
  el.textContent = !card ? 'Quick card: off' : offHere ? 'Quick card: off on this site' : 'Quick card: on';
  el.title = offHere ? 'Show the quick card on this site again' : card ? 'Stop showing the quick card on pages' : 'Show the quick card on product pages';
  el.onclick = async (e) => {
    e.preventDefault();
    if (card && offHere) await saveSettings({ neverSites: neverSites.filter((h) => h !== host) });
    else await saveSettings({ card: !card });
    renderCardSetting();
  };
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
    // The evidence page opens on that tab and highlights the row behind this exact line (see evidence/sections.js resolve()).
    const q = new URLSearchParams({ check: state.checkId, tab: el.dataset.view || 'overview', ...(el.dataset.row ? { row: el.dataset.row } : {}) });
    const url = chrome.runtime.getURL(`evidence/index.html?${q}`);
    chrome.tabs.create({ url });
  } else if (act === 'open') {
    // A cheaper credible store's listing: a new tab next to this one (the shopper keeps this page).
    if (/^https:\/\//.test(el.dataset.url)) chrome.tabs.create({ url: el.dataset.url, index: state.tab.index + 1 });
  } else if (act === 'compare') {
    if (isCompareUrl(el.dataset.url)) chrome.tabs.create({ url: el.dataset.url, index: state.tab.index + 1 });
  } else if (act === 'go') {
    chrome.tabs.update(state.tab.id, { url: el.dataset.url });
  } else if (act === 'untick') {
    const addon = state.findings.find((x) => x.type === 'prechecked_addon');
    const sel = addon?.evidence?.find((x) => x.kind === 'selector')?.ref;
    await chrome.tabs.sendMessage(state.tab.id, { type: 'pricise:untick', selector: sel }).catch(() => {});
    await api.untick(state.checkId, sel).catch(() => {});
    state.unticked = true;
    state.untickedSummary = addon?.summary;
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
