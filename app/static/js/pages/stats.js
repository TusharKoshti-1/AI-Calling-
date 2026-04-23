/* ============================================================
 * stats.js — shared stats loader + hot-leads widget
 * Used by dashboard + hot pages.
 * ============================================================ */
(function () {
  const { api } = window.CallSaraAPI;
  const L = window.CallSaraLayout;

  function setText(id, v) {
    const el = document.getElementById(id);
    if (el) el.textContent = v;
  }

  async function loadStats() {
    try {
      const s = await api('/api/stats');
      setText('st-total',    s.total_calls || 0);
      setText('st-ans',      s.answered || 0);
      setText('st-hot',      s.hot_leads || 0);
      setText('st-fail',     s.no_answer || 0);
      setText('st-today',    (s.calls_today || 0) + ' today');
      setText('st-hot-today',(s.hot_leads_today || 0) + ' today');
      setText('st-ring',     (s.ringing || 0) + ' ringing');
      const avg = s.avg_duration_sec || 0;
      setText('st-avg', avg > 0 ? 'avg ' + L.fmtDur(avg) : 'avg —');

      // Hot leads page triple-stat card set
      setText('hl-count', s.hot_leads || 0);
      setText('hl-today', s.hot_leads_today || 0);
      setText('hl-avg',   avg > 0 ? L.fmtDur(avg) : '—');
    } catch (_) { /* keep last values visible */ }
  }

  async function loadHotWidget() {
    const el = document.getElementById('hotWidget');
    if (!el) return;
    try {
      const d = await api('/api/calls?limit=8&offset=0&status=all&hot_only=true&search=');
      const hot = d.calls || [];
      if (!hot.length) {
        el.innerHTML = '<div class="tbl-empty" style="padding:22px 18px"><p>No hot leads yet</p></div>';
        return;
      }
      el.innerHTML = hot.map((c) => `
        <div class="hot-item" onclick='CallSaraModal.open(${JSON.stringify({
          sid: c.sid, phone: c.phone || '', hot: true,
          dur: c.duration_sec || 0, started: c.started_at || '',
          rec: c.recording_url || '',
        }).replace(/'/g, "\\'")})'>
          <div>
            <div class="hot-phone">🔥 ${L.esc(c.phone || c.sid)}</div>
            <div class="hot-meta">${L.fmtDate(c.started_at)} · ${L.fmtDur(c.duration_sec || 0)}</div>
          </div>
          <span class="badge ${L.badgeClass(c.status)}">${c.status}</span>
        </div>`).join('');
    } catch (_) { /* ignore */ }
  }

  async function loadHotTable() {
    const tb = document.getElementById('hot-tbl');
    if (!tb) return;
    try {
      const d = await api('/api/calls?limit=100&offset=0&status=all&hot_only=true&search=');
      const calls = d.calls || [];
      if (!calls.length) {
        tb.innerHTML = '<div class="tbl-empty"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="36" height="36"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg><p>No hot leads yet</p></div>';
        return;
      }
      tb.innerHTML = calls.map(window.CallSaraTable.rowHtml).join('');
    } catch (_) { /* ignore */ }
  }

  window.CallSaraStats = { loadStats, loadHotWidget, loadHotTable };
})();
