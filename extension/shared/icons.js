// Pricise line icons: 24x24, 1.8 stroke, currentColor. One place for every icon in the extension (no emoji, no icon CDN).
// The quick card (a classic content script) can't import modules, so the background passes it the few it needs as strings.
const svg = (body, label = '') =>
  `<svg class="i" viewBox="0 0 24 24" ${label ? `role="img" aria-label="${label}"` : 'aria-hidden="true"'}>${body}</svg>`;

export const ICONS = {
  refresh: svg('<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>'),
  store: svg('<path d="M3 9l1.5-5h15L21 9"/><path d="M4 9v11h16V9"/><path d="M9 20v-6h6v6"/>'),
  tag: svg('<path d="M20.6 13.4l-7.2 7.2a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8z"/><circle cx="7.5" cy="7.5" r="1.2"/>'),
  target: svg('<circle cx="12" cy="12" r="7"/><path d="M12 2v4M12 18v4M2 12h4M18 12h4"/>'),
  shield: svg('<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M9 12l2 2 4-4"/>'),
  chevron: svg('<path d="M9 6l6 6-6 6"/>'),
  star: svg('<path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z"/>'),
  check: svg('<path d="M5 12.5l4.5 4.5L19 7.5"/>'),
  lock: svg('<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>'),
  send: svg('<path d="M5 12h14M13 6l6 6-6 6"/>'),
  close: svg('<path d="M6 6l12 12M18 6L6 18"/>'),
  upload: svg('<path d="M12 16V4M7 9l5-5 5 5"/><path d="M5 20h14"/>'),
  bell: svg('<path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4z"/><path d="M10 21h4"/>'),
};
