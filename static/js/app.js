/* AI-BloodLink dashboard - vanilla JS single-page app (no build step needed) */
'use strict';

// ------------------------------------------------------------------ helpers
const $ = (s, el = document) => el.querySelector(s);
const view = $('#view');
const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const api = async (url, opts) => {
  const r = await fetch(url, opts);
  let data = null;
  try { data = await r.json(); } catch (e) { /* ignore */ }
  if (!r.ok) throw new Error((data && data.error) || r.statusText);
  return data;
};
const post = (url, body) => api(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
const fmt = n => Number(n).toLocaleString('en-IN');
const md = s => esc(s).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/\*(.+?)\*/g, '<i>$1</i>').replace(/\n/g, '<br>');
const mmdd = d => d.slice(5);
const daysLeft = d => Math.round((new Date(d) - new Date(new Date().toDateString())) / 864e5);

function toast(msg, isErr) {
  const t = document.createElement('div');
  t.className = 'toast' + (isErr ? ' err' : '');
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3200);
}

const STATUS_LABEL = { critical: 'Critical', low: 'Low', ok: 'Healthy', surplus: 'Surplus', in_transit: 'In transit', delivered: 'Delivered', available: 'Available', issued: 'Issued' };
const badge = s => `<span class="badge ${esc(s)}">${esc(STATUS_LABEL[s] || s)}</span>`;
const COLORS = { red: '#c81e1e', redSoft: 'rgba(200,30,30,.14)', blue: '#2563eb', slate: '#64748b', green: '#15803d', amber: '#d97706', violet: '#7c3aed' };

// Chart.js registry - destroy before re-render
const charts = {};
function mkChart(id, config) {
  if (charts[id]) charts[id].destroy();
  const el = document.getElementById(id);
  if (!el) return null;
  config.options = Object.assign({ responsive: true, maintainAspectRatio: false }, config.options || {});
  charts[id] = new Chart(el, config);
  return charts[id];
}
if (window.Chart) {
  Chart.defaults.font.family = "'Inter', system-ui, sans-serif";
  Chart.defaults.font.size = 12;
  Chart.defaults.color = '#6b7280';
  Chart.defaults.plugins.legend.labels.usePointStyle = true;
  Chart.defaults.plugins.legend.labels.boxWidth = 8;
}

let timers = [];
let map = null;

// ------------------------------------------------------------------ icons + routes
const ICON = {
  grid: '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/>',
  drop: '<path d="M12 2.69l5.66 5.66a8 8 0 1 1-11.31 0z"/>',
  trend: '<polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/>',
  shuffle: '<polyline points="16 3 21 3 21 8"/><line x1="4" y1="20" x2="21" y2="3"/><polyline points="21 16 21 21 16 21"/><line x1="15" y1="15" x2="21" y2="21"/><line x1="4" y1="4" x2="9" y2="9"/>',
  pin: '<path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/>',
  therm: '<path d="M14 14.76V3.5a2.5 2.5 0 0 0-5 0v11.26a4.5 4.5 0 1 0 5 0z"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
  users: '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
  chat: '<path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>',
  flask: '<path d="M10 2v7.5L4.5 19a2 2 0 0 0 1.8 3h11.4a2 2 0 0 0 1.8-3L14 9.5V2"/><line x1="8.5" y1="2" x2="15.5" y2="2"/>',
  layers: '<polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/>',
};
const ROUTES = [
  { id: 'dashboard', label: 'Dashboard', icon: 'grid', sub: 'Network overview and live shortage warnings', group: 'Overview' },
  { id: 'inventory', label: 'Inventory', icon: 'drop', sub: 'RFID-tracked units across every blood bank', group: 'Overview' },
  { id: 'forecast', label: 'Demand Forecast', icon: 'trend', sub: 'Machine learning prediction for the next 7 days', group: 'Intelligence' },
  { id: 'redistribution', label: 'Smart Redistribution', icon: 'shuffle', sub: 'Move blood between banks before it expires', group: 'Intelligence', badge: true },
  { id: 'impact', label: 'Impact Backtest', icon: 'flask', sub: 'Would this system have prevented shortages? Replayed on held-out days', group: 'Intelligence' },
  { id: 'tracking', label: 'Live Tracking', icon: 'pin', sub: 'GPS tracking of drone and van deliveries', group: 'Operations' },
  { id: 'coldchain', label: 'Cold Chain (IoT)', icon: 'therm', sub: 'Sensors, 5G / LoRaWAN links and edge computing', group: 'Operations' },
  { id: 'traceability', label: 'Traceability', icon: 'link', sub: 'RFID scanning and blockchain ledger', group: 'Operations' },
  { id: 'donors', label: 'Donors', icon: 'users', sub: 'Donor registry and donation recording', group: 'People' },
  { id: 'assistant', label: 'AI Assistant', icon: 'chat', sub: 'Chatbot for donors and hospitals', group: 'People' },
  { id: 'architecture', label: 'IoE Architecture', icon: 'layers', sub: 'People · Process · Data · Things', group: 'Project' },
];

function buildNav() {
  let html = '', last = '';
  for (const r of ROUTES) {
    if (r.group !== last) { html += `<div class="nav-label">${r.group}</div>`; last = r.group; }
    html += `<a href="#/${r.id}" data-id="${r.id}"><svg viewBox="0 0 24 24">${ICON[r.icon]}</svg>${r.label}${r.badge ? '<span class="count" id="nav-count" style="display:none"></span>' : ''}</a>`;
  }
  $('#nav').innerHTML = html;
}

// ------------------------------------------------------------------ router
const views = {};
async function route() {
  timers.forEach(clearInterval); timers = [];
  if (map) { map.remove(); map = null; }
  Object.keys(charts).forEach(k => { charts[k].destroy(); delete charts[k]; });
  const id = (location.hash.replace('#/', '') || 'dashboard');
  const r = ROUTES.find(x => x.id === id) || ROUTES[0];
  document.querySelectorAll('.nav a').forEach(a => a.classList.toggle('active', a.dataset.id === r.id));
  $('#title').textContent = r.label;
  $('#subtitle').textContent = r.sub;
  view.innerHTML = '<div class="loading">Loading…</div>';
  try { await views[r.id](); }
  catch (e) { view.innerHTML = `<div class="card empty">Something went wrong: ${esc(e.message)}</div>`; }
  window.scrollTo(0, 0);
}

