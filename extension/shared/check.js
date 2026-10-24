// Shared check logic. The background service worker runs every check (one per tab + URL), so the side panel and the
// quick verdict card show the same result and a page is never checked twice. Service workers have no EventSource, so the
// SSE stream is read with fetch.
import { api, BACKEND, money } from './api.js';

export const SETTINGS_KEY = 'pricise:settings'; // { card: true|false, neverSites: [host, ...] }
const STREAM_TIMEOUT_MS = 120000; // the real Deal Agent's timer test can take ~45 s

// Runs one check against the local backend. Calls onStart(checkId), onFinding(finding) per finding and onCard(card) per
// verdict: the backend sends a first verdict as soon as the fast checks finish and an updated one each time slower proof
// lands (run-ahead, timer test). Resolves with { checkId, card } (the last verdict) when the stream ends. fresh re-runs
// everything instead of reusing recent results. Rejects if the backend isn't running.
export async function runCheck(url, page, { onStart, onFinding, onCard, fresh = false } = {}) {
  await api.health();
  const { check_id: checkId } = await api.startCheck(url, page, fresh);
  onStart?.(checkId);
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), STREAM_TIMEOUT_MS);
  try {
    const res = await fetch(`${BACKEND}/check/${checkId}/events`, { signal: ctrl.signal, headers: { Accept: 'text/event-stream' } });
    if (!res.ok || !res.body) throw new Error(`/check/${checkId}/events returned ${res.status}`);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let card = null;
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
        if (msg.kind === 'final_card') { card = msg.card; onCard?.(card); }
      }
    }
    if (!card) throw new Error('The check ended without a verdict');
    return { checkId, card };
  } finally {
    clearTimeout(timer);
  }
}

// Side panel: ask the background for this tab's check (it reuses a running or finished one unless force is set) and
// call onUpdate(snapshot) each time it changes. Snapshot: { checkId, page, findings, card, error, seq, final }.
// With page = null it only joins an existing check: the snapshot is { missing: true } if there is none.
// Returns a stop function.
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

// ------------------------------------------------------------------ what a finished check means
// One reading of a check, shared by the side panel (verdict block) and the quick card on the page, so they always agree.
export const TRICK_TYPES = new Set(['fake_discount', 'resetting_timer', 'prechecked_addon', 'drip_fees']);
export const PENDING = 'pending'; // Finding.model of a "still checking" placeholder row (see backend/agents/deal.py)
const SOFT_TYPES = new Set(['claimed_discount', 'countdown', 'scarcity', 'fake_discount']);
const AMOUNT = String.raw`(\d{1,3}(?:,\d{3})*(?:\.\d+)?)`;
const toNum = (s) => (s == null ? null : Number(String(s).replace(/,/g, '')));
export const money2 = (v) => (v == null ? '—' : `$${Number(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);

export function summarize(card, findings = [], page = null) {
  const confirmed = findings.filter((f) => TRICK_TYPES.has(f.type) && f.status === 'confirmed');
  const tricks = card ? card.tricks || 0 : new Set(confirmed.map((f) => f.type)).size;
  const m = card?.subtitle?.match(new RegExp(`real total \\$${AMOUNT}(?:, not \\$${AMOUNT})?`, 'i'));
  const listed = page?.product?.price ?? toNum(m?.[2]);
  const realTotal = m ? toNum(m[1]) : listed;
  const risk = { bad: ['High', 'bad'], caution: ['Medium', 'caution'], good: ['Low', 'good'] }[card?.safe?.status] || ['Not checked', 'unknown'];
  const bp = card?.best_price;
  let safer = null;
  if (card?.alt?.store) safer = { store: card.alt.store, total: card.alt.total, url: card.alt.url };
  else if (bp?.status === 'good' && bp.store && bp.total != null) safer = { store: bp.store, total: bp.total, url: bp.url };
  if (safer) {
    const detail = (bp?.reasons?.[0]?.text || '').replace(/^exact match:\s*/i, '');
    safer.note = ['Trusted store', detail].filter(Boolean).join(' · ');
  }
  const soft = findings.filter((f) => f.agent === 'deal' && f.model !== PENDING && SOFT_TYPES.has(f.type)
    && ['unverified', 'proposed'].includes(f.status));
  const tone = tricks > 0 ? 'bad' : soft.length ? 'caution' : 'good';
  return { tone, tricks, realTotal, listed, storeRisk: risk[0], storeTone: risk[1], risky: card?.safe?.status === 'bad', safer, soft, confirmed };
}

// The quick card: title plus one or two detail lines. Lines are [text, bold] segments so the card can render them safely.
// Wording: the safer option is introduced with "Safer:" only, never "save", because it can cost more than this page.
export function quickVerdict(card, findings = [], page = null) {
  const s = summarize(card, findings, page);
  const lines = [];
  if (s.tone === 'bad') {
    const title = `${s.tricks} trick${s.tricks === 1 ? '' : 's'} on this page`;
    if (s.realTotal != null && s.listed != null && s.realTotal > s.listed + 0.005) {
      lines.push([['Real total '], [money2(s.realTotal), true], [`, not ${money(s.listed)}${s.risky ? ' · risky store' : ''}`]]);
    } else {
      // No real total yet: name the first trick in the verdict's own words, so the card and the panel always agree.
      const first = (card.honest?.reasons || []).find((r) => r.tone === 'bad')?.text || 'Tricks found on this page';
      lines.push([[first + (s.risky ? ' · risky store' : '')]]);
    }
    if (s.safer) lines.push([['Safer: '], [`${s.safer.store} · ${money(s.safer.total)}`, true]]);
    return { tone: 'bad', title, lines };
  }
  if (s.tone === 'caution') {
    const price = page?.product?.price;
    const was = (page?.signals || []).filter((x) => x.type === 'claimed_discount' && x.amount && (price == null || x.amount > price))
      .map((x) => x.amount)[0];
    const parts = [...new Set(s.soft.map((f) => ({
      claimed_discount: was ? `"Was ${money(was)}" can't be verified yet` : "Discount claim can't be verified yet",
      fake_discount: 'Discount claim still being checked',
      countdown: 'countdown timer',
      scarcity: 'pressure message',
    }[f.type])))];
    const text = parts.join(' · ');
    lines.push([[text.charAt(0).toUpperCase() + text.slice(1)]]);
    if (s.safer) lines.push([['Safer: '], [`${s.safer.store} · ${money(s.safer.total)}`, true]]);
    return { tone: 'caution', title: 'Check before you buy', lines };
  }
  lines.push(s.listed != null ? [['Listed '], [money2(s.listed), true], [' · no extra fees found']] : [['No extra fees or pre-ticked add-ons found']]);
  if (s.safer) lines.push([['Safer: '], [`${s.safer.store} · ${money(s.safer.total)}`, true]]);
  return { tone: 'good', title: 'No tricks spotted', lines };
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
