// Pricise service worker: runs every page check (one per tab + URL, shared by the side panel and the quick verdict card),
// keeps the toolbar badge, opens the side panel, shows desktop notifications.
import { getSettings, quickVerdict, runCheck } from './shared/check.js';

const checks = new Map(); // tabId -> { url, page, checkId, findings, card, error, done, wantCard }

chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg?.type === 'badge') {
    setBadge(msg.tabId, msg.count);
    sendResponse({ ok: true });
  } else if (msg?.type === 'pricise:check') {
    // Side panel: reuse this tab's running or finished check for the same URL, unless it asked to check again.
    const cur = checks.get(msg.tabId);
    if (!msg.force && cur && cur.url === msg.url && !cur.error) sendResponse(snapshot(cur));
    else sendResponse(snapshot(start(msg.tabId, msg.url, msg.page, false)));
  } else if (msg?.type === 'pricise:auto-check' && sender.tab) {
    autoCheck(sender.tab, msg.page);
    sendResponse({ ok: true });
  } else if (msg?.type === 'pricise:open-panel' && sender.tab) {
    // Called straight from the card's click so Chrome can see the user gesture; if it still refuses, the card says so.
    chrome.sidePanel.open({ tabId: sender.tab.id }).then(() => sendResponse({ ok: true }), () => sendResponse({ ok: false }));
    return true;
  } else if (msg?.type === 'notify') {
    chrome.notifications.create(msg.id || `pricise-${Date.now()}`, {
      type: 'basic',
      iconUrl: chrome.runtime.getURL('icons/icon128.png'),
      title: msg.title || 'Pricise',
      message: msg.message || '',
      buttons: [{ title: 'See why' }],
      priority: 2,
    }, (id) => sendResponse({ ok: true, id }));
    return true;
  }
});

chrome.notifications.onClicked.addListener(() => {
  chrome.tabs.create({ url: chrome.runtime.getURL('wishlist/index.html') });
});
chrome.notifications.onButtonClicked.addListener(() => {
  chrome.tabs.create({ url: chrome.runtime.getURL('wishlist/index.html') });
});

// Clear the badge when a tab navigates to a new page; forget the old check when the URL changes.
chrome.tabs.onUpdated.addListener((tabId, info) => {
  if (info.status === 'loading') chrome.action.setBadgeText({ tabId, text: '' });
  if (info.url && checks.get(tabId)?.url !== info.url) checks.delete(tabId);
});
chrome.tabs.onRemoved.addListener((tabId) => checks.delete(tabId));

// ------------------------------------------------------------------ checks

function setBadge(tabId, count) {
  chrome.action.setBadgeText({ tabId, text: count > 0 ? String(count) : '' }).catch(() => {});
  chrome.action.setBadgeBackgroundColor({ tabId, color: count > 0 ? '#c5221f' : '#137333' }).catch(() => {});
}

function snapshot(c) {
  return { checkId: c.checkId, findings: c.findings, card: c.card, error: c.error };
}

function broadcast(tabId, c) {
  chrome.runtime.sendMessage({ type: 'pricise:check-update', tabId, url: c.url, snapshot: snapshot(c) }).catch(() => {}); // panel may be closed
}

let running = 0;
let keepAlive = null;

function start(tabId, url, page, wantCard) {
  const c = { url, page, checkId: null, findings: [], card: null, error: null, done: false, wantCard };
  checks.set(tabId, c);
  running += 1;
  // A check can take ~45 s with the real Deal Agent; calling an extension API keeps the service worker awake meanwhile.
  keepAlive ??= setInterval(() => chrome.runtime.getPlatformInfo(() => {}), 20000);
  runCheck(url, page, {
    onStart: (id) => { c.checkId = id; },
    onFinding: (f) => { if (checks.get(tabId) !== c) return; c.findings = [...c.findings, f]; broadcast(tabId, c); },
  }).then(({ card }) => {
    if (checks.get(tabId) !== c) return;
    c.card = card;
    setBadge(tabId, card.tricks || 0);
    broadcast(tabId, c);
    if (c.wantCard) showCard(tabId, c);
  }).catch((err) => {
    c.error = err?.message || String(err); // backend not running: the panel says so; the page shows nothing
    if (checks.get(tabId) === c) broadcast(tabId, c);
  }).finally(() => {
    c.done = true;
    running -= 1;
    if (!running) { clearInterval(keepAlive); keepAlive = null; }
  });
  return c;
}

async function autoCheck(tab, page) {
  const { neverSites } = await getSettings();
  if (neverSites.includes(new URL(tab.url).host)) return;
  const cur = checks.get(tab.id);
  if (cur && cur.url === tab.url && !cur.error) {
    cur.wantCard = true;
    if (cur.card) { setBadge(tab.id, cur.card.tricks || 0); showCard(tab.id, cur); }
    return;
  }
  start(tab.id, tab.url, page, true);
}

let cardCss = null;
async function loadCardCss() {
  // Shared tokens, with :root turned into :host so they apply inside the card's Shadow DOM.
  if (!cardCss) {
    const [tokens, card] = await Promise.all(['shared/tokens.css', 'content/quick-card.css']
      .map((p) => fetch(chrome.runtime.getURL(p)).then((r) => r.text())));
    cardCss = tokens.replace(/:root\b/g, ':host') + '\n' + card;
  }
  return cardCss;
}

async function showCard(tabId, c) {
  const { card: enabled, neverSites } = await getSettings();
  const host = new URL(c.url).host;
  if (!enabled || neverSites.includes(host)) return;
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  if (!tab || tab.url !== c.url) return; // the user has moved on
  const verdict = quickVerdict(c.card, c.findings, c.page?.product?.price ?? null);
  if (!verdict.show) return; // only for a confirmed trick or a cheaper safe option
  const data = { ...verdict, checkId: c.checkId, host, logo: chrome.runtime.getURL('icons/logo.svg'), css: await loadCardCss() };
  await chrome.tabs.sendMessage(tabId, { type: 'pricise:quick-card', data }).catch(() => {}); // every page load; × closes it for this view
}