async function refreshGlobals() {
  try {
    const s = await api('/api/summary');
    const dot = $('#ledger-dot');
    dot.classList.toggle('bad', !s.ledger.valid);
    $('#ledger-text').textContent = s.ledger.valid ? `Ledger verified · ${s.ledger.length} blocks` : `Ledger TAMPERED · block #${s.ledger.broken_at}`;
    const c = $('#nav-count');
    if (c) { c.style.display = s.proposals ? '' : 'none'; c.textContent = s.proposals; }
  } catch (e) { /* ignore */ }
}
function tickClock() {
  $('#clock').textContent = new Date().toLocaleString('en-IN', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

// ------------------------------------------------------------------ dashboard
views.dashboard = async () => {
  const [s, net, alerts, tr] = await Promise.all([api('/api/summary'), api('/api/forecast/network'), api('/api/alerts'), api('/api/transfers')]);
  const active = tr.transfers.filter(t => t.status === 'in_transit');
  view.innerHTML = `
    <div class="grid g6">
      <div class="card kpi blue"><span class="lbl">Units in stock</span><span class="val">${fmt(s.units)}</span><span class="note">across ${s.banks} blood banks</span></div>
      <div class="card kpi red"><span class="lbl">Critical shortages</span><span class="val red">${s.critical}</span><span class="note">${s.low} more running low</span></div>
      <div class="card kpi amber"><span class="lbl">Expiring in 5 days</span><span class="val">${fmt(s.expiring)}</span><span class="note">first-expiry-first-out moves these first</span></div>
      <div class="card kpi violet"><span class="lbl">Transfers in transit</span><span class="val">${s.active_transfers}</span><span class="note">${s.proposals} recommendations waiting</span></div>
      <div class="card kpi green"><span class="lbl">Forecast accuracy gain</span><span class="val">${s.model.improvement_pct}%</span><span class="note">${esc(s.model.best)} vs baseline</span></div>
      <div class="card kpi ${s.ledger.valid ? 'green' : 'red'}"><span class="lbl">Ledger integrity</span><span class="val">${s.ledger.valid ? 'Verified' : 'Broken'}</span><span class="note">${s.ledger.length} blockchain blocks</span></div>
    </div>
    <div class="grid g-main section-gap">
      <div class="card"><div class="card-head"><div><h3>Network demand: last 30 days and 7-day forecast</h3><div class="hint">Units issued per day, all banks and blood groups</div></div></div><div class="chart-box"><canvas id="c-demand"></canvas></div></div>
      <div class="card"><div class="card-head"><div><h3>Stock vs next 7 days of demand</h3><div class="hint">Where the bars cross, a shortage is coming</div></div></div><div class="chart-box"><canvas id="c-stock"></canvas></div></div>
    </div>
    <div class="grid g-main section-gap">
      <div class="card"><div class="card-head"><div><h3>Shortage warnings</h3><div class="hint">Days of cover = stock ÷ predicted daily demand</div></div><a class="btn small primary" href="#/redistribution">View recommendations</a></div>
        ${alerts.length ? `<div class="table-wrap"><table><thead><tr><th>Blood bank</th><th>Group</th><th>Stock</th><th>7-day demand</th><th>Cover</th><th>Status</th></tr></thead><tbody>
        ${alerts.slice(0, 8).map(a => `<tr><td>${esc(a.bank)}</td><td><span class="chip">${a.group}</span></td><td>${a.units}${a.incoming ? ` <span class="muted">+${a.incoming} incoming</span>` : ''}</td><td>${a.forecast7}</td><td><b>${a.cover}</b> days</td><td>${badge(a.status)}</td></tr>`).join('')}
        </tbody></table></div>` : '<div class="empty">No shortages predicted. 🎉</div>'}</div>
      <div class="card"><div class="card-head"><div><h3>Active deliveries</h3><div class="hint">Live GPS-tracked transfers</div></div></div>
        ${active.length ? active.map(t => `<div class="titem" style="margin-bottom:10px"><div class="row"><span><span class="chip">${t.group}</span> ${t.units} units</span><span class="badge in_transit">${esc(t.mode)}</span></div><div class="muted" style="margin-bottom:8px">${esc(t.from.name)} → ${esc(t.to.name)}</div><div class="progress"><i style="width:${t.progress * 100}%"></i></div></div>`).join('') : '<div class="empty">No deliveries right now.<br>Approve a recommendation to start one.</div>'}
      </div>
    </div>`;
  const labels = [...net.history.dates.map(mmdd), ...net.forecast.dates.map(mmdd)];
  const hist = [...net.history.values, ...net.forecast.values.map(() => null)];
  const last = net.history.values[net.history.values.length - 1];
  const fc = [...net.history.values.map((_, i) => (i === net.history.values.length - 1 ? last : null)), ...net.forecast.values];
  mkChart('c-demand', { type: 'line', data: { labels, datasets: [
    { label: 'Actual demand', data: hist, borderColor: COLORS.slate, backgroundColor: 'rgba(100,116,139,.1)', fill: true, tension: .35, pointRadius: 0, borderWidth: 2 },
    { label: 'ML forecast', data: fc, borderColor: COLORS.red, backgroundColor: COLORS.redSoft, borderDash: [6, 4], fill: true, tension: .35, pointRadius: 3, borderWidth: 2.5 }] },
    options: { interaction: { mode: 'index', intersect: false }, scales: { y: { beginAtZero: true, grid: { color: '#f1f2f5' } }, x: { grid: { display: false }, ticks: { maxTicksLimit: 10 } } }, plugins: { legend: { position: 'bottom' } } } });
  mkChart('c-stock', { type: 'bar', data: { labels: net.groups.map(g => g.group), datasets: [
    { label: 'Stock on hand', data: net.groups.map(g => g.stock), backgroundColor: '#94a3b8', borderRadius: 5 },
    { label: 'Predicted 7-day demand', data: net.groups.map(g => g.forecast7), backgroundColor: COLORS.red, borderRadius: 5 }] },
    options: { scales: { y: { beginAtZero: true, grid: { color: '#f1f2f5' } }, x: { grid: { display: false } } }, plugins: { legend: { position: 'bottom' } } } });
  refreshGlobals();
};

// ------------------------------------------------------------------ inventory
views.inventory = async () => {
  const inv = await api('/api/inventory');
  const cell = (b, g) => inv.cells.find(c => c.bank_id === b && c.group === g);
  view.innerHTML = `
    <div class="card"><div class="card-head"><div><h3>Stock heatmap</h3><div class="hint">Each cell shows units on hand and days of cover. Colour reflects the ML-predicted risk.</div></div></div>
      <div class="table-wrap"><table class="heat"><thead><tr><th>Blood bank</th>${inv.groups.map(g => `<th style="text-align:center">${g}</th>`).join('')}</tr></thead><tbody>
      ${inv.banks.map(b => `<tr><td>${esc(b.name)}<div class="muted" style="font-weight:400;font-size:11.5px">${esc(b.city)} · ${esc(b.link)}</div></td>${inv.groups.map(g => { const c = cell(b.id, g.toString()); return `<td><div class="cell ${c.status}" title="${esc(b.name)} · ${g}: ${c.units} units, ${c.cover} days cover, ${c.expiring} expiring ≤5d"><b>${c.units}${c.incoming ? `<small>+${c.incoming} incoming</small>` : ''}</b><span>${c.cover >= 99 ? '—' : c.cover + ' d'}</span>${c.expiring ? `<small>⏳ ${c.expiring} expiring</small>` : ''}</div></td>`; }).join('')}</tr>`).join('')}
      </tbody></table></div>
      <div class="legend"><span><i style="background:var(--crit-l)"></i>Critical (&lt; 3 days)</span><span><i style="background:var(--warn-l)"></i>Low (&lt; 5 days)</span><span><i style="background:#dcfce7"></i>Healthy</span><span><i style="background:var(--sur-l)"></i>Surplus (&gt; 14 days)</span></div></div>
    <div class="card section-gap"><div class="card-head"><div><h3>RFID bag registry</h3><div class="hint">Every unit carries an RFID tag. Sorted by earliest expiry.</div></div></div>
      <div class="toolbar"><select id="f-bank"><option value="0">All banks</option>${inv.banks.map(b => `<option value="${b.id}">${esc(b.name)}</option>`).join('')}</select>
      <select id="f-group"><option value="">All groups</option>${inv.groups.map(g => `<option>${g}</option>`).join('')}</select><button class="btn" id="f-go">Apply</button></div>
      <div id="bags"></div></div>`;
  const load = async () => {
    const rows = await api(`/api/bags?limit=25&status=available&bank_id=${$('#f-bank').value}&group=${encodeURIComponent($('#f-group').value)}`);
    $('#bags').innerHTML = `<div class="table-wrap"><table><thead><tr><th>RFID tag</th><th>Group</th><th>Blood bank</th><th>Collected</th><th>Expires</th><th>Status</th><th></th></tr></thead><tbody>${rows.map(b => {
      const d = daysLeft(b.expiry_date);
      return `<tr><td class="mono">${b.tag}</td><td><span class="chip">${b.blood_group}</span></td><td>${esc(b.bank)}</td><td>${b.collected_date}</td><td>${b.expiry_date} <span class="badge ${d <= 5 ? 'critical' : d <= 10 ? 'low' : 'gray'}">${d}d left</span></td><td>${badge(b.status)}</td><td><a class="btn small" href="#/traceability" onclick="sessionStorage.setItem('tag','${b.tag}')">Trace</a></td></tr>`;
    }).join('')}</tbody></table></div>`;
  };
  $('#f-go').onclick = load; load();
};

// ------------------------------------------------------------------ forecast
views.forecast = async () => {
  const [inv, model] = await Promise.all([api('/api/inventory'), api('/api/model')]);
  view.innerHTML = `
    <div class="toolbar"><select id="s-bank"><option value="0">Whole network</option>${inv.banks.map(b => `<option value="${b.id}" ${b.id === 1 ? 'selected' : ''}>${esc(b.name)}</option>`).join('')}</select>
      <select id="s-group">${inv.groups.map(g => `<option ${g === 'B+' ? 'selected' : ''}>${g}</option>`).join('')}</select></div>
    <div class="grid g-main"><div class="card"><div class="card-head"><div><h3 id="f-title">Demand forecast</h3><div class="hint">Last 30 days of actual demand and the model's next 7 days</div></div><span id="f-badge"></span></div><div class="chart-box tall"><canvas id="c-fc"></canvas></div></div>
      <div class="grid" style="align-content:start"><div class="card kpi blue"><span class="lbl">Stock now</span><span class="val" id="k-stock">–</span><span class="note" id="k-daily"></span></div>
      <div class="card kpi amber"><span class="lbl">Days of cover</span><span class="val" id="k-cover">–</span><span class="note">stock ÷ predicted daily demand</span></div></div></div>
    <div class="grid g2 section-gap">
      <div class="card"><div class="card-head"><div><h3>Model comparison</h3><div class="hint">Time-based split: trained on history, tested on the last ${model.test_days} days (${fmt(model.test_rows)} predictions)</div></div></div>
        <table><thead><tr><th>Model</th><th class="right">MAE</th><th class="right">RMSE</th><th class="right">WMAPE</th></tr></thead><tbody>${model.table.map(m => `<tr><td>${esc(m.model)} ${m.model === model.best ? '<span class="badge real">selected</span>' : ''}</td><td class="right">${m.mae}</td><td class="right">${m.rmse}</td><td class="right">${m.wmape}%</td></tr>`).join('')}</tbody></table>
        <p class="muted" style="margin-top:12px">Lower is better. ${esc(model.best)} cuts error by <b>${model.improvement_pct}%</b> versus the naive moving-average baseline.</p></div>
      <div class="card"><div class="card-head"><div><h3>What drives the forecast?</h3><div class="hint">Feature importance of the selected model</div></div></div><div class="chart-box short"><canvas id="c-imp"></canvas></div></div></div>`;
  const draw = async () => {
    const bank = $('#s-bank').value, group = $('#s-group').value;
    const f = await api(`/api/forecast?bank_id=${bank}&group=${encodeURIComponent(group)}`);
    const bn = bank === '0' ? 'Whole network' : inv.banks.find(b => b.id == bank).name;
    $('#f-title').textContent = `${group} demand · ${bn}`;
    $('#f-badge').innerHTML = badge(f.status);
    $('#k-stock').textContent = fmt(f.stock); $('#k-daily').textContent = `~${f.daily} units/day predicted`;
    $('#k-cover').textContent = f.cover >= 99 ? '—' : f.cover + ' d';
    const h = f.history, fc = f.forecast, n = h.values.length;
    mkChart('c-fc', { type: 'line', data: { labels: [...h.dates.map(mmdd), ...fc.dates.map(mmdd)], datasets: [
      { label: 'Actual', data: [...h.values, ...fc.values.map(() => null)], borderColor: COLORS.slate, tension: .3, pointRadius: 2, borderWidth: 2 },
      { label: 'Forecast', data: [...h.values.map((_, i) => i === n - 1 ? h.values[n - 1] : null), ...fc.values], borderColor: COLORS.red, backgroundColor: COLORS.redSoft, fill: true, borderDash: [6, 4], tension: .3, pointRadius: 4, borderWidth: 2.5 }] },
      options: { interaction: { mode: 'index', intersect: false }, scales: { y: { beginAtZero: true, grid: { color: '#f1f2f5' } }, x: { grid: { display: false }, ticks: { maxTicksLimit: 10 } } }, plugins: { legend: { position: 'bottom' } } } });
  };
  $('#s-bank').onchange = draw; $('#s-group').onchange = draw; draw();
  mkChart('c-imp', { type: 'bar', data: { labels: model.importance.map(i => i.feature), datasets: [{ data: model.importance.map(i => i.value), backgroundColor: COLORS.red, borderRadius: 5 }] },
    options: { indexAxis: 'y', plugins: { legend: { display: false } }, scales: { x: { grid: { color: '#f1f2f5' } }, y: { grid: { display: false } } } } });
};

// ------------------------------------------------------------------ redistribution
views.redistribution = async () => {
  const props = await api('/api/redistribution');
  const saved = props.reduce((a, p) => a + p.rescued, 0);
  view.innerHTML = `
    <div class="banner ${props.length ? 'warn' : 'ok'}">${props.length ? `${props.length} recommended transfer${props.length > 1 ? 's' : ''} · ${saved} near-expiry units would be rescued from wastage` : 'All banks are balanced. No transfers needed.'}</div>
    <div class="grid" id="plist">${props.map((p, i) => `
      <div class="card proposal"><div>
        <div class="route"><span>${esc(p.from)}</span><span class="arrow">→</span><span>${esc(p.to)}</span><span class="chip">${p.units} × ${p.group}</span>${badge(p.urgency)}</div>
        <div class="meta"><span>Distance <b>${p.distance_km} km</b></span><span>Transport <b>${p.mode === 'Drone' ? '🚁 Drone' : '🚚 Refrigerated van'}</b></span><span>ETA <b>${p.eta_min} min</b></span><span>Rescues <b>${p.rescued}</b> near-expiry units</span></div>
        <div class="reason">${esc(p.reason)} Units are picked first-expiring-first-out.</div></div>
        <button class="btn primary" data-i="${i}">Approve &amp; dispatch</button></div>`).join('')}</div>`;
  document.querySelectorAll('#plist button').forEach(btn => btn.onclick = async () => {
    const p = props[btn.dataset.i];
    btn.disabled = true; btn.textContent = 'Dispatching…';
    try {
      const r = await post('/api/redistribution/approve', { from_id: p.from_id, to_id: p.to_id, group: p.group, units: p.units });
      toast(`Transfer #${r.transfer_id} dispatched by ${r.mode} · ETA ${r.eta_min} min`);
      await refreshGlobals(); route();
    } catch (e) { toast(e.message, true); btn.disabled = false; btn.textContent = 'Approve & dispatch'; }
  });
  refreshGlobals();
  const [subs, cm] = await Promise.all([api('/api/substitutions'), api('/api/compatibility')]);
  const withOpt = subs.filter(x => x.options.length);
  view.insertAdjacentHTML('beforeend', `
    <div class="card section-gap"><div class="card-head"><div><h3>Compatible-group substitution (emergency advisory)</h3><div class="hint">When a group runs short, the same bank may spare a compatible group. O- is the universal donor but scarce, so it is a last resort. A clinician must confirm every substitution (cross-match, patient Rh status).</div></div></div>
      ${withOpt.length ? `<div class="table-wrap"><table><thead><tr><th>Bank</th><th>Short group</th><th>Units needed</th><th>Compatible groups it could spare</th><th>Cover now</th><th>Cover with substitution</th></tr></thead><tbody>
      ${withOpt.map(x => `<tr><td>${esc(x.bank)}</td><td><span class="chip">${x.group}</span> ${badge(x.status)}</td><td>${x.need}</td><td>${x.options.map(o => `<span class="badge ${o.last_resort ? 'low' : 'ok'}">${o.group} · ${o.spare} spare${o.last_resort ? ' (last resort)' : ''}</span>`).join(' ')}</td><td>${x.cover_now} d</td><td><b>${x.cover_with_sub} d</b></td></tr>`).join('')}
      </tbody></table></div><p class="muted" style="margin-top:10px">Substitution only covers part of the gap here, which is why transfers between banks are still needed.</p>`
      : '<div class="empty">No short group has a compatible group to spare at its own bank right now.</div>'}</div>
    <div class="card section-gap"><div class="card-head"><div><h3>Red-cell compatibility matrix</h3><div class="hint">Rows are the patient's group; ✓ marks donor groups they can safely receive.</div></div></div>
      <div class="table-wrap"><table class="heat"><thead><tr><th>Patient ↓ / Donor →</th>${cm.groups.map(g => `<th style="text-align:center">${g}</th>`).join('')}</tr></thead><tbody>
      ${cm.groups.map(r => `<tr><td>${r}</td>${cm.groups.map(d => `<td>${cm.donors[r].includes(d) ? `<span class="badge ${d === 'O-' && r !== 'O-' ? 'low' : 'ok'}">✓</span>` : '<span class="muted">·</span>'}</td>`).join('')}</tr>`).join('')}
      </tbody></table></div></div>`);
};

// ------------------------------------------------------------------ impact backtest + real-data import
views.impact = async () => {
  const b = await api('/api/backtest');
  const src = b.data;
  view.innerHTML = `
    <div class="banner ${src.source === 'synthetic' ? 'warn' : 'ok'}">Data source: <b>${esc(src.source)}</b> · ${fmt(src.rows)} demand rows · ${src.from} → ${src.to}${src.source === 'synthetic' ? ' · synthetic demo data. Load your own CSV below to test on real data.' : ''}</div>
    <div class="toolbar"><label class="muted">Supply scenario</label><select id="lvl">${b.levels.map((l, i) => `<option value="${i}">${esc(l.label)}</option>`).join('')}</select>
      <span class="muted">Last ${b.window_days} days held out · model trained only on the ${b.train_days} days before · averaged over ${b.scenarios} random supply scenarios</span></div>
    <div id="bt-body"></div>
    <div class="card section-gap"><div class="card-head"><div><h3>Use your own data</h3><div class="hint">Upload daily demand per bank and blood group. The model and this backtest retrain on it (about 10 seconds).</div></div></div>
      <div class="toolbar"><a class="btn" href="/api/data/template" download>Download CSV template</a><input type="file" id="csv" accept=".csv,text/csv"><button class="btn primary" id="up">Upload and retrain</button><button class="btn" id="reset">Reset to demo data</button></div>
      <div id="up-status" class="muted"></div>
      <p class="muted" style="margin-top:10px">Format: <span class="mono">bank_id, blood_group, day, units</span>. Needs all 5 banks × 8 groups (40 series) and at least 90 days. Map your hospital's records to the 5 bank IDs in the template (for example, the 5 hospitals of a district). Public sources such as government blood-bank open data or published blood-donation datasets can be reshaped into this format.</p></div>
    <div class="card section-gap"><h3>How the backtest works</h3><ul class="muted" style="margin:8px 0 0 18px;line-height:1.8">
      <li>The last ${b.window_days} days are hidden. A new model is trained only on earlier days, so there is no data leakage.</li>
      <li>Blood units age daily (35-day shelf life) and are issued first-expiring-first-out. Units that reach zero days expire.</li>
      <li>Three policies replay on identical demand and donations: <b>none</b> (banks work alone), <b>reactive</b> (transfers based on a trailing 7-day average) and <b>predictive</b> (transfers based on the ML forecast).</li>
      <li>Transfers go from banks with more than 10 days of cover to banks with less than 4 days, arrive next day, and top the recipient up to 7 days.</li>
      <li><b>Limitation:</b> demand history has no donation records, so donations are simulated: each bank gets ${b.imbalance[0]}× to ${b.imbalance[1]}× its fair share, and opening stock is ${b.start_days} days of demand. Results are a simulation, not a clinical trial.</li></ul></div>`;
  const draw = () => {
    const L = b.levels[+$('#lvl').value], R = L.results, none = R[0], rea = R[1], pre = R[2];
    const diff = rea.unmet > 0 ? Math.round(100 * (rea.unmet - pre.unmet) / rea.unmet) : 0;
    const mlNote = Math.abs(diff) < 5
      ? `The ML forecast performed about the same as a simple trailing average for transfer decisions (${diff >= 0 ? '+' : ''}${diff}% unmet demand versus reactive). Its value shows in forecast accuracy (the model beats the baseline on per-day error), but for week-long stock decisions on this data the main benefit comes from pooling stock across the network.`
      : diff > 0 ? `The ML forecast cut unmet demand a further ${diff}% compared with the reactive policy.` : `The reactive policy beat the ML forecast by ${-diff}% on unmet demand in this scenario.`;
    const expNote = none.expired < 40 ? ` Expired units are small in absolute terms (${none.expired} → ${pre.expired}) because first-expiring-first-out already uses old units first; redistribution mostly prevents shortages.` : ` Expired units fell from ${none.expired} to ${pre.expired}.`;
    $('#bt-body').innerHTML = `
      <div class="grid g4">
        <div class="card kpi green"><span class="lbl">Fill rate (demand met)</span><span class="val">${pre.fill_rate}%</span><span class="note">vs ${none.fill_rate}% with no redistribution</span></div>
        <div class="card kpi red"><span class="lbl">Shortage events avoided</span><span class="val">${pre.events_reduction}%</span><span class="note">${none.shortage_events} → ${pre.shortage_events} bank-group-days short</span></div>
        <div class="card kpi blue"><span class="lbl">Unmet demand reduced</span><span class="val">${pre.unmet_reduction}%</span><span class="note">range ${pre.unmet_reduction_min}–${pre.unmet_reduction_max}% across scenarios</span></div>
        <div class="card kpi amber"><span class="lbl">Expired units</span><span class="val">${pre.expired}</span><span class="note">vs ${none.expired} with no redistribution</span></div>
      </div>
      <div class="grid g2 section-gap">
        <div class="card"><div class="card-head"><div><h3>Cumulative unmet demand</h3><div class="hint">Units that could not be issued when requested. Lower is better.</div></div></div><div class="chart-box"><canvas id="c-bt1"></canvas></div></div>
        <div class="card"><div class="card-head"><div><h3>Unmet demand and expired units</h3><div class="hint">Total over ${b.window_days} days, average of ${b.scenarios} scenarios</div></div></div><div class="chart-box"><canvas id="c-bt2"></canvas></div></div>
      </div>
      <div class="card section-gap"><div class="card-head"><div><h3>Policy comparison</h3><div class="hint">${esc(L.label)} · ${fmt(b.total_demand)} units demanded</div></div></div><div class="table-wrap"><table><thead><tr><th>Policy</th><th class="right">Fill rate</th><th class="right">Range</th><th class="right">Unmet units</th><th class="right">Shortage events</th><th class="right">Expired</th><th class="right">Transfers</th><th class="right">Units moved</th></tr></thead><tbody>
        ${R.map(r => `<tr><td><b>${esc(r.label)}</b></td><td class="right">${r.fill_rate}%</td><td class="right muted">${r.fill_min}–${r.fill_max}%</td><td class="right">${r.unmet}</td><td class="right">${r.shortage_events}</td><td class="right">${r.expired}</td><td class="right">${r.transfers}</td><td class="right">${r.units_moved}</td></tr>`).join('')}</tbody></table></div>
        <p class="reason" style="margin-top:12px"><b>What this means.</b> Moving stock between banks lifted the fill rate from ${none.fill_rate}% to ${pre.fill_rate}%.${expNote} ${mlNote}</p></div>`;
    const cols = [COLORS.slate, COLORS.amber, COLORS.red];
    mkChart('c-bt1', { type: 'line', data: { labels: b.dates.map(mmdd), datasets: R.map((r, i) => ({ label: r.label, data: r.cum_unmet, borderColor: cols[i], tension: .25, pointRadius: 0, borderWidth: 2.5 })) },
      options: { interaction: { mode: 'index', intersect: false }, scales: { y: { beginAtZero: true, grid: { color: '#f1f2f5' } }, x: { grid: { display: false }, ticks: { maxTicksLimit: 8 } } }, plugins: { legend: { position: 'bottom' } } } });
    mkChart('c-bt2', { type: 'bar', data: { labels: R.map(r => r.label.split(' (')[0]), datasets: [
      { label: 'Unmet demand (units)', data: R.map(r => r.unmet), backgroundColor: COLORS.red, borderRadius: 5 },
      { label: 'Expired units', data: R.map(r => r.expired), backgroundColor: COLORS.amber, borderRadius: 5 }] },
      options: { scales: { y: { beginAtZero: true, grid: { color: '#f1f2f5' } }, x: { grid: { display: false } } }, plugins: { legend: { position: 'bottom' } } } });
  };
  $('#lvl').onchange = draw; draw();
  $('#up').onclick = async () => {
    const f = $('#csv').files[0];
    if (!f) { toast('Choose a CSV file first', true); return; }
    const fd = new FormData(); fd.append('file', f);
    $('#up').disabled = true; $('#up-status').textContent = 'Validating and retraining the model… about 10 seconds.';
    try {
      const r = await fetch('/api/data/import', { method: 'POST', body: fd });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || 'Import failed');
      toast(`Imported ${fmt(d.rows)} rows (${d.days} days) · ${d.best_model}, ${d.improvement_pct}% better than baseline`);
      refreshGlobals(); route();
    } catch (e) { $('#up-status').textContent = ''; $('#up-status').innerHTML = `<span style="color:var(--crit);font-weight:600">${esc(e.message)}</span>`; $('#up').disabled = false; }
  };
  $('#reset').onclick = async () => { $('#up-status').textContent = 'Restoring demo data and retraining…'; try { await post('/api/data/reset'); toast('Demo data restored'); refreshGlobals(); route(); } catch (e) { toast(e.message, true); } };
};

// ------------------------------------------------------------------ live tracking (GPS)
views.tracking = async () => {
  view.innerHTML = `<div class="map-grid"><div class="card"><div id="map"></div></div><div class="card"><div class="card-head"><div><h3>Deliveries</h3><div class="hint">Simulated at 60× speed so you can watch a trip finish.</div></div></div><div class="tlist" id="tlist"></div></div></div>`;
  map = L.map('map', { zoomControl: true });
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: '© OpenStreetMap contributors', maxZoom: 18 }).addTo(map);
  const inv = await api('/api/inventory');
  const pts = inv.banks.map(b => [b.lat, b.lng]);
  inv.banks.forEach(b => L.circleMarker([b.lat, b.lng], { radius: 9, color: '#fff', weight: 3, fillColor: '#c81e1e', fillOpacity: 1 }).addTo(map).bindTooltip(b.name, { direction: 'top' }));
  map.fitBounds(pts, { padding: [40, 40] });
  const layer = L.layerGroup().addTo(map);
  const draw = async () => {
    const { transfers } = await api('/api/transfers');
    layer.clearLayers();
    transfers.filter(t => t.status === 'in_transit').forEach(t => {
      L.polyline([[t.from.lat, t.from.lng], [t.to.lat, t.to.lng]], { color: '#c81e1e', weight: 3, dashArray: '6 8', opacity: .7 }).addTo(layer);
      L.marker([t.lat, t.lng], { icon: L.divIcon({ className: '', html: `<div class="vehicle">${t.mode === 'Drone' ? '🚁' : '🚚'}</div>`, iconSize: [26, 26], iconAnchor: [13, 13] }) }).addTo(layer)
        .bindTooltip(`#${t.id} · ${t.units}×${t.group} · ${Math.round(t.progress * 100)}%`);
    });
    $('#tlist').innerHTML = transfers.length ? transfers.map(t => `<div class="titem"><div class="row"><span><span class="chip">${t.group}</span> ${t.units} units · #${t.id}</span>${badge(t.status)}</div>
      <div class="muted" style="margin-bottom:8px">${esc(t.from.name)} → ${esc(t.to.name)}</div><div class="progress"><i style="width:${t.progress * 100}%"></i></div>
      <div class="meta"><span>${t.mode === 'Drone' ? '🚁' : '🚚'} <b>${esc(t.mode)}</b></span><span><b>${t.distance} km</b></span><span>${t.status === 'delivered' ? 'Delivered' : `<b>${t.remaining_min} min</b> left`}</span></div></div>`).join('')
      : '<div class="empty">No deliveries yet.<br><a href="#/redistribution" style="color:var(--brand);font-weight:600">Approve a transfer</a> to see it move on the map.</div>';
  };
  await draw();
  timers.push(setInterval(() => draw().catch(() => {}), 2000));
  setTimeout(() => map && map.invalidateSize(), 200);
};

