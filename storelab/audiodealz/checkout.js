// Store Lab: renders the order summary for the made-up AudioDealz checkout. The trick: two fees appear only at step 4.
(() => {
  const params = new URLSearchParams(location.search);
  const addon = params.get('addon') !== '0';
  const step = Number(document.body.dataset.step || 0);
  const lines = [['Sony WH-1000XM5 Wireless Headphones, Black', 149.00]];
  if (addon) lines.push(['2-Year Protection Plan', 4.99]);
  lines.push(['Shipping', 0]);
  if (step >= 4) lines.push(['Service fee', 22.00], ['Processing fee', 15.01]);
  const total = lines.reduce((s, [, a]) => s + a, 0);
  const fmt = (a) => (a === 0 ? 'FREE' : '$' + a.toFixed(2));
  const rows = lines.map(([l, a]) => `<tr data-line><td>${l}</td><td class="amt">${fmt(a)}</td></tr>`).join('');
  document.getElementById('summary').innerHTML =
    `<table class="summary">${rows}<tr class="total" data-total><td>Order total</td><td class="amt">$${total.toFixed(2)}</td></tr></table>`;
  document.querySelectorAll('a[data-next]').forEach((a) => { a.href = a.getAttribute('href') + '?addon=' + (addon ? 1 : 0); });
  const order = document.getElementById('place-order');
  if (order) order.addEventListener('click', () => { document.getElementById('note').textContent = 'Store Lab: this made-up store takes no orders.'; });
})();
