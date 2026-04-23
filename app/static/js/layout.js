/* ============================================================
 * layout.js — shared sidebar + topbar + toast for every page
 *
 * Each page file includes an <div id="sidebar-slot"></div> and
 * <div id="topbar-slot"></div>. We inject the shell on DOMContentLoaded
 * so the same markup only lives in one place.
 *
 * Pages tell us what to highlight via window.CALLSARA_PAGE = "dashboard"
 * (or "calls", "hot", etc) set in the page's own <script>.
 * ============================================================ */
(function () {
  const { api } = window.CallSaraAPI;

  const PAGE_TITLES = {
    dashboard: 'Dashboard',
    calls:     'All Calls',
    hot:       'Hot Leads',
    dialer:    'New Call',
    settings:  'Settings',
    voice:     'AI Voice',
  };

  const NAV = [
    { group: 'Main',
      items: [
        { page: 'dashboard', label: 'Dashboard', icon: svgGrid() },
        { page: 'calls',     label: 'All Calls', icon: svgPhone() },
        { page: 'hot',       label: 'Hot Leads', icon: svgBolt() },
      ],
    },
    { group: 'Tools',
      items: [
        { page: 'dialer',   label: 'New Call',  icon: svgCircle() },
        { page: 'settings', label: 'Settings',  icon: svgGear() },
        { page: 'voice',    label: 'AI Voice',  icon: svgMic() },
      ],
    },
  ];

  function svgGrid() { return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>'; }
  function svgPhone() { return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 16.92v3a2 2 0 01-2.18 2 19.79 19.79 0 01-8.63-3.07A19.5 19.5 0 013.07 9.81 19.79 19.79 0 01.02 5.13 2 2 0 012 3h3a2 2 0 012 1.72c.127.96.361 1.903.7 2.81a2 2 0 01-.45 2.11L6.09 9.91a16 16 0 006 6l1.27-1.27a2 2 0 012.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0122 16.92z"/></svg>'; }
  function svgBolt() { return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>'; }
  function svgCircle() { return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="16"/><line x1="8" y1="12" x2="16" y2="12"/></svg>'; }
  function svgGear() { return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.07 4.93a10 10 0 010 14.14M4.93 4.93a10 10 0 000 14.14"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2"/></svg>'; }
  function svgMic() { return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3z"/><path d="M19 10v2a7 7 0 01-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>'; }

  function renderSidebar(activePage) {
    const slot = document.getElementById('sidebar-slot');
    if (!slot) return;
    const groups = NAV.map((g) => {
      const items = g.items.map((it) => `
        <a class="nav-item ${it.page === activePage ? 'active' : ''}"
           href="/${it.page}">
          ${it.icon}${it.label}
        </a>
      `).join('');
      return `<div class="nav-group">${g.group}</div>${items}`;
    }).join('');

    slot.outerHTML = `
      <nav class="sidebar">
        <div class="brand">
          <div class="brand-name">CallSara</div>
          <div class="brand-tag" id="agencyTag">AI Real Estate Dialer</div>
        </div>
        <div class="nav">${groups}</div>
        <div class="sidebar-foot">
          <div class="live-dot">
            <div class="dot" id="liveDot"></div>
            <span id="liveLabel">Checking...</span>
          </div>
          <div style="margin-top:10px;font-size:11px;color:var(--t3);font-family:'DM Mono',monospace;display:flex;align-items:center;justify-content:space-between">
            <span id="userEmail">—</span>
            <a href="#" id="signoutBtn" style="color:var(--t3);text-decoration:none">sign out</a>
          </div>
        </div>
      </nav>`;
  }

  function renderTopbar(activePage) {
    const slot = document.getElementById('topbar-slot');
    if (!slot) return;
    slot.outerHTML = `
      <div class="topbar">
        <div class="page-title" id="pgTitle">${PAGE_TITLES[activePage] || ''}</div>
        <div class="topbar-r">
          <div class="agency-pill" id="agencyPill">—</div>
          <a class="btn btn-gold btn-sm" href="/dialer">📞 New Call</a>
        </div>
      </div>`;
  }

  async function loadStatus() {
    try {
      const d = await api('/api/status');
      const agencyPill = document.getElementById('agencyPill');
      const agencyTag = document.getElementById('agencyTag');
      if (agencyPill) agencyPill.textContent = d.agency || '';
      if (agencyTag) agencyTag.textContent = d.agency || 'AI Dialer';
      document.title = (d.agency || 'CallSara') + ' — ' +
                       (PAGE_TITLES[window.CALLSARA_PAGE] || 'AI Dialer');

      const dot = document.getElementById('liveDot');
      const lbl = document.getElementById('liveLabel');
      if (d.twilio_configured) {
        if (dot) dot.className = 'dot on';
        if (lbl) lbl.textContent = d.from_number || 'Live';
      } else {
        if (dot) dot.className = 'dot err';
        if (lbl) lbl.textContent = 'Token Missing';
      }
      const uemail = document.getElementById('userEmail');
      if (uemail && d.user) uemail.textContent = d.user.email || '';

      window.CallSaraLayout.agentName = d.agent || 'Sara';
      window.CallSaraLayout.fromNumber = d.from_number || '';
      window.CallSaraLayout.agencyName = d.agency || '';
      const elFrom = document.getElementById('fromNum');
      if (elFrom) elFrom.textContent = d.from_number || '—';
      // Let pages react to status arrival.
      document.dispatchEvent(new CustomEvent('callsara:status', { detail: d }));
    } catch (_) {
      const lbl = document.getElementById('liveLabel');
      if (lbl) lbl.textContent = 'Server Error';
    }
  }

  async function signout(e) {
    e && e.preventDefault();
    try { await api('/api/auth/signout', 'POST', {}); } catch (_) { /* ignore */ }
    location.assign('/signin');
  }

  // Toast — global, reused by every page.
  let _tt;
  function toast(msg, type = 'info') {
    const el = document.getElementById('toast');
    if (!el) return;
    const ic = { ok: '✓', err: '✗', info: 'ℹ' };
    el.innerHTML = `<span>${ic[type] || 'ℹ'}</span><span>${msg}</span>`;
    el.className = 'show ' + (type === 'err' ? 'err' : type === 'ok' ? 'ok' : 'info');
    clearTimeout(_tt);
    _tt = setTimeout(() => { el.className = ''; }, 4000);
  }

  // Utility: safe HTML escape
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#x27;');
  }

  function fmtDur(sec) {
    if (!sec || sec <= 0) return '—';
    const m = Math.floor(sec / 60), s = sec % 60;
    return m > 0 ? `${m}m ${s}s` : `${s}s`;
  }

  function fmtDate(iso) {
    if (!iso) return '—';
    try {
      const d = new Date(iso);
      return d.toLocaleDateString('en-AE', { day: '2-digit', month: 'short' }) + ' ' +
        d.toLocaleTimeString('en-AE', { hour: '2-digit', minute: '2-digit', hour12: false });
    } catch (_) { return iso; }
  }

  function badgeClass(s) {
    return ({
      ringing: 'b-ring', answered: 'b-ans', completed: 'b-done',
      'no-answer': 'b-fail', busy: 'b-fail', failed: 'b-fail',
    })[s] || 'b-done';
  }

  window.CallSaraLayout = {
    agentName: 'Sara',
    fromNumber: '',
    agencyName: '',
    toast, esc, fmtDur, fmtDate, badgeClass,
  };

  document.addEventListener('DOMContentLoaded', () => {
    const page = window.CALLSARA_PAGE || '';
    renderSidebar(page);
    renderTopbar(page);
    loadStatus();
    document.getElementById('signoutBtn')?.addEventListener('click', signout);
  });
})();