// ------------------------------------------------------------------ cold chain (IoT + edge + 5G/LoRaWAN)
views.coldchain = async () => {
  view.innerHTML = `<div id="cc-banner"></div><div class="card"><div class="card-head"><div><h3>Storage temperature · live</h3><div class="hint">Safe range for red cells is 2–6 °C. Updates every 3 seconds.</div></div>
    <div class="toolbar" style="margin:0"><button class="btn" id="net-btn">Simulate network outage</button></div></div><div class="chart-box"><canvas id="c-temp"></canvas></div></div>
    <div class="grid g3 section-gap" id="cc-cards"></div>
    <div class="card section-gap"><h3>How this works</h3><p class="muted" style="margin-top:6px">Each bank has a temperature/humidity <b>IoT sensor</b> connected by <b>5G</b> (urban banks) or <b>LoRaWAN</b> (remote, low-power). An <b>edge node</b> at the bank checks every reading locally, so an alarm fires instantly even with no internet. During an outage readings are buffered at the edge and synchronised to the cloud database when the link returns. Every alarm is also written to the blockchain ledger.</p></div>`;
  let online = true;
  $('#net-btn').onclick = async () => { await post('/api/iot/network', { online: !online }); toast(online ? 'Network cut - edge nodes are buffering locally' : 'Network restored - syncing buffered readings'); poll(); };
  const palette = ['#c81e1e', '#2563eb', '#15803d', '#d97706', '#7c3aed'];
  const poll = async () => {
    const d = await api('/api/iot/live');
    online = d.network.online;
    $('#net-btn').textContent = online ? 'Simulate network outage' : 'Restore network';
    $('#net-btn').className = 'btn ' + (online ? '' : 'primary');
    const bad = d.banks.filter(b => b.status === 'ALARM');
    $('#cc-banner').innerHTML = (!online ? `<div class="banner warn">⚠ Cloud link is DOWN. The edge nodes keep monitoring locally and have buffered <b>${d.network.buffered}</b> readings.</div>` : '')
      + (bad.length ? `<div class="banner bad">🚨 Cold-chain alarm: ${bad.map(b => esc(b.city)).join(', ')} outside 2–6 °C. Raised at the edge node.</div>`
        : `<div class="banner ok">✓ All storage units are within the safe range · ${fmt(d.network.synced)} readings synced from edge buffers so far</div>`);
    $('#cc-cards').innerHTML = d.banks.map(b => `<div class="card bankcard"><div class="card-head"><div><h3>${esc(b.name)}</h3><div class="hint">${esc(b.city)}</div></div>${badge(b.status)}</div>
      <div class="temp ${b.status === 'ALARM' ? 'bad' : ''}">${b.temp.toFixed(1)}<small> °C</small></div>
      <div class="stats"><div><small>Link</small><b>${esc(b.link)}</b></div><div><small>Latency</small><b>${b.latency} ms</b></div><div><small>Signal</small><b>${b.rssi} dBm</b></div></div>
      <div class="stats" style="grid-template-columns:1fr 1fr"><div><small>Humidity</small><b>${b.humidity}%</b></div><div><small>Edge node</small><b>${online ? 'Online' : 'Buffering'}</b></div></div>
      <button class="btn small danger" style="margin-top:12px" data-b="${b.bank_id}">Simulate fridge failure</button></div>`).join('');
    document.querySelectorAll('#cc-cards button').forEach(btn => btn.onclick = async () => { await post('/api/iot/fault', { bank_id: btn.dataset.b }); toast('Compressor failure injected'); poll(); });
    const n = Math.max(...Object.values(d.history).map(h => h.length));
    const ds = d.banks.map((b, i) => ({ label: b.city, data: d.history[b.bank_id], borderColor: palette[i % 5], tension: .35, pointRadius: 0, borderWidth: 2 }));
    ds.push({ label: 'Safe limit', data: Array(n).fill(6), borderColor: '#9ca3af', borderDash: [5, 5], pointRadius: 0, borderWidth: 1 });
    ds.push({ label: 'Safe limit ', data: Array(n).fill(2), borderColor: '#9ca3af', borderDash: [5, 5], pointRadius: 0, borderWidth: 1 });
    const chart = mkChart('c-temp', { type: 'line', data: { labels: Array.from({ length: n }, (_, i) => i - n + 1), datasets: ds },
      options: { animation: false, scales: { y: { min: 0, max: 10, title: { display: true, text: '°C' }, grid: { color: '#f1f2f5' } }, x: { display: false } }, plugins: { legend: { position: 'bottom', labels: { filter: i => i.text !== 'Safe limit ' } } } } });
  };
  await poll();
  timers.push(setInterval(() => poll().catch(() => {}), 3000));
};

