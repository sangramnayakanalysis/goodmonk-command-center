/* ===================================================================
   app.js — GoodMonk D2C Command Center dashboard logic.

   Reads the static JSON files produced by the Python pipeline and renders
   the 8 dashboard sections + Chart.js trends. No backend calls.

   DATA SOURCE
   -----------
   When the dashboard is hosted on InfinityFree, the data lives in your GitHub
   repo (the Actions workflows commit it). Point DATA_BASE at the raw GitHub URL:

     const DATA_BASE = 'https://raw.githubusercontent.com/<user>/<repo>/main/dashboard/data/';

   Left as './data/' it reads the bundled files next to index.html (works for
   local preview and if you upload the data folder to InfinityFree too).
   =================================================================== */

const DATA_BASE = './data/';
const REFRESH_MS = 60_000;            // re-pull JSON every minute

Auth.requireAuth();                   // gate the page

// ---- helpers --------------------------------------------------------------
const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };

async function getJSON(name) {
  try {
    const r = await fetch(DATA_BASE + name + '?_=' + Date.now());
    if (!r.ok) throw new Error(r.status);
    return await r.json();
  } catch (e) {
    console.warn('Could not load', name, e.message);
    return null;
  }
}

function fmtTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' });
}
function sevClass(s) { return (s || '').toLowerCase(); }

// ---- 01 executive + topbar ------------------------------------------------
function renderExec(d) {
  if (!d) return;
  const s = d.summary;
  $('#execSite').textContent = d.site || '';
  $('#updated').textContent = 'Updated ' + fmtTime(d.generated_at);

  // status pill + heartbeat
  const pill = $('#statusPill');
  const map = { OPERATIONAL: ['ok', 'All systems operational'],
                DEGRADED: ['degraded', 'Degraded performance'],
                DOWN: ['down', 'Site down'] };
  const [cls, label] = map[s.site_status] || ['ok', s.site_status];
  pill.className = 'status-pill ' + cls;
  $('#statusText').textContent = label;
  drawHeartbeat(cls);

  const cards = [
    { label: 'Website Status', value: s.site_status, cls: cls === 'ok' ? 'ok' : (cls === 'down' ? 'crit' : 'warn'), sm: true },
    { label: 'Health Score', value: s.health_score, unit: '/100', cls: s.health_score >= 80 ? 'ok' : (s.health_score >= 50 ? 'warn' : 'crit') },
    { label: 'Pages Monitored', value: s.pages_monitored, cls: '' },
    { label: 'Healthy Pages', value: s.healthy, cls: 'ok' },
    { label: 'Warning Pages', value: s.warning, cls: s.warning ? 'warn' : '' },
    { label: 'Critical Pages', value: s.critical, cls: s.critical ? 'crit' : '' },
    { label: 'Avg Load Time', value: s.avg_load_time, unit: 's', cls: s.avg_load_time < 4 ? 'ok' : (s.avg_load_time <= 10 ? 'warn' : 'crit') },
  ];
  const grid = $('#kpiGrid'); grid.innerHTML = '';
  cards.forEach(c => {
    const k = el('div', 'kpi ' + c.cls);
    k.innerHTML = `<div class="label">${c.label}</div>
      <div class="value ${c.sm ? 'sm' : ''}">${c.value}<span class="unit">${c.unit || ''}</span></div>`;
    grid.appendChild(k);
  });

  // health table
  const body = $('#healthBody'); body.innerHTML = '';
  (d.health_table || []).forEach(h => {
    const tr = el('tr');
    tr.innerHTML = `<td>${h.page}</td>
      <td><span class="badge ${sevClass(h.severity)}">${h.severity}</span></td>
      <td class="mono">${h.http}</td>
      <td class="mono">${h.load_time ? h.load_time.toFixed(2) + 's' : '—'}</td>
      <td class="mono" style="color:var(--muted)">${fmtTime(h.last_checked)}</td>`;
    body.appendChild(tr);
  });
  if (!(d.health_table || []).length) body.innerHTML = '<tr><td colspan="5" class="empty">No health checks yet.</td></tr>';

  // break grid
  const bg = $('#breakGrid'); bg.innerHTML = '';
  (d.break_grid || []).forEach(b => {
    const state = (b.status || '—').toUpperCase();
    const cls = state === 'PASS' ? 'pass' : (state === 'WARNING' ? 'warning' : (state === 'FAILED' ? 'failed' : ''));
    const cell = el('div', 'break-cell ' + cls);
    cell.innerHTML = `<div class="name">${b.label}</div><div class="state">${state}</div>`;
    bg.appendChild(cell);
  });
}

