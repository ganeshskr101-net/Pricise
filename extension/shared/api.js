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
  startCheck: (url, page, fresh = false) => call(`/check${fresh ? '?fresh=1' : ''}`, { method: 'POST', body: JSON.stringify({ url, page }) }),
  getCheck: (id) => call(`/check/${id}`),
  chat: (check_id, question, history) => call('/chat', { method: 'POST', body: JSON.stringify({ check_id, question, history }) }),
  untick: (check_id, selector, amount) => call('/action/untick', { method: 'POST', body: JSON.stringify({ check_id, selector, amount }) }),
  wishlist: () => call('/wishlist'),
  addWishlist: (item) => call('/wishlist', { method: 'POST', body: JSON.stringify(item) }),
  clock: (day) => call('/demo/clock', { method: 'POST', body: JSON.stringify({ day }) }),
};

export const money = (v) => (v == null ? '—' : `$${Number(v).toLocaleString('en-US', { minimumFractionDigits: Number.isInteger(v) ? 0 : 2, maximumFractionDigits: 2 })}`);

export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