// ------------------------------------------------------------------ traceability (RFID + blockchain)
views.traceability = async () => {
  view.innerHTML = `<div class="grid g-main"><div class="card"><div class="card-head"><div><h3>RFID scanner</h3><div class="hint">Enter or scan a bag tag to see its complete blockchain trail from donor to patient.</div></div></div>
      <div class="toolbar"><input type="text" id="tag" placeholder="RFID-00001" style="width:220px"><button class="btn primary" id="scan">Scan bag</button><button class="btn" id="issue" disabled>Issue to patient</button></div><div id="trace"><div class="empty">Scan a tag to see its history.</div></div></div>
    <div class="card"><div class="card-head"><div><h3>Ledger integrity</h3><div class="hint">Each block stores the SHA-256 hash of the one before it.</div></div></div><div id="verify"></div>
      <div class="toolbar" style="margin-top:14px"><button class="btn danger" id="tamper">Tamper with a record</button><button class="btn" id="restore">Restore</button></div>
      <p class="muted">Demo: edit an old block without recomputing its hash. Verification pinpoints the broken block, which is why a blockchain suits blood traceability.</p></div></div>
    <div class="card section-gap"><div class="card-head"><div><h3>Latest ledger blocks</h3></div></div><div id="ledger"></div></div>`;
  const showVerify = async () => {
    const v = await api('/api/ledger/verify');
    $('#verify').innerHTML = `<div class="banner ${v.valid ? 'ok' : 'bad'}" style="margin:0">${v.valid ? `✓ Chain valid · ${v.length} blocks` : `✕ Tampering detected at block #${v.broken_at}`}</div>`;
    const rows = await api('/api/ledger?limit=12');
    $('#ledger').innerHTML = `<div class="table-wrap"><table><thead><tr><th>#</th><th>Time</th><th>Event</th><th>Data</th><th>Previous hash</th><th>Hash</th></tr></thead><tbody>${rows.map(r => `<tr><td>${r.idx}</td><td>${r.ts.replace('T', ' ')}</td><td><span class="badge ${v.valid || r.idx < v.broken_at ? 'in_transit' : 'critical'}">${esc(r.event)}</span></td><td class="mono"><span class="hash" style="max-width:280px" title="${esc(r.data)}">${esc(r.data)}</span></td><td class="mono"><span class="hash">${r.prev_hash.slice(0, 16)}…</span></td><td class="mono"><span class="hash">${r.hash.slice(0, 16)}…</span></td></tr>`).join('')}</tbody></table></div>`;
    refreshGlobals();
  };
  let current = null;
  const scan = async () => {
    const tag = $('#tag').value.trim().toUpperCase();
    if (!tag) return;
    try {
      const r = await api('/api/rfid/' + encodeURIComponent(tag));
      current = r.bag;
      $('#issue').disabled = r.bag.status !== 'available';
      $('#trace').innerHTML = `<div class="meta" style="margin:0 0 16px"><span>Tag <b class="mono">${r.bag.tag}</b></span><span>Group <span class="chip">${r.bag.blood_group}</span></span><span>Location <b>${esc(r.bag.bank)}</b></span><span>Expires <b>${r.bag.expiry_date}</b></span><span>${badge(r.bag.status)}</span></div>
        ${r.trail.length ? `<div class="timeline">${r.trail.map(t => `<div class="tl"><b>${esc(t.event.replace(/_/g, ' '))}</b><small>${t.ts.replace('T', ' ')} · block #${t.idx} · <span class="mono">${t.hash.slice(0, 12)}…</span></small></div>`).join('')}</div>` : '<div class="empty">No blockchain events recorded for this bag yet. Trace a bag from the first 12 tags (RFID-00001…00012), a newly recorded donation, or one that was transferred.</div>'}`;
    } catch (e) { current = null; $('#issue').disabled = true; $('#trace').innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
  };
  $('#scan').onclick = scan; $('#tag').onkeydown = e => { if (e.key === 'Enter') scan(); };
  $('#issue').onclick = async () => { try { await post(`/api/bags/${current.tag}/issue`, { patient: 'demo-' + Date.now() }); toast('Unit issued · recorded on blockchain'); scan(); showVerify(); } catch (e) { toast(e.message, true); } };
  $('#tamper').onclick = async () => { const r = await post('/api/ledger/tamper'); toast(`Block #${r.tampered} was edited`, true); showVerify(); };
  $('#restore').onclick = async () => { await post('/api/ledger/restore'); toast('Ledger restored'); showVerify(); };
  const pre = sessionStorage.getItem('tag') || 'RFID-00001'; sessionStorage.removeItem('tag');
  $('#tag').value = pre; scan(); showVerify();
};

// ------------------------------------------------------------------ donors
views.donors = async () => {
  const [donors, inv] = await Promise.all([api('/api/donors'), api('/api/inventory')]);
  const ok = donors.filter(d => d.eligible).length;
  view.innerHTML = `<div class="grid g3"><div class="card kpi blue"><span class="lbl">Registered donors</span><span class="val">${donors.length}</span></div><div class="card kpi green"><span class="lbl">Eligible today</span><span class="val">${ok}</span><span class="note">90+ days since last donation</span></div><div class="card kpi amber"><span class="lbl">Need to wait</span><span class="val">${donors.length - ok}</span></div></div>
    <div class="card section-gap"><div class="card-head"><div><h3>Donor registry</h3><div class="hint">Phone numbers are masked. Recording a donation creates an RFID-tagged bag and three blockchain records.</div></div></div>
    <div class="table-wrap"><table><thead><tr><th>Donor</th><th>Group</th><th>Phone</th><th>Last donation</th><th>Eligibility</th><th>Record donation at</th></tr></thead><tbody>
    ${donors.map(d => `<tr><td><b>${esc(d.name)}</b></td><td><span class="chip">${d.blood_group}</span></td><td class="mono">${esc(d.phone)}</td><td>${d.last_donation}</td><td>${d.eligible ? '<span class="badge ok">Eligible</span>' : '<span class="badge low">Wait</span>'}</td>
      <td>${d.eligible ? `<select data-s="${d.id}" style="padding:5px 8px">${inv.banks.map(b => `<option value="${b.id}" ${b.id === d.bank_id ? 'selected' : ''}>${esc(b.city)}</option>`).join('')}</select> <button class="btn small primary" data-d="${d.id}">Record</button>` : '<span class="muted">—</span>'}</td></tr>`).join('')}
    </tbody></table></div></div>`;
  document.querySelectorAll('[data-d]').forEach(btn => btn.onclick = async () => {
    try {
      const r = await post('/api/donations', { donor_id: +btn.dataset.d, bank_id: +document.querySelector(`[data-s="${btn.dataset.d}"]`).value });
      toast(`Donation recorded · ${r.group} unit ${r.tag} stored at ${r.bank}`);
      route();
    } catch (e) { toast(e.message, true); }
  });
};

// ------------------------------------------------------------------ chatbot
views.assistant = async () => {
  view.innerHTML = `<div class="card chat"><div class="msgs" id="msgs"></div><div class="chips" id="chips"></div>
    <div class="chat-in"><input type="text" id="q" placeholder="Ask about stock, shortages, donating, expiring units, transfers…" autocomplete="off"><button class="btn primary" id="send">Send</button></div></div>`;
  const msgs = $('#msgs');
  const add = (who, text) => { const d = document.createElement('div'); d.className = 'msg ' + who; d.innerHTML = md(text); msgs.appendChild(d); msgs.scrollTop = msgs.scrollHeight; };
  const chips = list => { $('#chips').innerHTML = list.map(s => `<button>${esc(s)}</button>`).join(''); document.querySelectorAll('#chips button').forEach(b => b.onclick = () => ask(b.textContent)); };
  const ask = async text => {
    if (!text.trim()) return;
    add('me', text); $('#q').value = '';
    try { const r = await post('/api/chat', { message: text }); add('bot', r.reply); chips(r.suggestions); }
    catch (e) { add('bot', 'Sorry, something went wrong: ' + e.message); }
  };
  $('#send').onclick = () => ask($('#q').value); $('#q').onkeydown = e => { if (e.key === 'Enter') ask($('#q').value); };
  add('bot', "Hi! I'm the **BloodLink assistant**. Ask me about stock levels, predicted shortages, where to donate, expiring units, live transfers, cold-chain health or ledger integrity.");
  chips(["How many units of O- are available?", "Which blood groups are at risk of shortage?", "Where can I donate B+ blood?", "Am I eligible to donate?", "Which units are expiring soon?", "Track active transfers", "Is the cold chain healthy?", "Can O- give to B+?", "Is the blockchain ledger valid?"]);
  $('#q').focus();
};

// ------------------------------------------------------------------ architecture
views.architecture = async () => {
  const T = [
    ['IoT sensors', 'Things', 'real*', 'Temperature and humidity sensors monitor every storage unit', 'coldchain'],
    ['RFID tagging', 'Things', 'real*', 'Every blood bag has a unique RFID tag from donation to transfusion', 'traceability'],
    ['5G / LoRaWAN', 'Things ↔ Data', 'sim', 'Urban banks use 5G; remote banks use long-range low-power LoRaWAN', 'coldchain'],
    ['Edge computing', 'Data', 'real*', 'Alarms are decided locally at the bank and buffered during outages', 'coldchain'],
    ['Cloud + big data', 'Data', 'real', 'Flask API and database aggregate 180 days of demand across all banks', 'dashboard'],
    ['Machine learning', 'Data → Process', 'real', 'Gradient Boosting forecasts demand, triggers warnings, and is backtested on held-out days; accepts real CSV data', 'impact'],
    ['Blockchain', 'Process', 'real', 'Hash-chained ledger gives tamper-evident donor-to-patient traceability', 'traceability'],
    ['GPS + drones', 'Things + Process', 'sim', 'Smart redistribution dispatches drones or vans and tracks them live', 'tracking'],
    ['Mobile / web app', 'People', 'real', 'Responsive dashboard for hospitals, bank staff and donors', 'dashboard'],
    ['AI chatbot (NLP)', 'People', 'real', 'Intent-based assistant answers from live data and the ML forecast', 'assistant'],
  ];
  const P = [['People', '#c81e1e', 'Donors, doctors, bank staff and patients: served by the app and chatbot'], ['Process', '#2563eb', 'Shortage warning, smart redistribution and delivery workflows'], ['Data', '#15803d', 'Demand history, sensor streams, RFID events and the ML forecast'], ['Things', '#d97706', 'Sensors, RFID tags, storage units, drones and vans']];
  view.innerHTML = `<div class="grid g4">${P.map(p => `<div class="card pillar" style="--c:${p[1]}"><h4><span class="dot" style="background:${p[1]};box-shadow:none"></span>${p[0]}</h4><p>${p[2]}</p></div>`).join('')}</div>
    <div class="card section-gap"><div class="card-head"><div><h3>Data flow</h3><div class="hint">From a sensor reading to a life-saving decision</div></div></div><div class="flow">
      <div class="step"><b>Things</b><small>Sensors · RFID · GPS</small></div><span class="sep">→</span><div class="step"><b>Connectivity</b><small>5G · LoRaWAN</small></div><span class="sep">→</span><div class="step"><b>Edge</b><small>Local alarm · buffer</small></div><span class="sep">→</span><div class="step"><b>Cloud + Data</b><small>Database · analytics</small></div><span class="sep">→</span><div class="step"><b>AI / ML</b><small>Forecast · optimise</small></div><span class="sep">→</span><div class="step"><b>Blockchain</b><small>Trust · traceability</small></div><span class="sep">→</span><div class="step"><b>People</b><small>App · chatbot</small></div></div></div>
    <div class="card section-gap"><div class="card-head"><div><h3>The 10 technologies</h3><div class="hint">* hardware is simulated in this demo; the decision logic is the real design. Cross-cutting: security headers, parameterised SQL, hashed patient references.</div></div></div>
    <div class="table-wrap"><table><thead><tr><th>#</th><th>Technology</th><th>IoE component</th><th>Role in AI-BloodLink</th><th>Implementation</th><th></th></tr></thead><tbody>
    ${T.map((t, i) => `<tr><td>${i + 1}</td><td><b>${t[0]}</b></td><td>${t[1]}</td><td>${t[3]}</td><td>${t[2] === 'sim' ? '<span class="badge sim">Simulated</span>' : t[2] === 'real*' ? '<span class="badge sim">Logic real · hardware simulated</span>' : '<span class="badge real">Working</span>'}</td><td><a class="btn small" href="#/${t[4]}">Open demo</a></td></tr>`).join('')}
    </tbody></table></div></div>`;
};

// ------------------------------------------------------------------ boot
buildNav();
tickClock(); setInterval(tickClock, 30000);
window.addEventListener('hashchange', route);
refreshGlobals(); setInterval(refreshGlobals, 15000);
route();
