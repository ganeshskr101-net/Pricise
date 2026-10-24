// Pricise service worker: opens the side panel, keeps the toolbar badge, shows desktop notifications.
chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg?.type === 'badge') {
    const text = msg.count > 0 ? String(msg.count) : '';
    chrome.action.setBadgeText({ tabId: msg.tabId, text });
    chrome.action.setBadgeBackgroundColor({ tabId: msg.tabId, color: msg.count > 0 ? '#c5221f' : '#137333' });
    sendResponse({ ok: true });
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

// Clear the badge when a tab navigates to a new page; the panel re-checks it.
chrome.tabs.onUpdated.addListener((tabId, info) => {
  if (info.status === 'loading') chrome.action.setBadgeText({ tabId, text: '' });
});
