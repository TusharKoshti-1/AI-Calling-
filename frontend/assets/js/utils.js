/**
 * frontend/assets/js/utils.js
 * Shared utility functions used across all pages.
 */

// ── Formatting ────────────────────────────────────────────────
function fmtDuration(sec) {
  if (!sec || sec <= 0) return '—';
  const m = Math.floor(sec / 60), s = sec % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

function fmtDate(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    return (
      d.toLocaleDateString('en-AE', { day: '2-digit', month: 'short' }) +
      ' ' +
      d.toLocaleTimeString('en-AE', { hour: '2-digit', minute: '2-digit', hour12: false })
    );
  } catch { return iso; }
}

function fmtPhone(phone) {
  return phone || '—';
}

// ── Badge HTML ────────────────────────────────────────────────
function badgeHtml(status) {
  const cls = {
    ringing:   'badge-ringing',
    answered:  'badge-answered',
    completed: 'badge-completed',
    'no-answer':'badge-failed',
    busy:      'badge-failed',
    failed:    'badge-failed',
  }[status] || 'badge-completed';
  return `<span class="badge ${cls}">${status}</span>`;
}

// ── Escape HTML ───────────────────────────────────────────────
function esc(s) {
  return String(s || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#x27;');
}

// ── Hash (for diff rendering) ─────────────────────────────────
function hashStr(s) {
  let h = 0;
  for (let i = 0; i < s.length; i++) {
    h = ((h << 5) - h) + s.charCodeAt(i);
    h |= 0;
  }
  return h;
}

// ── Toast ─────────────────────────────────────────────────────
let _toastTimer;
function toast(msg, type = 'info') {
  const el = document.getElementById('toast');
  if (!el) return;
  const icons = { ok: '✓', err: '✗', info: 'ℹ' };
  el.innerHTML = `<span>${icons[type] || 'ℹ'}</span><span>${esc(msg)}</span>`;
  el.className = `show ${type === 'err' ? 'err' : type === 'ok' ? 'ok' : 'info'}`;
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => { el.className = ''; }, 4000);
}

// ── DOM helpers ───────────────────────────────────────────────
function setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}

function setHtml(id, val) {
  const el = document.getElementById(id);
  if (el) el.innerHTML = val;
}

// ── Sidebar toggle ────────────────────────────────────────────
function toggleSidebar() {
  const sidebar = document.querySelector('.sidebar');
  const wrap    = document.querySelector('.page-wrap');
  const topbar  = document.querySelector('.topbar');
  if (!sidebar) return;
  sidebar.classList.toggle('open');   // mobile
  sidebar.classList.toggle('hidden'); // desktop
  if (wrap)   wrap.classList.toggle('full');
  if (topbar) topbar.classList.toggle('full');
}

// ── Set active nav link ───────────────────────────────────────
function setActiveNav(page) {
  document.querySelectorAll('.nav-link').forEach(el => {
    el.classList.toggle('active', el.dataset.page === page);
  });
}

// ── Load status into sidebar ──────────────────────────────────
async function loadSidebarStatus() {
  try {
    const s = await StatusAPI.get();
    setText('sidebarAgency', s.agency_name || '');
    setText('topbarAgency',  s.agency_name || '');
    setText('sidebarVersion', `v${s.version || '1.0'}`);
    document.title = (s.agency_name || 'CallSara') + ' — AI Dialer';

    const dot = document.getElementById('sidebarDot');
    const lbl = document.getElementById('sidebarStatus');
    if (dot && lbl) {
      if (s.twilio_configured && s.db_connected) {
        dot.className = 'status-dot live';
        lbl.textContent = s.from_number || 'Live';
      } else if (!s.twilio_configured) {
        dot.className = 'status-dot error';
        lbl.textContent = 'Token Missing';
      } else {
        dot.className = 'status-dot error';
        lbl.textContent = 'DB Error';
      }
    }
  } catch (e) {
    setText('sidebarStatus', 'Offline');
  }
}

// ── Sleep ─────────────────────────────────────────────────────
const sleep = ms => new Promise(r => setTimeout(r, ms));

// ── Modal ─────────────────────────────────────────────────────
function openModalOverlay(id) {
  document.getElementById(id)?.classList.add('open');
}
function closeModalOverlay(id, event) {
  if (!event || event.target === document.getElementById(id)) {
    document.getElementById(id)?.classList.remove('open');
  }
}
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    document.querySelectorAll('.modal-overlay.open').forEach(m => m.classList.remove('open'));
  }
});
