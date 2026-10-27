// Evidence page: the proof behind every line in the side panel, one tab per section (sections.js). A link from the panel
// (?tab=…&row=…) opens that tab, then scrolls to and briefly highlights the exact row, once the content has rendered.
// A tab with no data on this page opens the nearest one with a one-line note ("No timer on this page").
import { api, esc } from '../shared/api.js';
import { matchRow, resolve, sections, TABS } from './sections.js';

const grid = document.getElementById('grid');
const tabsBar = document.getElementById('tabs');
const noteBar = document.getElementById('note');
const params = new URLSearchParams(location.search);
const id = params.get('check');
let available = [];

load();

async function load() {
  if (!id) { grid.innerHTML = '<div class="panel span2 empty-note">Open this page from the Pricise side panel.</div>'; return; }
  let chk;
  try { chk = await api.getCheck(id); } catch (e) {
    grid.innerHTML = `<div class="panel span2 empty-note">Could not load this check (${esc(e.message)}). Is the backend running?</div>`; return;
  }
  const p = chk.page?.product || {};
  document.getElementById('page-url').textContent = chk.url;
  document.getElementById('title').textContent = `Evidence · ${p.name ? p.name.slice(0, 70) : chk.page?.host || 'this page'}`;
  if (chk.final_card) document.getElementById('subtitle').textContent = `${chk.final_card.title} · ${chk.final_card.subtitle}`;
  const list = sections(chk);
  available = list.map((s) => s.id);
  grid.innerHTML = list.map((s) => s.html).join('');
  tabsBar.innerHTML = TABS.filter(([t]) => available.includes(t))
    .map(([t, label]) => `<a href="#" role="tab" data-tab="${t}">${esc(label)}</a>`).join('');
  document.addEventListener('click', (e) => {
    const all = e.target.closest('[data-act="trace-all"]');
    if (all) { // a long trace: show the rows after the first 40
      grid.querySelectorAll('#trace .r.more').forEach((r) => { r.hidden = false; });
      all.remove();
      return;
    }
    const a = e.target.closest('[data-tab]');
    if (!a) return;
    e.preventDefault();
    open(a.dataset.tab, null, true);
  });
  // An old link with only #section still works.
  open(params.get('tab') || location.hash.slice(1) || 'overview', params.get('row'), false);
}

function open(view, row, fromClick) {
  const { tab, note, signals } = resolve(view, available);
  grid.querySelectorAll(':scope > section').forEach((s) => { s.hidden = s.id !== tab; });
  tabsBar.querySelectorAll('[data-tab]').forEach((a) => a.setAttribute('aria-selected', String(a.dataset.tab === tab)));
  noteBar.textContent = fromClick ? '' : note || '';
  noteBar.hidden = fromClick || !note;
  const q = new URLSearchParams(location.search);
  q.set('tab', tab);
  if (row && !fromClick) q.set('row', row); else q.delete('row');
  history.replaceState(null, '', `?${q}`);
  // Scroll only after the browser has laid out the tab we just showed.
  requestAnimationFrame(() => requestAnimationFrame(() => focusRow(tab, fromClick ? null : row, signals)));
}

function focusRow(tab, row, signals = []) {
  const section = document.getElementById(tab);
  if (!section) return;
  const rows = [...section.querySelectorAll('[data-row]')];
  const i = matchRow(rows.map((r) => r.dataset.row), row);
  // No exact row: the page's own signal row for a missing tab (its countdown, its fees), else the tab's main row.
  const signal = signals.map((t) => section.querySelector(`[data-signal="${t}"]`)).find(Boolean);
  const el = i >= 0 ? rows[i] : row ? signal || section.querySelector('[data-row-default]') || section : null;
  if (!el) { window.scrollTo({ top: 0 }); return; }
  el.scrollIntoView({ block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  el.classList.remove('flash');
  void el.offsetWidth; // restart the highlight animation
  el.classList.add('flash');
}
