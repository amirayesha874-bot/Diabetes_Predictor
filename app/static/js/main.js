/* Shared front-end behaviour: menu, flashes, confirm dialogs, BMI calc, charts, session timeout */
(function () {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

  const mb = $('.menu-btn');
  if (mb) mb.addEventListener('click', () => $('nav.links').classList.toggle('open'));

  $$('.flash button').forEach(b => b.addEventListener('click', () => b.parentElement.remove()));
  $$('form[data-confirm]').forEach(f => f.addEventListener('submit', e => {
    if (!confirm(f.dataset.confirm)) e.preventDefault();
  }));

  // Live BMI calculation on the assessment form
  const h = $('#height'), w = $('#weight'), out = $('#bmi_out');
  function bmi() {
    const hv = parseFloat(h.value), wv = parseFloat(w.value);
    if (hv > 0 && wv > 0) {
      const b = wv / Math.pow(hv / 100, 2);
      const cat = b < 18.5 ? 'Underweight' : b < 25 ? 'Healthy' : b < 30 ? 'Overweight' : 'Obese';
      out.textContent = b.toFixed(1) + ' kg/m² (' + cat + ')';
    } else out.textContent = '—';
  }
  if (h && w && out) { h.addEventListener('input', bmi); w.addEventListener('input', bmi); bmi(); }

  // Gender -> pregnancies field
  const g = $('#gender'), preg = $('#pregnancies_wrap');
  if (g && preg) { const t = () => preg.style.display = g.value === 'Male' ? 'none' : ''; g.addEventListener('change', t); t(); }

  // Inactivity timeout mirror of the server's 15-minute session timeout
  const mins = parseInt(document.body.dataset.timeout || '0', 10);
  if (mins && document.body.dataset.auth === '1') {
    let timer;
    const reset = () => { clearTimeout(timer); timer = setTimeout(() => { location.href = '/logout'; }, mins * 60 * 1000); };
    ['click', 'keydown', 'mousemove', 'touchstart'].forEach(ev => document.addEventListener(ev, reset, { passive: true }));
    reset();
  }

  // Sortable client-side tables
  $$('table[data-sortable] th').forEach((th, idx) => th.addEventListener('click', () => {
    const tb = th.closest('table').tBodies[0], rows = Array.from(tb.rows);
    const dir = th.dataset.dir = th.dataset.dir === 'asc' ? 'desc' : 'asc';
    rows.sort((a, b) => {
      const x = a.cells[idx].innerText.trim(), y = b.cells[idx].innerText.trim();
      const nx = parseFloat(x), ny = parseFloat(y);
      const c = (!isNaN(nx) && !isNaN(ny)) ? nx - ny : x.localeCompare(y);
      return dir === 'asc' ? c : -c;
    });
    rows.forEach(r => tb.appendChild(r));
  }));

  /* ---------- chart helpers (Chart.js is vendored locally) ---------- */
  const COLORS = { 'Low Risk': '#16a34a', 'Moderate Risk': '#d97706', 'High Risk': '#dc2626' };
  window.drawTrend = function (canvasId, url, emptyId) {
    const el = document.getElementById(canvasId); if (!el || !window.Chart) return;
    let chart;
    async function load(range) {
      const r = await fetch(url + (url.includes('?') ? '&' : '?') + 'range=' + range, { credentials: 'same-origin' });
      const d = await r.json();
      const empty = emptyId && document.getElementById(emptyId);
      if (empty) empty.style.display = d.scores.length ? 'none' : 'block';
      el.style.display = d.scores.length ? 'block' : 'none';
      if (chart) chart.destroy();
      chart = new Chart(el, {
        type: 'line',
        data: { labels: d.labels, datasets: [{ label: 'Risk score (%)', data: d.scores, borderColor: '#0f766e',
          backgroundColor: 'rgba(15,118,110,.12)', fill: true, tension: .3, pointRadius: 5,
          pointBackgroundColor: d.levels.map(l => COLORS[l]) }] },
        options: { responsive: true, maintainAspectRatio: false,
          scales: { y: { min: 0, max: 100, title: { display: true, text: 'Risk score (%)' } } },
          plugins: { tooltip: { callbacks: { afterLabel: c => d.levels[c.dataIndex] } } } }
      });
    }
    $$('.tabs button[data-range]').forEach(b => b.addEventListener('click', () => {
      $$('.tabs button[data-range]').forEach(x => x.classList.remove('on')); b.classList.add('on'); load(b.dataset.range);
    }));
    load('all');
  };
  window.drawBar = function (id, labels, datasets, opts) {
    const el = document.getElementById(id); if (!el || !window.Chart) return;
    return new Chart(el, { type: (opts && opts.type) || 'bar', data: { labels, datasets },
      options: Object.assign({ responsive: true, maintainAspectRatio: false }, opts && opts.options) });
  };
})();
