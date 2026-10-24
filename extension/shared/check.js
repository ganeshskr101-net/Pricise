// Shared check logic. The background service worker runs every check (one per tab + URL), so the side panel and the
// quick verdict card show the same result and a page is never checked twice. Service workers have no EventSource, so the
// SSE stream is read with fetch.
import { api, BACKEND, money } from './api.js';

export const SETTINGS_KEY = 'pricise:settings'; // { card: true|false, neverSites: [host, ...] }
const STREAM_TIMEOUT_MS = 120000; // the real Deal Agent's timer test can take ~45 s

// Runs one check against the local backend. Calls onStart(checkId), onFinding(finding) per finding, and resolves with
// { checkId, card } when the final card arrives. Rejects if the backend isn't running.
export async function runCheck(url, page, { onStart, onFinding } = {}) {
  await api.health();
  const { check_id: checkId } = await api.startCheck(url, page);
  onStart?.(checkId);
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), STREAM_TIMEOUT_MS);
  try {
    const res = await fetch(`${BACKEND}/check/${checkId}/events`, { signal: ctrl.signal, headers: { Accept: 'text/event-stream' } });
    if (!res.ok || !res.body) throw new Error(`/check/${checkId}/events returned ${res.status}`);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const data = buf.slice(0, i).split('\n').filter((l) => l.startsWith('data:')).map((l) => l.slice(5).trimStart()).join('\n');
        buf = buf.slice(i + 2);
        if (!data) continue;
        const msg = JSON.parse(data);
        if (msg.kind === 'finding') onFinding?.(msg.finding);
        if (msg.kind === 'final_card') { reader.cancel().catch(() => {}); return { checkId, card: msg.card }; }
      }
    }
    throw new Error('The check ended without a verdict');
  } finally {
    clearTimeout(timer);
  }
}

// Side panel: ask the background for this tab's check (it reuses a running or finished one unless force is set) and
// call onUpdate(snapshot) each time it changes. Snapshot: { checkId, findings, card, error }. Returns a stop function.
export function followCheck({ tabId, url, page, force }, onUpdate) {
  const listener = (msg) => {
    if (msg?.type === 'pricise:check-update' && msg.tabId === tabId && msg.url === url) onUpdate(msg.snapshot);
  };
  chrome.runtime.onMessage.addListener(listener);
  chrome.runtime.sendMessage({ type: 'pricise:check', tabId, url, page, force })
    .then((snap) => snap && onUpdate(snap))
    .catch((err) => onUpdate({ error: err?.message || String(err), findings: [], card: null }));
  return () => chrome.runtime.onMessage.removeListener(listener);
}

// What the quick verdict card on the page shows: the verdict line and ONE short reason, in the backend's own words.
// show is true only when there is a confirmed trick or a cheaper safe option; otherwise the card stays hidden.
const TRICK_TYPES = new Set(['fake_discount', 'resetting_timer', 'prechecked_addon', 'drip_fees']);

export function quickVerdict(card, findings = [], listedPrice = null) {
  const tricks = card.tricks || findings.filter((f) => TRICK_TYPES.has(f.type) && f.status === 'confirmed').length;
  const trick = tricks > 0;
  const totalText = card.subtitle?.match(/real total [^·]+/i)?.[0]?.trim();
  const realTotal = Number(totalText?.match(/\$([\d,]+(?:\.\d+)?)/)?.[1]?.replace(/,/g, '')) || listedPrice;
  const bp = card.best_price;
  const option = card.alt?.store ? { store: card.alt.store, total: card.alt.total }
    : bp?.status === 'good' && bp.store && bp.total != null ? { store: bp.store, total: bp.total } : null;
  const cheaper = option && realTotal != null && option.total < realTotal ? option : null;
  let reason = '';
  if (totalText) reason = totalText.replace(/^r/, 'R');
  else if (cheaper) reason = `Cheaper safe option: ${cheaper.store} · ${money(cheaper.total)}`;
  else if (trick) reason = `${tricks} trick${tricks === 1 ? '' : 's'} found on this page`;
  return { show: trick || !!cheaper, tone: card.tone || 'unknown', title: card.title, reason };
}

// Friendly text when a chat question fails (the API client aborts after 8 s).
export function chatErrorText(err) {
  if (err?.name === 'AbortError') return 'That took too long (over 8 seconds). Please try again.';
  if (err instanceof TypeError) return "I can't reach Pricise on this laptop right now. Is the backend running?";
  return "Sorry, I couldn't answer that just now. Please try again.";
}

export async function getSettings() {
  const s = (await chrome.storage.local.get(SETTINGS_KEY))[SETTINGS_KEY] || {};
  return { card: s.card !== false, neverSites: Array.isArray(s.neverSites) ? s.neverSites : [] };
}

export async function saveSettings(patch) {
  const next = { ...(await getSettings()), ...patch };
  await chrome.storage.local.set({ [SETTINGS_KEY]: next });
  return next;
}
