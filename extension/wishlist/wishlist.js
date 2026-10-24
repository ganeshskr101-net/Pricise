// Wishlist page: items Pricise is watching, weekly stats, and the demo clock that fast-forwards time.
import { api, money, esc, SHIELD } from '../shared/api.js';

document.getElementById('logo').innerHTML = SHIELD;
document.getElementById('nav-evidence').addEventListener('click', (e) => { e.preventDefault(); history.back(); });
const DAYS = [1, 3, 5, 7, 9];
let flashId = null;

load();

async function load() {
  let data;
  try { data = await api.wishlist(); } catch (e) {
    document.getElementById('rows').innerHTML = `<tr><td colspan="4" class="muted">Backend not running: start it with <code>python -m uvicorn backend.stub_server:app --port 8787</code></td></tr>`;
    return;
  }
  renderDays(data.day);
  renderRows(data.items);
  renderStats(data.stats);
}

function renderDays(day) {
  const el = document.getElementById('days');
  el.innerHTML = DAYS.map((d) => `<button class="day ${d === day ? 'on' : ''}" data-day="${d}">Day ${d}</button>`).join(' ');
  el.querySelectorAll('button').forEach((b) => b.addEventListener('click', () => setDay(+b.dataset.day)));
}

function renderRows(items) {
  document.getElementById('count').textContent = `${items.length} item${items.length === 1 ? '' : 's'} watched`;
  document.getElementById('rows').innerHTML = items.map((i) => `
    <tr class="${i.id === flashId ? 'flash' : ''}">
      <td><b>${esc(i.name)}</b>${i.url ? `<div class="muted" style="font-size:12px">${esc(new URL(i.url).host)}</div>` : ''}</td>
      <td>${esc(i.rule)}</td>
      <td>${i.best_price != null ? `<b>${money(i.best_price)}</b>` : '—'}${i.best_store ? ` · ${esc(i.best_store)}` : ''}</td>
      <td><span class="pill ${i.tone}">${i.tone === 'good' ? '✓ ' : ''}${esc(i.status)}</span></td>
    </tr>`).join('');
}

function renderStats(s) {
  const cells = [['Price changes seen', s.price_changes], ['Alerts sent', s.alerts], ['Tricks caught', s.tricks], ['Stores checked', s.stores]];
  document.getElementById('stats').innerHTML = cells.map(([l, n]) => `<div class="stat"><div class="l">${l}</div><div class="n">${n}</div></div>`).join('');
  document.getElementById('not-alerted').textContent = s.alerts
    ? `Not alerted: ${Math.max(0, s.tricks - 4)} fake discounts, 3 wrong variants, 1 scam store, and drops above your target.`
    : 'No genuine drops yet. Pricise keeps watching.';
}

async function setDay(day) {
  const res = await api.clock(day);
  for (const a of res.alerts || []) {
    flashId = a.id;
    chrome.runtime.sendMessage({ type: 'notify', id: `pricise-${a.id}`, title: a.title, message: a.message });
    toast(`Desktop notification sent: ${a.title}`);
  }
  await load();
  flashId = null;
}

function toast(text) {
  const t = document.getElementById('toast');
  t.textContent = text;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 3200);
}
