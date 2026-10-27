// Pricise quick card: a small card at the top-right of every product page, from "Checking this page…" to red / amber /
// green, updated live as findings arrive. It stays until the user closes it (×) for this page view.
// It sits in a closed Shadow DOM, so the store's CSS can't change it and its CSS can't change the store.
// It never clicks or changes anything on the page. Questions about the page are asked in the side panel.
(() => {
  if (window.__priciseCard) return;
  window.__priciseCard = true;

  const SETTINGS_KEY = 'pricise:settings'; // same key as extension/shared/check.js
  let host = null;
  let view = { dismissed: false, tone: null, refresh: null }; // this page view: closed with ×? last tone shown? live updater

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg?.type === 'pricise:quick-card') { (msg.data.update ? update : show)(msg.data); sendResponse({ ok: true }); }
  });
  // content.js calls this when a single-page site moves to another URL, so an old result never sits on a new product.
  // It lives in the extension's isolated world, so the page's own scripts can't call it.
  window.__priciseRemoveCard = () => { remove(); view = { dismissed: false, tone: null, refresh: null }; };

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  // A newer state for this page view (a finding landed, the verdict firmed up). Update the visible card in place; if it was
  // closed with ×, leave it closed.
  function update(data) {
    if (view.dismissed) return;
    if (host && host.isConnected && view.refresh) return view.refresh(data);
    show(data);
  }

  function show(data) {
    remove();
    host = document.createElement('pricise-quick-card');
    const root = host.attachShadow({ mode: 'closed' });
    const style = el('style');
    style.textContent = data.css || '';
    const tone = ['good', 'bad', 'caution'].includes(data.tone) ? data.tone : 'unknown';
    const card = el('div', `qc ${tone}`);
    card.setAttribute('role', 'dialog');
    card.setAttribute('aria-label', 'Pricise check result');

    const head = el('div', 'qh');
    const logo = el('img');
    logo.src = data.logo || chrome.runtime.getURL('icons/logo.svg');
    logo.alt = '';
    const close = el('button', 'qx');
    close.type = 'button';
    close.setAttribute('aria-label', 'Close');
    close.innerHTML = data.close || '×'; // our own static SVG from extension/shared/icons.js
    head.append(logo, document.createTextNode('Pricise'), close);

    const body = el('div', 'qb');
    const fill = (d) => {
      const tone = ['good', 'bad', 'caution'].includes(d.tone) ? d.tone : 'unknown';
      card.className = `qc ${tone}${d.state === 'checking' ? ' checking' : ''}`;
      card.setAttribute('aria-busy', d.state === 'checking' ? 'true' : 'false');
      view.tone = tone;
      body.replaceChildren(el('div', 'qt', d.title || 'Checked'));
      for (const line of (d.lines || []).slice(0, 2)) {
        const row = el('div', 'qs');
        for (const [text, bold] of line) row.appendChild(bold ? el('b', 'num', text) : document.createTextNode(text));
        body.appendChild(row);
      }
      // Compare prices: search links for this exact item (built from the page's own brand + model). Opens a new tab.
      const links = (d.compare || []).filter((x) => x && x.url);
      if (links.length) {
        const row = el('div', 'qcmp');
        links.forEach((x, i) => {
          const b = el('button', i === 0 ? 'main' : 'alt', i === 0 ? 'Compare prices' : x.label);
          b.type = 'button';
          b.title = i === 0 ? `Search ${x.label} for this item` : `Search ${x.label}`;
          b.addEventListener('click', () => chrome.runtime.sendMessage({ type: 'pricise:open-url', url: x.url }).catch(() => {}));
          row.appendChild(b);
        });
        body.appendChild(row);
      }
    };
    fill(data);

    const note = el('div', 'qnote', 'Click the Pricise icon in the toolbar for details.');
    note.hidden = true;
    const foot = el('div', 'qf');
    const details = el('button', 'l', 'See details');
    details.type = 'button';
    const never = el('button', 'r', "Don't show on this site");
    never.type = 'button';
    foot.append(details, never);

    card.append(head, body, note, foot);
    root.append(style, card);
    document.documentElement.appendChild(host);

    // No auto-hide: the card stays until × (this page view) or "Don't show on this site".
    view.refresh = (d) => fill(d);

    close.addEventListener('click', () => { view.dismissed = true; hide(card); }); // this page view only
    details.addEventListener('click', async () => {
      const res = await chrome.runtime.sendMessage({ type: 'pricise:open-panel' }).catch(() => null);
      if (res?.ok) return hide(card);
      note.hidden = false; // Chrome wants a click on the toolbar icon
    });
    never.addEventListener('click', async () => {
      const s = (await chrome.storage.local.get(SETTINGS_KEY))[SETTINGS_KEY] || {};
      const sites = new Set(Array.isArray(s.neverSites) ? s.neverSites : []);
      sites.add(data.host || location.host);
      await chrome.storage.local.set({ [SETTINGS_KEY]: { ...s, neverSites: [...sites] } });
      view.dismissed = true;
      hide(card);
    });
  }

  function hide(card) {
    if (!card.isConnected) return;
    const mine = card.getRootNode().host;
    const done = () => { if (host === mine) remove(); };
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) return done();
    card.classList.add('out');
    card.addEventListener('animationend', done, { once: true });
    setTimeout(done, 400); // in case animationend never fires
  }

  function remove() {
    // Every card on the page, including one left by an older copy of this script (e.g. after the extension was reloaded).
    document.querySelectorAll('pricise-quick-card').forEach((n) => n.remove());
    host = null;
    view.refresh = null;
  }
})();