// ---- heartbeat signature --------------------------------------------------
function drawHeartbeat(cls) {
  const W = 600, H = 34, mid = H / 2;
  let pts = [], x = 0;
  while (x < W) {
    pts.push(`${x},${mid}`);
    x += 38 + Math.random() * 26;
    if (x >= W) break;
    // a spike whose violence scales with severity
    const amp = cls === 'down' ? 14 : (cls === 'degraded' ? 9 : 6);
    pts.push(`${x},${mid}`, `${x + 4},${mid - amp}`, `${x + 8},${mid + amp}`, `${x + 12},${mid}`);
    x += 16;
  }
  pts.push(`${W},${mid}`);
  $('#heartbeatLine').setAttribute('points', pts.join(' '));
  $('#heartbeat').className = 'heartbeat ' + (cls === 'ok' ? '' : cls);
}

// ---- 04 performance -------------------------------------------------------
function metricCell(label, val, good) {
  const cls = good === null ? '' : (good ? 'good' : 'bad');
  return `<div class="metric ${cls}"><div class="m-label">${label}</div><div class="m-val">${val}</div></div>`;
}
function renderPerf(d) {
  const card = $('#perfCard');
  if (!d || !(d.pages || []).length) { card.innerHTML = '<div class="empty">No performance data yet — the hourly PageSpeed run will populate this.</div>'; return; }
  const b = d.benchmarks || {};
  card.innerHTML = '';
  d.pages.forEach(p => {
    const row = el('div', 'perf-page');
    const scoreCls = (sc) => sc >= 90 ? 'badge healthy' : (sc >= 50 ? 'badge warning' : 'badge critical');
    const lcp = p.lcp_ms || 0, cls_ = p.cls || 0, inp = p.inp_ms || 0, ttfb = p.ttfb_ms || 0;
    let issues = '';
    (p.top_issues || []).forEach(i => issues += `<div class="issue">${i}</div>`);
    row.innerHTML = `
      <div class="perf-head">
        <span class="pname">${p.name}</span>
        <span class="${scoreCls(p.mobile_score)}">📱 ${p.mobile_score}</span>
        <span class="${scoreCls(p.desktop_score)}">🖥 ${p.desktop_score}</span>
      </div>
      <div class="metrics">
        ${metricCell('LCP', (lcp/1000).toFixed(2)+'s', lcp <= b.lcp_ms)}
        ${metricCell('CLS', cls_.toFixed(3), cls_ <= b.cls)}
        ${metricCell('INP', Math.round(inp)+'ms', inp <= b.inp_ms)}
        ${metricCell('TTFB', (ttfb/1000).toFixed(2)+'s', ttfb <= b.ttfb_ms)}
        ${p.total_kb != null ? metricCell('Weight', Math.round(p.total_kb)+'KB', !p.weight_flag) : ''}
      </div>
      ${issues ? `<div class="issues">${issues}</div>` : ''}`;
    card.appendChild(row);
  });
}

// ---- 05 alerts ------------------------------------------------------------
function alertItem(a) {
  return `<div class="summary-line">
    <span class="k"><span class="badge ${sevClass(a.severity)}">${a.severity}</span> ${a.issue}</span>
    <span class="v mono" style="color:var(--muted);font-weight:500">${fmtTime(a.time)}</span></div>`;
}
function renderAlerts(d) {
  if (!d) return;
  const kpis = [
    { label: 'Open Alerts', value: (d.open || []).length, cls: (d.open || []).length ? 'warn' : 'ok' },
    { label: 'Critical Alerts', value: d.critical_count || 0, cls: d.critical_count ? 'crit' : 'ok' },
    { label: 'Resolved', value: (d.resolved || []).length, cls: 'ok' },
  ];
  const g = $('#alertKpis'); g.innerHTML = '';
  kpis.forEach(c => { const k = el('div', 'kpi ' + c.cls); k.innerHTML = `<div class="label">${c.label}</div><div class="value">${c.value}</div>`; g.appendChild(k); });

  $('#openAlerts').innerHTML = (d.open || []).length ? d.open.map(alertItem).join('') : '<div class="empty">No open alerts. All clear.</div>';
  $('#resolvedAlerts').innerHTML = (d.resolved || []).length ? d.resolved.map(alertItem).join('') : '<div class="empty">Nothing resolved recently.</div>';
}

// ---- 06 trends ------------------------------------------------------------
let charts = {};
const GRID = '#28362e', TICK = '#8aa094';
function baseOpts(extra = {}) {
  return Object.assign({
    responsive: true, maintainAspectRatio: false,
    plugins: { legend: { labels: { color: TICK, boxWidth: 10, font: { size: 11 } } },
               title: { display: true, color: '#e9f0ea', font: { size: 13, family: 'Space Grotesk' } } },
    scales: { x: { ticks: { color: TICK, maxTicksLimit: 7, font: { size: 10 } }, grid: { color: GRID } },
              y: { ticks: { color: TICK, font: { size: 10 } }, grid: { color: GRID }, beginAtZero: true } },
  }, extra);
}
function makeChart(id, cfg) { if (charts[id]) charts[id].destroy(); charts[id] = new Chart($('#' + id), cfg); }

