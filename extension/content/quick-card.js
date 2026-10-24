// Pricise quick verdict card: a small card at the top-right of the page when a check finds a trick or a cheaper safe option.
// It sits in a closed Shadow DOM, so the store's CSS can't change it and its CSS can't change the store.
// It never clicks or changes anything on the page. Questions about the page are asked in the side panel.
(() => {
  if (window.__priciseCard) return;
  window.__priciseCard = true;

  const SETTINGS_KEY = 'pricise:settings'; // same key as extension/shared/check.js
  const HIDE_AFTER_MS = 8000;
  const ICON_DETAILS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/></svg>';
  const ICON_CLOSE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>';
  let host = null;

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg?.type === 'pricise:quick-card') { show(msg.data); sendResponse({ ok: true }); }
  });

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function show(data) {
    remove();
    host = document.createElement('pricise-quick-card');
    const root = host.attachShadow({ mode: 'closed' });
    const style = el('style');
    style.textContent = data.css || '';
    const card = el('div', 'qc card');
    card.setAttribute('role', 'dialog');
    card.setAttribute('aria-label', 'Pricise quick verdict');
    card.innerHTML = `
      <div class="qc-head"><img class="logo" alt=""><div class="qc-verdict"></div>
        <button class="qc-icon" type="button" data-act="details" title="Details" aria-label="Open details in the side panel">${ICON_DETAILS}</button>
        <button class="qc-icon" type="button" data-act="close" title="Close" aria-label="Close">${ICON_CLOSE}</button></div>
      <div class="qc-reason"></div>
      <div class="qc-foot"><a href="#" class="qc-never" data-act="never">Never on this site</a></div>`;
    card.querySelector('.logo').src = data.logo || chrome.runtime.getURL('icons/logo.svg');
    const tone = ['good', 'bad', 'caution', 'unknown'].includes(data.tone) ? data.tone : 'unknown';
    const verdict = card.querySelector('.qc-verdict');
    verdict.classList.add(tone);
    verdict.textContent = data.title || 'Checked';
    const reasonEl = card.querySelector('.qc-reason');
    reasonEl.textContent = data.reason || '';
    reasonEl.title = data.reason || '';
    root.append(style, card);
    document.documentElement.appendChild(host);

    // Auto-hide after 8 s unless the pointer or keyboard focus is on the card.
    let timer = null;
    const hold = () => clearTimeout(timer);
    const arm = () => { clearTimeout(timer); timer = setTimeout(() => hide(card), HIDE_AFTER_MS); };
    card.addEventListener('mouseenter', hold);
    card.addEventListener('mouseleave', arm);
    card.addEventListener('focusin', hold);
    card.addEventListener('focusout', arm);
    arm();

    card.querySelector('[data-act=close]').addEventListener('click', () => hide(card)); // this page view only
    card.querySelector('[data-act=details]').addEventListener('click', async () => {
      const res = await chrome.runtime.sendMessage({ type: 'pricise:open-panel' }).catch(() => null);
      if (res?.ok) return hide(card);
      reasonEl.textContent = 'Click the Pricise icon for details'; // Chrome wants a click on the toolbar icon
    });
    card.querySelector('[data-act=never]').addEventListener('click', async (e) => {
      e.preventDefault();
      const s = (await chrome.storage.local.get(SETTINGS_KEY))[SETTINGS_KEY] || {};
      const sites = new Set(Array.isArray(s.neverSites) ? s.neverSites : []);
      sites.add(data.host || location.host);
      await chrome.storage.local.set({ [SETTINGS_KEY]: { ...s, neverSites: [...sites] } });
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
  }
})();
