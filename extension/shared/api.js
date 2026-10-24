// Small client for the local Pricise backend. The extension holds no keys; everything goes to localhost.
export const BACKEND = 'http://localhost:8787';

async function call(path, opts = {}) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 8000);
  try {
    const res = await fetch(BACKEND + path, {
      ...opts,
      signal: ctrl.signal,
      headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    });
    if (!res.ok) throw new Error(`${path} returned ${res.status}`);
    return await res.json();
  } finally {
    clearTimeout(t);
  }
}

export const api = {
  health: () => call('/health'),
  startCheck: (url, page) => call('/check', { method: 'POST', body: JSON.stringify({ url, page }) }),
  getCheck: (id) => call(`/check/${id}`),
  events: (id) => new EventSource(`${BACKEND}/check/${id}/events`),
  untick: (check_id, selector, amount) => call('/action/untick', { method: 'POST', body: JSON.stringify({ check_id, selector, amount }) }),
  wishlist: () => call('/wishlist'),
  addWishlist: (item) => call('/wishlist', { method: 'POST', body: JSON.stringify(item) }),
  clock: (day) => call('/demo/clock', { method: 'POST', body: JSON.stringify({ day }) }),
};

export const money = (v) => (v == null ? '—' : `$${Number(v).toLocaleString('en-US', { minimumFractionDigits: Number.isInteger(v) ? 0 : 2, maximumFractionDigits: 2 })}`);

export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

export const SHIELD = '<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M9 12l2 2 4-4"/></svg>';