function renderTrends(d) {
  if (!d) return;
  const h = d.health || {}, lt = d.load_time || {}, pf = d.performance || {}, al = d.alerts || {};
  makeChart('healthTrend', { type: 'line', data: { labels: h.labels || [], datasets: [
      { label: 'Healthy', data: h.healthy || [], borderColor: '#46d18b', backgroundColor: '#46d18b22', tension: .3, fill: true },
      { label: 'Warning', data: h.warning || [], borderColor: '#f5b13d', tension: .3 },
      { label: 'Critical', data: h.critical || [], borderColor: '#ff5d52', tension: .3 } ] },
    options: opt('Website Health Trend') });
  makeChart('loadTrend', { type: 'line', data: { labels: lt.labels || [], datasets: [
      { label: 'Avg load (s)', data: lt.avg || [], borderColor: '#5aa9e6', backgroundColor: '#5aa9e622', tension: .3, fill: true } ] },
    options: opt('Load Time Trend') });
  makeChart('perfTrend', { type: 'line', data: { labels: pf.labels || [], datasets: [
      { label: 'Mobile PageSpeed', data: pf.mobile_avg || [], borderColor: '#46d18b', backgroundColor: '#46d18b22', tension: .3, fill: true } ] },
    options: opt('Performance Trend', { y: { min: 0, max: 100, ticks: { color: TICK }, grid: { color: GRID } } }) });
  makeChart('alertTrend', { type: 'bar', data: { labels: al.labels || [], datasets: [
      { label: 'Alerts/day', data: al.count || [], backgroundColor: '#ff5d5288', borderColor: '#ff5d52', borderWidth: 1 } ] },
    options: opt('Alert Trend') });
}
function opt(title, scaleOverride) {
  const o = baseOpts(); o.plugins.title.text = title;
  if (scaleOverride) Object.assign(o.scales, scaleOverride);
  return o;
}

// ---- 07 weekly ------------------------------------------------------------
function renderWeekly(d) {
  const c = $('#weeklyCard');
  if (!d) { c.innerHTML = '<div class="empty">No weekly data yet.</div>'; return; }
  const line = (k, v) => `<div class="summary-line"><span class="k">${k}</span><span class="v">${v}</span></div>`;
  c.innerHTML =
    line('Best page (PageSpeed)', `${d.best_page.page} · ${Math.round(d.best_page.value)}`) +
    line('Worst page (PageSpeed)', `${d.worst_page.page} · ${Math.round(d.worst_page.value)}`) +
    line('Fastest page (load)', `${d.fastest_page.page} · ${d.fastest_page.value}s`) +
    line('Slowest page (load)', `${d.slowest_page.page} · ${d.slowest_page.value}s`) +
    line('Critical incidents', `<span class="badge ${d.incidents ? 'critical' : 'healthy'}">${d.incidents}</span>`);
}

// ---- 08 monthly -----------------------------------------------------------
function renderMonthly(d) {
  const body = $('#monthlyBody'); body.innerHTML = '';
  const list = (d && d.diagnostics) || [];
  if (!list.length) { body.innerHTML = '<tr><td colspan="4" class="empty">No diagnostics yet — populated from PageSpeed opportunities.</td></tr>'; return; }
  list.forEach(x => {
    const cls = x.impact === 'High' ? 'critical' : (x.impact === 'Medium' ? 'warning' : 'muted');
    const tr = el('tr');
    tr.innerHTML = `<td class="diag-page">${x.page}</td><td class="diag-issue">${x.issue}</td>
      <td><span class="badge ${cls}">${x.impact}</span></td><td class="diag-fix">${x.fix}</td>`;
    body.appendChild(tr);
  });
}

// ---- nav, refresh ---------------------------------------------------------
function setupNav() {
  const links = document.querySelectorAll('.nav-link');
  links.forEach(l => l.addEventListener('click', () => {
    const t = document.getElementById(l.dataset.target);
    if (t) t.scrollIntoView({ behavior: 'smooth', block: 'start' });
    $('#sidebar').classList.remove('open');
  }));
  // scroll-spy
  const obs = new IntersectionObserver((entries) => {
    entries.forEach(e => { if (e.isIntersecting) {
      links.forEach(l => l.classList.toggle('active', l.dataset.target === e.target.id));
    }});
  }, { rootMargin: '-30% 0px -60% 0px' });
  document.querySelectorAll('.section').forEach(s => obs.observe(s));

  $('#logoutBtn').addEventListener('click', Auth.logout);
  $('#menuToggle').addEventListener('click', () => $('#sidebar').classList.toggle('open'));
}

async function refresh() {
  const [dash, perf, alerts, trends, weekly, monthly] = await Promise.all([
    getJSON('dashboard.json'), getJSON('performance.json'), getJSON('alerts.json'),
    getJSON('trends.json'), getJSON('weekly.json'), getJSON('monthly.json'),
  ]);
  renderExec(dash); renderPerf(perf); renderAlerts(alerts);
  renderTrends(trends); renderWeekly(weekly); renderMonthly(monthly);
}

setupNav();
refresh();
setInterval(refresh, REFRESH_MS);
